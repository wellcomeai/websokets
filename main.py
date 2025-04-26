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

# Port for internal proxy calls and uvicorn
port = int(os.getenv("PORT", "10000"))

# OpenAI async client
client = AsyncOpenAI(api_key=API_KEY)

# FastAPI app
app = FastAPI(title="Jarvis Voice Assistant API")

# CORS middleware (allow all origins)
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
    audio: str  # base64-encoded audio data

class MessageRequest(BaseModel):
    message: str

class ChatItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatItem]
    system_prompt: Optional[str] = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    )
    temperature: Optional[float] = 0.7

# Добавлено: запрос создания Realtime-сессии
class RealtimeSessionRequest(BaseModel):
    model: str = "gpt-4o"
    modalities: List[str] = ["audio", "text"]
    instructions: str = (
        "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    )
    voice: str = "alloy"  # alloy, ash, ballad, coral, echo, sage, shimmer, verse
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[dict] = None
    turn_detection: Optional[dict] = None
    input_audio_noise_reduction: Optional[dict] = None
    temperature: float = 0.8
    max_response_output_tokens: Union[int, str] = "inf"

# ─────────── Endpoints ───────────
@app.post("/transcribe")
async def transcribe_audio(request: AudioTranscriptionRequest):
    try:
        # decode base64
        audio_data = base64.b64decode(request.audio)
        if len(audio_data) < 100:
            return {"transcript": "Аудио слишком короткое или пустое"}
        # write to temp file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_filename = temp_file.name
        # whisper transcription
        with open(temp_filename, 'rb') as audio_file:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1", file=audio_file, language="ru"
            )
        os.unlink(temp_filename)
        transcript = transcription.text or "Не удалось распознать речь"
        return {"transcript": transcript}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    print(f"🔌 WebSocket client connected: {client_id}")
    ping_task = asyncio.create_task(send_ping_periodically(websocket, client_id))
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            try:
                message = json.loads(data)
                # handle structured messages
                if isinstance(message, dict) and "type" in message:
                    msg_type = message["type"]
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue
                    elif msg_type == "message":
                        content = message.get("content", "")
                        if content:
                            asyncio.create_task(handle_message(content, websocket))
                        continue
                if "messages" in message:
                    asyncio.create_task(handle_chat(message, websocket))
                else:
                    asyncio.create_task(handle_message(data, websocket))
            except json.JSONDecodeError:
                asyncio.create_task(handle_message(data, websocket))
            except Exception as e:
                traceback.print_exc()
                await websocket.send_json({"type": "error", "content": str(e)})
    except WebSocketDisconnect:
        print(f"🔌 WebSocket client disconnected: {client_id}")
    finally:
        ping_task.cancel()
        print(f"🔌 Connection closed for {client_id}")

