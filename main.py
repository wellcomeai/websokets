# 📁 main.py — Jarvis backend (FastAPI + OpenAI Realtime API)
# Совместимо с openai-python ≥ 1.1.0

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AsyncOpenAI
import os
import httpx
import asyncio
import json
import base64
from typing import Optional, List, Union, Literal

# ────────────────── OpenAI async-клиент ──────────────────
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
API_KEY = os.getenv("OPENAI_API_KEY")

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
    voice: str = "alloy"  # Поддерживаемые голоса: alloy, ash, ballad, coral, echo, sage, shimmer, verse
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcription: Optional[InputAudioTranscription] = None
    turn_detection: Optional[TurnDetection] = None
    input_audio_noise_reduction: Optional[NoiseReduction] = None
    temperature: float = 0.8
    max_response_output_tokens: Union[int, Literal["inf"]] = "inf"

# ─────────── Endpoint для создания сессии Realtime ───────────
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
                "voice": session_data["voice"]
            }
        
    except httpx.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"❌ Session creation error: {e}")
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
        return {
            "status": "Ошибка проверки API",
            "error": str(e)
        }

# ─────────── WebSocket прокси для Realtime API ───────────
@app.websocket("/ws_proxy/{token}")
async def websocket_proxy(websocket: WebSocket, token: str, background_tasks: BackgroundTasks):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    print(f"🔌 WebSocket клиент подключен: {client_id}")
    
    try:
        # Отправляем клиенту событие session.created
        # Для имитации поведения WebSocket API
        await websocket.send_text(json.dumps({
            "type": "session.created",
            "event_id": "proxy_init_event",
            "session": {
                "id": "proxy_session",
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "model": "gpt-4o-realtime-preview",
                "instructions": "Ты русскоязычный голосовой помощник по имени Jarvis.",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16"
            }
        }))
        
        print(f"✅ Отправлено session.created для {client_id}")
        
        # Обработка сообщений от клиента
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                print(f"📥 Получено сообщение от {client_id}: {message['type']}")
                
                # Обрабатываем типы сообщений
                if message["type"] == "input_audio_buffer.append":
                    # Здесь мы получаем аудио от клиента
                    audio_base64 = message.get("audio", "")
                    audio_size = len(audio_base64) if audio_base64 else 0
                    print(f"🎤 Получено аудио от {client_id}, размер: {audio_size} bytes")
                    
                    # Имитация обработки аудио
                    # Отправляем событие речи
                    await websocket.send_text(json.dumps({
                        "type": "input_audio_buffer.speech_started",
                        "event_id": "proxy_speech_event",
                        "audio_start_ms": 0,
                        "item_id": "msg_proxy"
                    }))
                    
                    # Отправляем событие окончания речи через 1 секунду
                    await asyncio.sleep(1)
                    await websocket.send_text(json.dumps({
                        "type": "input_audio_buffer.speech_stopped",
                        "event_id": "proxy_speech_end_event",
                        "audio_end_ms": 1000,
                        "item_id": "msg_proxy"
                    }))
                    
                    # Отправляем событие транскрипции
                    await websocket.send_text(json.dumps({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "event_id": "proxy_transcription_event",
                        "item_id": "msg_proxy",
                        "content_index": 0,
                        "transcript": "Привет, Джарвис!"
                    }))
                    
                    # Отправляем событие начала ответа
                    await websocket.send_text(json.dumps({
                        "type": "response.created",
                        "event_id": "proxy_response_event",
                        "response": {
                            "id": "resp_proxy",
                            "status": "in_progress"
                        }
                    }))
                    
                    # Отправляем текст ответа по частям
                    response_text = "Здравствуйте! Чем я могу вам помочь сегодня?"
                    for i in range(0, len(response_text), 5):
                        chunk = response_text[i:i+5]
                        await websocket.send_text(json.dumps({
                            "type": "response.text.delta",
                            "event_id": f"proxy_text_delta_{i}",
                            "response_id": "resp_proxy",
                            "item_id": "msg_assistant_proxy",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": chunk
                        }))
                        await asyncio.sleep(0.1)
                    
                    # Завершаем ответ
                    await websocket.send_text(json.dumps({
                        "type": "response.done",
                        "event_id": "proxy_response_done",
                        "response": {
                            "id": "resp_proxy",
                            "status": "completed",
                            "output": [{
                                "id": "msg_assistant_proxy",
                                "type": "message",
                                "role": "assistant",
                                "content": [{
                                    "type": "text",
                                    "text": response_text
                                }]
                            }]
                        }
                    }))
                
                elif message["type"] == "manual_message":
                    # Ручной запрос от кнопки "Тест"
                    text_request = message.get("text", "Привет, Джарвис!")
                    print(f"📝 Ручной запрос от {client_id}: {text_request}")
                    
                    # Отправляем событие транскрипции
                    await websocket.send_text(json.dumps({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "event_id": "proxy_manual_transcription_event",
                        "item_id": "msg_proxy_manual",
                        "content_index": 0,
                        "transcript": text_request
                    }))
                    
                    # Отправляем событие начала ответа
                    await websocket.send_text(json.dumps({
                        "type": "response.created",
                        "event_id": "proxy_manual_response_event",
                        "response": {
                            "id": "resp_proxy_manual",
                            "status": "in_progress"
                        }
                    }))
                    
                    # Запрашиваем ответ от GPT
                    try:
                        completion = await client.chat.completions.create(
                            model="gpt-4o",
                            messages=[
                                {"role": "system", "content": "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."},
                                {"role": "user", "content": text_request}
                            ],
                            temperature=0.7,
                        )
                        
                        # Получаем ответ от модели
                        response_text = completion.choices[0].message.content
                    except Exception as e:
                        # Если ошибка, используем предустановленный ответ
                        print(f"❌ Ошибка GPT: {e}")
                        response_text = "Извините, я не смог обработать ваш запрос. Как я могу вам помочь иначе?"
                    
                    # Отправляем текст ответа по частям
                    for i in range(0, len(response_text), 5):
                        chunk = response_text[i:i+5]
                        await websocket.send_text(json.dumps({
                            "type": "response.text.delta",
                            "event_id": f"proxy_manual_delta_{i}",
                            "response_id": "resp_proxy_manual",
                            "item_id": "msg_assistant_proxy_manual",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": chunk
                        }))
                        await asyncio.sleep(0.1)
                    
                    # Завершаем ответ
                    await websocket.send_text(json.dumps({
                        "type": "response.done",
                        "event_id": "proxy_manual_response_done",
                        "response": {
                            "id": "resp_proxy_manual",
                            "status": "completed",
                            "output": [{
                                "id": "msg_assistant_proxy_manual",
                                "type": "message",
                                "role": "assistant",
                                "content": [{
                                    "type": "text",
                                    "text": response_text
                                }]
                            }]
                        }
                    }))
                
                elif message["type"] == "session.update":
                    # Клиент обновляет настройки сессии
                    await websocket.send_text(json.dumps({
                        "type": "session.updated",
                        "event_id": "proxy_session_updated",
                        "session": message.get("session", {})
                    }))
                
                # Добавьте обработку других типов сообщений при необходимости
                
            except json.JSONDecodeError:
                print(f"❌ Получены неверные данные JSON от {client_id}")
            except Exception as e:
                print(f"❌ Ошибка обработки сообщения от {client_id}: {e}")
    
    except WebSocketDisconnect:
        print(f"🔌 WebSocket клиент отключился: {client_id}")
    except Exception as e:
        print(f"❌ WebSocket ошибка: {e}")
    finally:
        # Закрываем соединение, если оно было открыто
        print(f"🔌 Закрытие прокси-соединения для {client_id}")

