from fastapi import FastAPI, WebSocket, Body
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
import os, io, asyncio

"""Jarvis WebSocket‑сервер + proxy TTS endpoint (OpenAI ≥ 1.0.0)"""

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app = FastAPI()

# ===================== WebSocket GPT =====================
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = f"{ws.client.host}:{ws.client.port}"
    print(f"🔌 WS connected {cid}", flush=True)

    try:
        while True:
            text = await ws.receive_text()
            print(f"📥 {cid} → {text!r}", flush=True)

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
        print(f"❌ WS error {cid}: {e}", flush=True)
        await ws.close()

# ===================== TTS proxy =====================
@app.post("/tts")
async def tts(text: str = Body(...), voice: str = Body("nova")):
    """Браузер обращается сюда, сервер делает запрос к OpenAI TTS
    и отдаёт mp3‑поток с правильными CORS‑заголовками."""
    speech = await client.audio.speech.create(
        model="tts-1-hd",
        voice=voice,
        input=text,
        format="mp3",
    )
    audio_bytes = await speech.read()
    return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")


@app.get("/")
async def root():
    return {"status": "Jarvis server running 🚀"}