# Added endpoint: create_session
@app.post("/create_session")
async def create_session(req: RealtimeSessionRequest):
    try:
        print(f"🔄 Creating session with voice: {req.voice}")
        session_id = str(uuid.uuid4())
        client_secret = secrets.token_hex(16)
        expires_at = int(time.time()) + 60
        session_data = {
            "sessionId": session_id,
            "clientSecret": client_secret,
            "expiresAt": expires_at,
            "voice": req.voice
        }
        print(f"✅ Session created: {session_id}")
        return session_data
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# Added endpoint: WebSocket proxy
@app.websocket("/ws_proxy/{token}")
async def websocket_proxy(websocket: WebSocket, token: str):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    print(f"🔌 Proxy WS client connected: {client_id}")
    ping_task = asyncio.create_task(send_ping_periodically(websocket, client_id))
    active_connection = True
    is_processing = False

    async def process_audio(audio_base64, event_id="auto"):
        nonlocal is_processing, active_connection
        if is_processing or not active_connection:
            return
        is_processing = True
        try:
            await websocket.send_text(json.dumps({"type": "input_audio_buffer.speech_started", "event_id": f"proxy_speech_event_{event_id}", "audio_start_ms": 0, "item_id": f"msg_proxy_{event_id}"}))
            await asyncio.sleep(0.2)
            await websocket.send_text(json.dumps({"type": "input_audio_buffer.speech_stopped", "event_id": f"proxy_speech_end_event_{event_id}", "audio_end_ms": 1000, "item_id": f"msg_proxy_{event_id}"}))
            transcript = ""
            if not audio_base64:
                transcript = "Не удалось получить аудио"
            else:
                try:
                    async with httpx.AsyncClient() as client_http:
                        url = f"http://127.0.0.1:{port}/transcribe"
                        resp = await client_http.post(url, json={"audio": audio_base64}, timeout=10.0)
                    if resp.status_code == 200:
                        transcript = resp.json().get("transcript", "")
                    else:
                        transcript = "Ошибка транскрипции аудио"
                except Exception:
                    transcript = "Ошибка при транскрибировании"
            if not transcript.strip():
                transcript = "Не удалось распознать речь, пожалуйста, повторите"
            await websocket.send_text(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "event_id": f"proxy_transcription_event_{event_id}", "item_id": f"msg_proxy_{event_id}", "content_index": 0, "transcript": transcript}))
            await websocket.send_text(json.dumps({"type": "response.created", "event_id": f"proxy_response_event_{event_id}", "response": {"id": f"resp_proxy_{event_id}", "status": "in_progress"}}))
            response_text = ""
            try:
                async with httpx.AsyncClient() as client_http:
                    response = await client_http.stream(
                        "POST",
                        "https://api.openai.com/v1/chat/completions",
                        headers={'Authorization': f'Bearer {API_KEY}'},
                        json={
                            "model": "gpt-4o",
                            "messages": [
                                {"role": "system", "content": req.instructions},
                                {"role": "user", "content": transcript}
                            ],
                            "temperature": 0.7,
                            "stream": True
                        }
                    )
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            chunk = json.loads(line[6:])
                            if 'choices' in chunk:
                                delta = chunk['choices'][0]['delta'].get('content', '')
                                if delta:
                                    response_text += delta
                                    await websocket.send_text(json.dumps({
                                        "type": "response.text.delta",
                                        "event_id": f"proxy_text_delta_{event_id}_{len(response_text)}",
                                        "response_id": f"resp_proxy_{event_id}",
                                        "item_id": f"msg_assistant_proxy_{event_id}",
                                        "output_index": 0,
                                        "content_index": 0,
                                        "delta": delta
                                    }))
            except Exception:
                response_text = "Извините, я не смог обработать ваш запрос. Пожалуйста, повторите."
            await websocket.send_text(json.dumps({"type": "response.done", "event_id": f"proxy_response_done_{event_id}", "response": {"id": f"resp_proxy_{event_id}", "status": "completed", "output": [{"id": f"msg_assistant_proxy_{event_id}", "type": "message", "role": "assistant", "content": [{"type": "text", "text": response_text}]}]}}))
        finally:
            is_processing = False

    audio_buffer = []
    msg_counter = 0
    try:
        # send initial session.created event
        await websocket.send_text(json.dumps({
            "type": "session.created",
            "event_id": "proxy_init_event",
            "session": {
                "id": "proxy_session",
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "model": "gpt-4o",
                "instructions": "Ты русскоязычный голосовой помощник по имени Jarvis.",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16"
            }
        }))
        while active_connection:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
            except asyncio.TimeoutError:
                continue
            msg_counter += 1
            message = json.loads(data)
            t = message.get("type")
            if t == "input_audio_buffer.append":
                audio = message.get("audio", "")
                if audio:
                    audio_buffer.append(audio)
                if len(audio) > 10000 and not is_processing:
                    combined = ''.join(audio_buffer)
                    audio_buffer.clear()
                    asyncio.create_task(process_audio(combined, f"audio_{msg_counter}"))
            elif t == "input_audio_buffer.commit":
                combined = ''.join(audio_buffer)
                audio_buffer.clear()
                if combined:
                    asyncio.create_task(process_audio(combined, f"commit_{msg_counter}"))
            elif t == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "event_id": f"pong_{msg_counter}", "ping_id": message.get("event_id")}))
            elif t == "session.update":
                await websocket.send_text(json.dumps({"type": "session.updated", "event_id": f"proxy_session_updated_{msg_counter}", "session": message.get("session", {})}))
    except WebSocketDisconnect:
        active_connection = False
    finally:
        ping_task.cancel()
        active_connection = False
        print(f"🔌 Proxy connection closed for {client_id}")

# Health-check / root
@app.get("/")
async def root():
    return {"status": "Jarvis Voice Assistant running 🚀", "version": "5.1"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