# ─────────── Текстовый чат с GPT ───────────
class ChatRequest(BaseModel):
    message: str
    
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": req.message}],
            temperature=0.7,
        )
        
        return {"response": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────── TTS endpoint для синтеза речи ───────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"   # допустимые для TTS API: alloy, shimmer, echo, onyx, nova, fable

@app.post("/tts")
async def tts(req: TTSRequest):
    try:
        print(f"🔊 TTS запрос: {req.text[:50]}... с голосом {req.voice}")
        
        try:
            # Пробуем сначала через audio.speech API
            audio_response = await client.audio.speech.create(
                model="tts-1-hd",
                voice=req.voice,
                input=req.text
            )
            
            # Получаем аудиоданные как bytes
            audio_data = await audio_response.read()
        except Exception as e:
            print(f"⚠️ Не удалось использовать audio.speech API: {e}, переключаемся на чтение файла")
            
            # Альтернативный вариант - создаем и читаем файл
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
                # Создаем временный файл для аудио
                temp_path = temp_file.name
            
            completion = await client.audio.speech.create(
                model="tts-1-hd",
                voice=req.voice,
                input=req.text,
                response_format="mp3"
            )
            
            # Сохраняем во временный файл
            with open(temp_path, "wb") as f:
                f.write(await completion.read())
            
            # Читаем файл
            with open(temp_path, "rb") as f:
                audio_data = f.read()
            
            # Удаляем временный файл
            import os
            os.unlink(temp_path)
        
        # Кодируем в base64 для передачи в JSON
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        print(f"✅ TTS успешно создан, размер: {len(audio_base64) // 1024} КБ")
        return {"audio": audio_base64}
    except Exception as e:
        print(f"❌ TTS error: {e}")
        # Возвращаем более подробную информацию об ошибке
        error_details = str(e)
        raise HTTPException(status_code=500, detail=f"TTS error: {error_details}")


# ─────────── Health-check ───────────
@app.get("/")
async def root():
    return {"status": "Jarvis Realtime server running 🚀"}
