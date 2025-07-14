from fastapi import FastAPI, Request, HTTPException, Depends, Query, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import os
import re
import requests
import json
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, AudioMessage, TextSendMessage
from sqlalchemy import desc, asc
from sqlalchemy.orm import Session
from database import Base, engine, get_db, SessionLocal
from models import User, Recording
from contextlib import contextmanager
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import secrets
import logging
from typing import Optional, List


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


Base.metadata.create_all(bind=engine)

load_dotenv()

app = FastAPI()

#@app.on_event("startup")
#async def startup_event():
#    create_symlinks()

# LINE API設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Whisper API設定
WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_API_KEY = os.getenv("WHISPER_API_KEY")

# ChatGPT API設定
CHATGPT_API_URL = "https://api.openai.com/v1/chat/completions"
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")

RECORDING_DIR = os.getenv("RECORDING_DIR")

USERNAME = "carebank"
PASSWORD = "honeycome"

# データベースセッションを取得するためのコンテキストマネージャ
@contextmanager
def get_db_context():

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

security = HTTPBasic()

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, USERNAME)
    correct_password = secrets.compare_digest(credentials.password, PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return JSONResponse(content={"message": "OK"})

@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event, db: Session = Depends(get_db)):
    line_user_id = event.source.user_id

    profile = line_bot_api.get_profile(line_user_id)
    display_name = profile.display_name


    # データベースセッションを明示的に取得
    with get_db_context() as db:
        # ユーザーがデータベースに存在しなければ作成
        user = db.query(User).filter(User.line_user_id == line_user_id).first()
        if not user:
            user = User(line_user_id=line_user_id, display_name=display_name)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.display_name = display_name
        db.commit()

        # 音声メッセージの取得
        message_content = line_bot_api.get_message_content(event.message.id)
        audio_file = f"{event.message.id}.m4a"
        audio_path = f"{RECORDING_DIR}/{audio_file}"
        with open(audio_path, "wb") as f:
            for chunk in message_content.iter_content():
                f.write(chunk)

        # Whisper APIで文字起こし
        with open(audio_path, "rb") as f:
            headers = {"Authorization": f"Bearer {WHISPER_API_KEY}"}
            files = {"file": (audio_path, f, "audio/m4a")}
            data = {"model": "whisper-1"}
            response = requests.post(WHISPER_API_URL, headers=headers, files=files, data=data)
            if response.status_code != 200:
                logger.error(f"Whisper API failed: {response.status_code} - {response.text}")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="文字起こしに失敗しました。"))
                return
            transcribed_text = response.json().get("text", "")

        # ChatGPTで誤字脱字補正
        headers = {"Authorization": f"Bearer {CHATGPT_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": f"以下のテキストの誤字脱字を修正してください：'{transcribed_text}'"}]
        }
        response = requests.post(CHATGPT_API_URL, headers=headers, json=data)
        if response.status_code != 200:
            logger.error(f"Whisper API failed: {response.status_code} - {response.text}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="テキストの補正に失敗しました。"))
            return
        corrected_text = response.json()["choices"][0]["message"]["content"]

        # データベースに録音と文字起こしを保存
        recording = Recording(user_id=user.id, filename=audio_file, transcription=corrected_text)
        db.add(recording)
        db.commit()

        # ユーザーに返信
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=corrected_text))

if RECORDING_DIR:
    # ディレクトリが存在しない場合は作成
    if not os.path.exists(RECORDING_DIR):
        os.makedirs(RECORDING_DIR, exist_ok=True)
        print(f"ディレクトリ '{RECORDING_DIR}' を作成しました。")
    else:
        print(f"ディレクトリ '{RECORDING_DIR}' は既に存在します。")

# recordings ディレクトリを静的ファイルとして公開
app.mount("/recordings", StaticFiles(directory=RECORDING_DIR), name="recordings")

# 日付フォーマット用のカスタムフィルタを定義
def format_datetime(value, format="%Y-%m-%d %H:%M"):
    if isinstance(value, datetime):
        return value.strftime(format)
    return ""

# フィルタをテンプレートに追加
templates = Jinja2Templates(directory="templates")
templates.env.filters["datetime"] = format_datetime

@app.get("/list/")
async def show_recordings(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(authenticate),
    user_id: Optional[str] = Query(None)  # ← 追加
):
    query = (
        db.query(Recording.id, User.line_user_id, User.display_name, Recording.filename, Recording.transcription, Recording.recorded_at)
        .join(Recording, User.id == Recording.user_id)
    )
    
    if user_id:
        query = query.filter(User.line_user_id == user_id)

    results = query.order_by(desc(Recording.id)).all()

    # ユーザー一覧を取得してフィルタ用に表示
    users = db.query(User.line_user_id, User.display_name).distinct().all()

    return templates.TemplateResponse("recordings.html", {
        "request": request,
        "recordings": results,
        "users": users,
        "selected_user_id": user_id
    })


