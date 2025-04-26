# 📁 main.py — Jarvis Voice Assistant Backend

import os
import asyncio
import json
import base64
import io
import tempfile
import traceback
import uuid
import secrets
import time
from typing import Optional, List, Union

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
# импорт для статики
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from openai import AsyncOpenAI

# ────────────────── Configuration ──────────────────
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY not found in environment variables")

port = int(os.getenv("PORT", "10000"))
client = AsyncOpenAI(api_key=API_KEY)
app = FastAPI(title="Jarvis Voice Assistant API")

# монтируем папку public как статику (отдаём index.html при запросе /)
app.mount("/", StaticFiles(directory="public", html=True), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── Data Models ───────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"

class AudioTranscriptionRequest(BaseModel):
    audio: str

class ChatItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatItem]
    system_prompt: Optional[str] = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    )
    temperature: Optional[float] = 0.7

# Realtime session request
class RealtimeSessionRequest(BaseModel):
    model: str = "gpt-4o-realtime-preview"
    modalities: List[str] = ["audio", "text"]
    instructions: str = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке."
    )
    voice: str = "alloy"
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[dict] = None
    turn_detection: Optional[dict] = None
    input_audio_noise_reduction: Optional[dict] = None
    temperature: float = 0.8
    max_response_output_tokens: Union[int, str] = "inf"

# ─────────── Endpoints ───────────
@app.post("/create_session")
async def create_session(req: RealtimeSessionRequest):
    session_id = str(uuid.uuid4())
    client_secret = secrets.token_hex(16)
    expires_at = int(time.time()) + 60
    return {
        "sessionId": session_id,
        "clientSecret": client_secret,
        "expiresAt": expires_at,
        "voice": req.voice
    }

@app.post("/transcribe")
async def transcribe_audio(request: AudioTranscriptionRequest):
    try:
        audio_data = base64.b64decode(request.audio)
        if len(audio_data) < 100:
            return {"transcript": "Аудио слишком короткое или пустое"}
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(audio_data)
            fname = f.name
        with open(fname, 'rb') as audi:
            tr = await client.audio.transcriptions.create(
                model="whisper-1", file=audi, language="ru"
            )
        os.unlink(fname)
        text = tr.text or "Не удалось распознать речь"
        return {"transcript": text}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ping = asyncio.create_task(ping_loop(ws))
    try:
        while True:
            data = await asyncio.wait_for(ws.receive_text(), timeout=60)
            msg = json.loads(data)
            # handle ping, messages, etc.
    except WebSocketDisconnect:
        pass
    finally:
        ping.cancel()

async def ping_loop(ws: WebSocket):
    try:
        while True:
            await asyncio.sleep(25)
            await ws.send_json({"type":"ping"})
    except asyncio.CancelledError:
        pass

@app.get("/")
```python
# 📁 main.py — Jarvis Voice Assistant Backend

import os
import asyncio
import json
import base64
import io
import tempfile
import traceback
import uuid
import secrets
import time
from typing import Optional, List, Union

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
from openai import AsyncOpenAI

# ────────────────── Configuration ──────────────────
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY not found in environment variables")

port = int(os.getenv("PORT", "10000"))
client = AsyncOpenAI(api_key=API_KEY)
app = FastAPI(title="Jarvis Voice Assistant API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── Data Models ───────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"

class AudioTranscriptionRequest(BaseModel):
    audio: str

class ChatItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatItem]
    system_prompt: Optional[str] = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    )
    temperature: Optional[float] = 0.7

# Realtime session request
class RealtimeSessionRequest(BaseModel):
    model: str = "gpt-4o-realtime-preview"
    modalities: List[str] = ["audio", "text"]
    instructions: str = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке."
    )
    voice: str = "alloy"
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[dict] = None
    turn_detection: Optional[dict] = None
    input_audio_noise_reduction: Optional[dict] = None
    temperature: float = 0.8
    max_response_output_tokens: Union[int, str] = "inf"

# ─────────── Endpoints ───────────
@app.post("/create_session")
async def create_session(req: RealtimeSessionRequest):
    session_id = str(uuid.uuid4())
    client_secret = secrets.token_hex(16)
    expires_at = int(time.time()) + 60
    return {
        "sessionId": session_id,
        "clientSecret": client_secret,
        "expiresAt": expires_at,
        "voice": req.voice
    }

@app.post("/transcribe")
async def transcribe_audio(request: AudioTranscriptionRequest):
    try:
        audio_data = base64.b64decode(request.audio)
        if len(audio_data) < 100:
            return {"transcript": "Аудио слишком короткое или пустое"}
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(audio_data)
            fname = f.name
        with open(fname, 'rb') as audi:
            tr = await client.audio.transcriptions.create(
                model="whisper-1", file=audi, language="ru"
            )
        os.unlink(fname)
        text = tr.text or "Не удалось распознать речь"
        return {"transcript": text}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ping = asyncio.create_task(ping_loop(ws))
    try:
        while True:
            data = await asyncio.wait_for(ws.receive_text(), timeout=60)
            msg = json.loads(data)
            # handle ping, messages, etc.
    except WebSocketDisconnect:
        pass
    finally:
        ping.cancel()

async def ping_loop(ws: WebSocket):
    try:
        while True:
            await asyncio.sleep(25)
            await ws.send_json({"type":"ping"})
    except asyncio.CancelledError:
        pass

@app.get("/")
def root():
    return {"status":"running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
