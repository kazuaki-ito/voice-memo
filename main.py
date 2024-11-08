from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import requests
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, AudioMessage, TextSendMessage

load_dotenv()

app = FastAPI()

# LINE API設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Whisper API設定
WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_API_KEY = os.getenv("WHISPER_API_KEY")

# ChatGPT API設定
CHATGPT_API_URL = "https://api.openai.com/v1/chat/completions"
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")

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
def handle_audio_message(event):
    # 音声メッセージの取得
    message_content = line_bot_api.get_message_content(event.message.id)
    audio_path = f"/tmp/{event.message.id}.m4a"
    with open(audio_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    # Whisper APIで文字起こし
    with open(audio_path, "rb") as f:
        headers = {
            "Authorization": f"Bearer {WHISPER_API_KEY}",
        }
        files = {
            "file": (audio_path, f, "audio/m4a")
        }
        data = {
            "model": "whisper-1"
        }
        response = requests.post(WHISPER_API_URL, headers=headers, files=files, data=data)
        if response.status_code != 200:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="文字起こしに失敗しました。")
            )
            return
        transcribed_text = response.json().get("text", "")

    # ChatGPTで誤字脱字補正
    headers = {
        "Authorization": f"Bearer {CHATGPT_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "user", "content": f"以下のテキストの誤字脱字を修正してください：'{transcribed_text}'"}
        ]
    }
    response = requests.post(CHATGPT_API_URL, headers=headers, json=data)
    if response.status_code != 200:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="テキストの補正に失敗しました。")
        )
        return
    corrected_text = response.json()["choices"][0]["message"]["content"]

    # ユーザーに返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=corrected_text)
    )
