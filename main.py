from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import os
import re
import requests
import json
from dotenv import load_dotenv
from sqlalchemy import desc
from sqlalchemy.orm import Session
from database import Base, engine, get_db, SessionLocal
from models import User, Recording
from contextlib import contextmanager
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import secrets
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


Base.metadata.create_all(bind=engine)

load_dotenv()

app = FastAPI()

#@app.on_event("startup")
#async def startup_event():
#    create_symlinks()

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

# 音声アップロード（Web）
@app.post("/upload")
async def upload_audio(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(RECORDING_DIR, filename)

    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    # Whisper API
    with open(filepath, "rb") as f:
        headers = {"Authorization": f"Bearer {WHISPER_API_KEY}"}
        files = {"file": (filename, f, file.content_type)}
        data = {"model": "whisper-1"}
        response = requests.post(WHISPER_API_URL, headers=headers, files=files, data=data)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="文字起こし失敗")
        transcribed_text = response.json().get("text", "")

    # ChatGPT API
    headers = {"Authorization": f"Bearer {CHATGPT_API_KEY}", "Content-Type": "application/json"}
    prompt = f"""
あなたは共感的なDNAセラピストです。
以下の音声文字起こしテキストに誤字脱字がある場合は修正し、
ユーザーが話した内容（修正済み）をそのまま一文にまとめた上で、
心理的に寄り添う応答をしてください。

出力は説明を含めず、以下のJSON形式で**コードブロックや記号なし**で返してください：

{{
  \"user_text\": \"修正済みの発話\",
  \"reply\": \"心理カウンセラーとしての優しい応答\"
}}

文字起こし：
{transcribed_text}
"""

    data = {
        "model": "gpt-4-turbo",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(CHATGPT_API_URL, headers=headers, json=data)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="補正失敗")

    # JSONとして解析（ChatGPTの出力がJSON形式であることが前提）
    content = response.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
        user_text = parsed.get("user_text", "")
        reply = parsed.get("reply", "")
    except json.JSONDecodeError:
        user_text = transcribed_text
        reply = content  # fallbackとして全文表示

    # DB保存
    user = db.query(User).filter_by(line_user_id="webuser").first()
    if not user:
        user = User(line_user_id="webuser", display_name="Web利用者")
        db.add(user)
        db.commit()
        db.refresh(user)

    recording = Recording(user_id=user.id, filename=filename, transcription=user_text + "\n" + reply)
    db.add(recording)
    db.commit()

    return JSONResponse(content={
        "user_text": user_text,
        "reply": reply
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_ui(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

@app.get("/list/")
async def show_recordings(request: Request, db: Session = Depends(get_db), username: str = Depends(authenticate)):
    # データベースからユーザー名、ファイル名、文字起こしを取得
    results = (
        db.query(Recording.id, User.line_user_id, User.display_name, Recording.filename, Recording.transcription, Recording.recorded_at)
        .join(Recording, User.id == Recording.user_id)
        .order_by(desc(Recording.id))  # ここで降順ソートを指定
        .all()
    )
    # テンプレートにデータを渡して表示
    return templates.TemplateResponse("recordings.html", {"request": request, "recordings": results})


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