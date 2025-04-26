# 📁 main.py — Jarvis backend (FastAPI + WebSocket + TTS proxy)
# Совместимо с openai-python ≥ 1.0.0

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AsyncOpenAI
from starlette.responses import StreamingResponse
import os, io

# ────────────────── OpenAI async-клиент ──────────────────
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ────────────────── FastAPI app ──────────────────
app = FastAPI()

# CORS (можно сузить allow_origins до своего домена)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── WebSocket: GPT-4o стриминг ───────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = f"{ws.client.host}:{ws.client.port}"
    print(f"🔌 WS connected {cid}", flush=True)

    try:
        while True:
            text = await ws.receive_text()
            print(f"📥 {cid}: {text!r}", flush=True)

            stream = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": text}],
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    await ws.send_text(delta)

            await ws.send_text("[DONE]")
            print(f"✅ answer sent to {cid}", flush=True)

    except Exception as e:
        # клиент закрыл соединение или произошла ошибка
        print(f"❌ WS error {cid}: {e}", flush=True)
        return

# ─────────── TTS proxy: текст → mp3 ───────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "nova"   # допустимые: nova | shimmer | echo | onyx | fable

@app.post("/tts")
async def tts(req: TTSRequest):
    try:
        speech = await client.audio.speech.create(
            model="tts-1-hd",
            voice=req.voice,
            input=req.text,
            response_format="mp3",
        )
        audio_bytes = await speech.read()
        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")
    except Exception as e:
        print("❌ TTS error:", e, flush=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─────────── Health-check ───────────
@app.get("/")
async def root():
    return {"status": "Jarvis server running 🚀"}
