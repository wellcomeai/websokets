# 📁 main.py — Jarvis Voice Assistant Backend

import os
import asyncio
import json
import base64
import traceback
import httpx
from typing import Optional, List, Union, Literal
import io

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ────────────────── Configuration ──────────────────
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY not found in environment variables")

app = FastAPI(title="Jarvis Voice Assistant API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (create public directory if it doesn't exist)
os.makedirs("public", exist_ok=True)

# ─────────── Data Models ───────────
class InputAudioTranscription(BaseModel):
    model: str = "gpt-4o-transcribe"
    language: Optional[str] = None
    prompt: str = ""

class TurnDetection(BaseModel):
    type: str = "server_vad"
    threshold: float = 0.5
    prefix_padding_ms: int = 300
    silence_duration_ms: int = 300
    create_response: bool = True

class NoiseReduction(BaseModel):
    type: str = "near_field"

class RealtimeSessionRequest(BaseModel):
    model: str = "gpt-4o-realtime-preview"
    modalities: List[str] = ["audio", "text"]
    instructions: str = "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    voice: str = "alloy"  # Поддерживаемые голоса: alloy, ash, ballad, coral, echo, sage, shimmer, verse
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[InputAudioTranscription] = None
    turn_detection: Optional[TurnDetection] = None
    input_audio_noise_reduction: Optional[NoiseReduction] = None
    temperature: float = 0.8
    max_response_output_tokens: Union[int, Literal["inf"]] = "inf"

class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"   # допустимые для TTS API: alloy, shimmer, echo, onyx, nova, fable

# Serve static index.html
@app.get("/")
async def serve_index():
    # Check if index.html exists, if not, create a simple one
    index_path = "public/index.html"
    if not os.path.exists(index_path):
        with open(index_path, "w") as f:
            f.write("""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Jarvis Voice Assistant</title>
</head>
<body>
  <h1>Jarvis Voice Assistant</h1>
  <p>API сервер работает. Для использования API обратитесь к документации.</p>
</body>
</html>""")
    return FileResponse(index_path)

# Mount static files after defining the root handler
app.mount("/static", StaticFiles(directory="public"), name="static")

# ─────────── Endpoints ───────────
@app.post("/create_session")
async def create_session(req: RealtimeSessionRequest):
    try:
        # Создаем сессию через прямой HTTP запрос к OpenAI API
        async with httpx.AsyncClient() as http_client:
            # Подготовка данных запроса
            request_data = {
                "model": req.model,
                "modalities": req.modalities,
                "instructions": req.instructions,
                "voice": req.voice,
                "input_audio_format": req.input_audio_format,
                "output_audio_format": req.output_audio_format,
                "temperature": req.temperature,
                "max_response_output_tokens": req.max_response_output_tokens
            }
            
            # Добавляем опциональные поля, если они предоставлены
            if req.input_audio_transcription:
                request_data["input_audio_transcription"] = req.input_audio_transcription.dict(exclude_none=True)
            
            if req.turn_detection:
                request_data["turn_detection"] = req.turn_detection.dict(exclude_none=True)
                
            if req.input_audio_noise_reduction:
                request_data["input_audio_noise_reduction"] = req.input_audio_noise_reduction.dict(exclude_none=True)
            
            # Отправляем запрос к API OpenAI
            response = await http_client.post(
                "https://api.openai.com/v1/realtime/sessions",
                json=request_data,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"OpenAI API error: {response.text}"
                )
            
            session_data = response.json()
            
            # Возвращаем необходимые данные для клиента
            return {
                "sessionId": session_data["id"],
                "clientSecret": session_data["client_secret"]["value"],
                "expiresAt": session_data["client_secret"]["expires_at"],
                "voice": session_data["voice"],
                "model": session_data["model"]
            }
        
    except httpx.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"❌ Session creation error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# ─────────── Проверка доступности OpenAI API ───────────
@app.get("/check_api")
async def check_api():
    try:
        # Проверяем доступность OpenAI API
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                "https://api.openai.com/v1/models",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                models = response.json().get("data", [])
                realtime_models = [model for model in models if "realtime" in model.get("id", "").lower()]
                
                return {
                    "status": "API доступен",
                    "models_count": len(models),
                    "realtime_models": realtime_models if realtime_models else "Не найдены"
                }
            else:
                return {
                    "status": "API недоступен",
                    "error": response.text
                }
    except Exception as e:
        traceback.print_exc()
        return {
            "status": "Ошибка проверки API",
            "error": str(e)
        }

# ─────────── TTS endpoint для синтеза речи ───────────
@app.post("/tts_stream")
async def tts_stream(req: TTSRequest):
    try:
        print(f"🔊 TTS Stream запрос: {req.text[:50]}... с голосом {req.voice}")
        
        # Создаем запрос к TTS API OpenAI
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "tts-1-hd",
                    "voice": req.voice,
                    "input": req.text,
                    "response_format": "mp3"
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"TTS API error: {response.text}")
            
            # Получаем аудио контент
            audio_data = response.content
            audio_stream = io.BytesIO(audio_data)
            audio_stream.seek(0)
            
            # Возвращаем аудио поток
            return StreamingResponse(
                content=audio_stream, 
                media_type="audio/mpeg",
                headers={"Content-Disposition": f"attachment; filename=speech_{req.voice}.mp3"}
            )
            
    except httpx.HTTPError as e:
        print(f"❌ TTS Stream HTTP error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"TTS Stream error: {str(e)}")
    except Exception as e:
        print(f"❌ TTS Stream error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"TTS Stream error: {str(e)}")

# ─────────── Health-check ───────────
@app.get("/health")
async def health():
    return {"status": "Jarvis Voice Assistant running 🚀", "version": "5.5"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
