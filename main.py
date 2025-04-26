# 📁 main.py — Jarvis backend (Голосовой помощник с WebSocket)
# Совместимо с openai-python ≥ 1.1.0

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from openai import AsyncOpenAI
import os
import httpx
import asyncio
import json
import base64
import io
from typing import Optional, List, Union, Literal
import traceback

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

# ─────────── Модели данных ───────────
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
async def websocket_proxy(websocket: WebSocket, token: str):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    print(f"🔌 WebSocket клиент подключен: {client_id}")
    
    # Переменная для отслеживания состояния обработки
    is_processing = False
    # Флаг для отслеживания состояния соединения
    active_connection = True
    
    try:
        # Отправляем клиенту событие session.created для инициализации
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
        
        # Создаем отдельную задачу для обработки сообщений
        async def process_audio(audio_base64, event_id="auto"):
            nonlocal is_processing
            if is_processing or not active_connection:
                return
                
            is_processing = True
            
            try:
                if not active_connection:
                    print(f"⚠️ Соединение уже закрыто для {client_id}, пропускаем обработку")
                    return
                    
                # Отправляем событие начала речи
                await websocket.send_text(json.dumps({
                    "type": "input_audio_buffer.speech_started",
                    "event_id": f"proxy_speech_event_{event_id}",
                    "audio_start_ms": 0,
                    "item_id": f"msg_proxy_{event_id}"
                }))
                
                await asyncio.sleep(0.5)  # Имитация задержки обработки
                
                if not active_connection:
                    return
                    
                # Отправляем событие окончания речи
                await websocket.send_text(json.dumps({
                    "type": "input_audio_buffer.speech_stopped",
                    "event_id": f"proxy_speech_end_event_{event_id}",
                    "audio_end_ms": 1000,
                    "item_id": f"msg_proxy_{event_id}"
                }))
                
                if not active_connection:
                    return
                    
                # Реальная транскрипция аудио через Whisper API
                transcript = "Привет, Джарвис!"  # Значение по умолчанию, будет заменено
                
                try:
                    # Декодируем base64 в бинарные данные
                    audio_data = base64.b64decode(audio_base64)
                    
                    # Создаем временный файл для аудио
                    audio_file = io.BytesIO(audio_data)
                    
                    # Транскрибируем аудио с помощью OpenAI Whisper API
                    transcription = await client.audio.transcriptions.create(
                        model="gpt-4o-transcribe",  # или "whisper-1"
                        file=audio_file,
                        language="ru"  # Указываем язык для лучшего распознавания
                    )
                    
                    # Получаем текст транскрипции
                    transcript = transcription.text
                    
                    # Если транскрипция пустая, используем резервное сообщение
                    if not transcript or transcript.strip() == "":
                        transcript = "Не удалось распознать речь"
                        print(f"⚠️ Пустая транскрипция, используем резервный текст")
                    
                    print(f"✅ Транскрипция успешно получена: {transcript}")
                    
                except Exception as e:
                    print(f"❌ Ошибка при транскрибировании аудио: {e}")
                    print(traceback.format_exc())
                
                if not active_connection:
                    return
                    
                # Отправляем событие транскрипции
                await websocket.send_text(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "event_id": f"proxy_transcription_event_{event_id}",
                    "item_id": f"msg_proxy_{event_id}",
                    "content_index": 0,
                    "transcript": transcript
                }))
                
                if not active_connection:
                    return
                    
                # Отправляем событие начала ответа
                await websocket.send_text(json.dumps({
                    "type": "response.created",
                    "event_id": f"proxy_response_event_{event_id}",
                    "response": {
                        "id": f"resp_proxy_{event_id}",
                        "status": "in_progress"
                    }
                }))
                
                if not active_connection:
                    return
                    
                # Получаем ответ от модели
                try:
                    completion = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."},
                            {"role": "user", "content": transcript}
                        ],
                        temperature=0.7,
                    )
                    
                    # Получаем ответ от модели
                    response_text = completion.choices[0].message.content
                except Exception as e:
                    print(f"❌ Ошибка GPT: {e}")
                    response_text = "Здравствуйте! Чем я могу вам помочь сегодня?"
                
                if not active_connection:
                    return
                    
                # Отправляем текст ответа по частям
                for i in range(0, len(response_text), 5):
                    if not active_connection:
                        return
                        
                    chunk = response_text[i:i+5]
                    await websocket.send_text(json.dumps({
                        "type": "response.text.delta",
                        "event_id": f"proxy_text_delta_{event_id}_{i}",
                        "response_id": f"resp_proxy_{event_id}",
                        "item_id": f"msg_assistant_proxy_{event_id}",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": chunk
                    }))
                    await asyncio.sleep(0.05)  # Ускоряем выдачу текста
                
                if not active_connection:
                    return
                    
                # Завершаем ответ
                await websocket.send_text(json.dumps({
                    "type": "response.done",
                    "event_id": f"proxy_response_done_{event_id}",
                    "response": {
                        "id": f"resp_proxy_{event_id}",
                        "status": "completed",
                        "output": [{
                            "id": f"msg_assistant_proxy_{event_id}",
                            "type": "message",
                            "role": "assistant",
                            "content": [{
                                "type": "text",
                                "text": response_text
                            }]
                        }]
                    }
                }))
            except Exception as e:
                print(f"❌ Ошибка при обработке аудио для {client_id}: {e}")
                traceback.print_exc()
            finally:
                is_processing = False
        
        # Основной цикл получения сообщений
        audio_buffer = []  # Буфер для хранения частей аудио
        msg_counter = 0
        while active_connection:
            try:
                # Ждем сообщение с тайм-аутом, чтобы избежать блокировки
                data = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                message = json.loads(data)
                msg_counter += 1
                
                print(f"📥 Получено сообщение от {client_id}: {message['type']}")
                
                # Обрабатываем типы сообщений
                if message["type"] == "input_audio_buffer.append":
                    # Здесь мы получаем аудио от клиента
                    audio_base64 = message.get("audio", "")
                    audio_size = len(audio_base64) if audio_base64 else 0
                    print(f"🎤 Получено аудио от {client_id}, размер: {audio_size} bytes")
                    
                    # Сохраняем полученное аудио в буфер
                    if audio_base64:
                        audio_buffer.append(audio_base64)
                    
                    # Проверяем нужно ли обрабатывать аудио сейчас
                    # (Обрабатываем сразу, если аудио достаточно большое)
                    if audio_size > 10000 and not is_processing:
                        # Объединяем все фрагменты аудио из буфера
                        combined_audio = ''.join(audio_buffer)
                        audio_buffer = []  # Очищаем буфер
                        
                        # Создаем фоновую задачу для обработки аудио
                        asyncio.create_task(process_audio(combined_audio, f"audio_{msg_counter}"))
                
                elif message["type"] == "input_audio_buffer.commit":
                    # Объединяем все фрагменты аудио из буфера
                    combined_audio = ''.join(audio_buffer)
                    audio_buffer = []  # Очищаем буфер
                    
                    if combined_audio:
                        # Создаем фоновую задачу для обработки аудио
                        asyncio.create_task(process_audio(combined_audio, f"commit_{msg_counter}"))
                
                elif message["type"] == "session.update":
                    # Клиент обновляет настройки сессии
                    await websocket.send_text(json.dumps({
                        "type": "session.updated",
                        "event_id": f"proxy_session_updated_{msg_counter}",
                        "session": message.get("session", {})
                    }))
                
                # Можно добавить обработку других типов сообщений при необходимости
                
            except asyncio.TimeoutError:
                # Просто продолжаем ожидание после тайм-аута
                continue
            except WebSocketDisconnect:
                print(f"🔌 WebSocket клиент отключился: {client_id}")
                active_connection = False
                break
            except json.JSONDecodeError:
                print(f"❌ Получены неверные данные JSON от {client_id}")
            except Exception as e:
                print(f"❌ Ошибка обработки сообщения от {client_id}: {e}")
                traceback.print_exc()
                if "disconnect" in str(e).lower():
                    active_connection = False
                    break
    
    except WebSocketDisconnect:
        print(f"🔌 WebSocket клиент отключился: {client_id}")
    except Exception as e:
        print(f"❌ WebSocket ошибка: {e}")
        traceback.print_exc()
    finally:
        # Отмечаем соединение как закрытое
        active_connection = False
        print(f"🔌 Закрытие прокси-соединения для {client_id}")

