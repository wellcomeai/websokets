# 📁 main.py — Jarvis backend (FastAPI + OpenAI Realtime API)
# Совместимо с openai-python ≥ 1.1.0

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import AsyncOpenAI
import os
from typing import Optional, List, Union, Literal

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

# ─────────── Модели для Realtime API ───────────
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
    voice: str = "nova"
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[InputAudioTranscription] = InputAudioTranscription()
    turn_detection: Optional[TurnDetection] = TurnDetection()
    input_audio_noise_reduction: Optional[NoiseReduction] = NoiseReduction()
    temperature: float = 0.8
    max_response_output_tokens: Union[int, Literal["inf"]] = "inf"

# ─────────── Endpoint для создания сессии Realtime ───────────
@app.post("/create_session")
async def create_session(req: RealtimeSessionRequest):
    try:
        # Создаем сессию через OpenAI API
        response = await client.realtime.sessions.create(
            model=req.model,
            modalities=req.modalities,
            instructions=req.instructions,
            voice=req.voice,
            input_audio_format=req.input_audio_format,
            output_audio_format=req.output_audio_format,
            input_audio_transcription=req.input_audio_transcription.dict(exclude_none=True) if req.input_audio_transcription else None,
            turn_detection=req.turn_detection.dict(exclude_none=True) if req.turn_detection else None,
            input_audio_noise_reduction=req.input_audio_noise_reduction.dict(exclude_none=True) if req.input_audio_noise_reduction else None,
            temperature=req.temperature,
            max_response_output_tokens=req.max_response_output_tokens
        )
        
        # Возвращаем необходимые данные для клиента
        return {
            "sessionId": response.id,
            "clientSecret": response.client_secret.value,
            "expiresAt": response.client_secret.expires_at,
            "voice": response.voice
        }
        
    except Exception as e:
        print(f"❌ Session creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─────────── Health-check ───────────
@app.get("/")
async def root():
    return {"status": "Jarvis Realtime server running 🚀"}