@app.get("/generate_facing_sheet/{recording_id}", response_class=HTMLResponse)
async def generate_facing_sheet(request: Request, recording_id: int, db: Session = Depends(get_db)):
    # 録音データを取得
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # ChatGPTにフェースシート作成依頼（フォーマットを明確に指定）
    headers = {"Authorization": f"Bearer {CHATGPT_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "gpt-4-turbo",
        "messages": [
            {
                "role": "user",
                "content": (
                    f"以下の内容に基づいて、介護のフェースシートをJSON形式で出力してください。"
                    f"以下の形式に厳密に従って出力してください。テキストではなく、必ず有効なJSONオブジェクトとして出力してください。\n\n"
                    f"次のフォーマットで出力し、長文の説明や生活の経緯は読みやすいように段落ごとに適切に改行してください：\n"
                    f"各項目は必ず含めてください。\n\n"
                    f"出力例:\n"
                    f"{{\n"
                    f'"利用者名": "○○",\n'
                    f'"年齢": "○○",\n'
                    f'"要介護度": "○○",\n'
                    f'"身障等級": "○○",\n'
                    f'"認知症": "○○",\n'
                    f'"相談内容-本人": "○○",\n'
                    f'"相談内容-ご家族": "○○",\n'
                    f'"これまでの生活の経緯": "○○"\n'
                    f"}}\n\n"
                    f"テキスト：\n{recording.transcription}"
                )
            }
        ]
    }
    response = requests.post(CHATGPT_API_URL, headers=headers, json=data)
    if response.status_code != 200:
        return HTMLResponse(content="<h1>フェースシート作成に失敗しました。</h1>", status_code=500)

    # ChatGPTの応答を辞書としてパース
    facing_sheet_content = response.json()["choices"][0]["message"]["content"]
    logger.info(facing_sheet_content)
    json_match = re.search(r'```json\n(.*)\n```', facing_sheet_content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)  # JSON部分のみを抽出
        try:
            facing_sheet_dict = json.loads(json_str)
        except json.JSONDecodeError as e:
            # JSONパースエラー時のデバッグ出力
            print("Error: JSONDecodeError - JSONのパースに失敗しました")
            return HTMLResponse(content=f"<h1>フェースシートのデータ形式が不正です。</h1><pre>{facing_sheet_content}</pre>",
                                status_code=500)
    else:
        # JSON部分が見つからなかった場合
        return HTMLResponse(content="<h1>フェースシートのデータ形式が不正です。</h1>", status_code=500)
    # テンプレートを利用してフェースシートを表示
    return templates.TemplateResponse("facing_sheet.html", {"request": request, "facing_sheet": facing_sheet_dict})

@app.get("/support_log/{recording_id}", response_class=HTMLResponse)
async def create_support_log_form(
    request: Request,
    recording_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(authenticate)
):
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    return templates.TemplateResponse("support_log_form.html", {
        "request": request,
        "recording": recording
    })

@app.post("/support_log/{recording_id}")
async def submit_support_log(
    request: Request,
    recording_id: int,
    user_name: str = Form(...),
    author_name: str = Form(...),
    db: Session = Depends(get_db),
    username: str = Depends(authenticate)
):
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # 入力データと録音情報を組み合わせてPDF生成・保存などが可能
    print(f"支援経過作成：{user_name} / {author_name}")
    print(f"録音日：{recording.recorded_at}")
    print(f"内容：{recording.transcription}")

    # 今回は画面遷移だけ
    return RedirectResponse(url="/list/", status_code=302)

@app.post("/support_log_batch", response_class=HTMLResponse)
async def generate_batch_support_log(
    request: Request,
    recording_ids: List[int] = Form(...),
    user_name: str = Form(...),
    author_name: str = Form(...),
    db: Session = Depends(get_db),
    username: str = Depends(authenticate)
):
    recordings = db.query(Recording).filter(Recording.id.in_(recording_ids)).order_by(asc(Recording.recorded_at)).all()
    # HTMLやPDFで整形して出力する処理へ
    return templates.TemplateResponse("support_log_batch.html", {
        "request": request,
        "user_name": user_name,
        "author_name": author_name,
        "recordings": recordings,
    })