# ─────────── TTS endpoint для синтеза речи ───────────
@app.post("/tts")
async def tts(req: TTSRequest):
    try:
        print(f"🔊 TTS запрос: {req.text[:50]}... с голосом {req.voice}")
        
        # Создаем модель TTS
        audio_response = await client.audio.speech.create(
            model="tts-1-hd",
            voice=req.voice,
            input=req.text,
            response_format="mp3"
        )
        
        # Получаем аудиоданные
        # Важно: используем способ, который не требует await для bytes объекта
        audio_content = audio_response.content  # Это уже байты
        
        # Кодируем в base64 для передачи в JSON
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')
        
        print(f"✅ TTS успешно создан, размер: {len(audio_base64) // 1024} КБ")
        return {"audio": audio_base64}
    except Exception as e:
        print(f"❌ TTS error: {e}")
        print(traceback.format_exc())  # Печать подробного трейсбека ошибки
        # Возвращаем более подробную информацию об ошибке
        error_details = str(e)
        raise HTTPException(status_code=500, detail=f"TTS error: {error_details}")

# ─────────── Альтернативный TTS endpoint с прямой передачей аудио ───────────
@app.post("/tts_stream")
async def tts_stream(req: TTSRequest):
    try:
        print(f"🔊 TTS Stream запрос: {req.text[:50]}... с голосом {req.voice}")
        
        # Создаем модель TTS
        speech_response = await client.audio.speech.create(
            model="tts-1-hd",
            voice=req.voice,
            input=req.text,
            response_format="mp3"
        )
        
        # Получаем байты аудио
        audio_data = speech_response.content
        
        # Создаем поток из байтов
        audio_stream = io.BytesIO(audio_data)
        
        # Устанавливаем указатель в начало
        audio_stream.seek(0)
        
        print(f"✅ TTS Stream успешно создан, размер: {len(audio_data)} bytes")
        
        # Возвращаем аудио как поток
        return StreamingResponse(
            content=audio_stream, 
            media_type="audio/mpeg",
            headers={"Content-Disposition": f"attachment; filename=speech_{req.voice}.mp3"}
        )
        
    except Exception as e:
        print(f"❌ TTS Stream error: {e}")
        print(traceback.format_exc())
        error_details = str(e)
        raise HTTPException(status_code=500, detail=f"TTS Stream error: {error_details}")

# ─────────── Health-check ───────────
@app.get("/")
async def root():
    return {"status": "Jarvis Voice Assistant running 🚀", "version": "5.0 - Voice Only"}
