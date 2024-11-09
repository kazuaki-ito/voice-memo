from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import os
import requests
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, AudioMessage, TextSendMessage
from sqlalchemy.orm import Session
from database import Base, engine, get_db, SessionLocal
from models import User, Recording
from contextlib import contextmanager
from fastapi.staticfiles import StaticFiles
import secrets
import logging
from symlink_utils import create_symlinks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


Base.metadata.create_all(bind=engine)

load_dotenv()

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    create_symlinks()

# LINE API設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Whisper API設定
WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_API_KEY = os.getenv("WHISPER_API_KEY")

# ChatGPT API設定
CHATGPT_API_URL = "https://api.openai.com/v1/chat/completions"
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")

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
        audio_path = f"recordings/{event.message.id}.m4a"
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
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="文字起こしに失敗しました。"))
                return
            transcribed_text = response.json().get("text", "")

        # ChatGPTで誤字脱字補正
        headers = {"Authorization": f"Bearer {CHATGPT_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": f"以下のテキストの誤字脱字を修正してください：'{transcribed_text}'"}]
        }
        response = requests.post(CHATGPT_API_URL, headers=headers, json=data)
        if response.status_code != 200:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="テキストの補正に失敗しました。"))
            return
        corrected_text = response.json()["choices"][0]["message"]["content"]

        # データベースに録音と文字起こしを保存
        recording = Recording(user_id=user.id, filename=audio_path, transcription=corrected_text)
        db.add(recording)
        db.commit()

        # ユーザーに返信
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=corrected_text))

# recordings ディレクトリを静的ファイルとして公開
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

# Jinja2 テンプレートの設定
templates = Jinja2Templates(directory="templates")

@app.get("/list/")
async def show_recordings(request: Request, db: Session = Depends(get_db), username: str = Depends(authenticate)):
    # データベースからユーザー名、ファイル名、文字起こしを取得
    results = (
        db.query(User.line_user_id, User.display_name, Recording.filename, Recording.transcription)
        .join(Recording, User.id == Recording.user_id)
        .all()
    )
    # テンプレートにデータを渡して表示
    return templates.TemplateResponse("recordings.html", {"request": request, "recordings": results})