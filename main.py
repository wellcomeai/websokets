# 📁 main.py — Jarvis Voice Assistant Backend

import os
import asyncio
import json
import base64
import io
import tempfile
import traceback
from typing import Optional, List, Union, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import httpx
from openai import AsyncOpenAI

# ────────────────── Конфигурация ──────────────────
# API ключ можно получить из переменной среды или задать напрямую
API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY не найден в переменных окружения")

# Клиент OpenAI с асинхронной поддержкой
client = AsyncOpenAI(api_key=API_KEY)

# ────────────────── FastAPI app ──────────────────
app = FastAPI(title="Jarvis Voice Assistant API")

# CORS (разрешаем запросы с любого домена для разработки)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── Модели данных ───────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "alloy"  # доступные голоса: alloy, shimmer, echo, onyx, nova, fable, etc.

class AudioTranscriptionRequest(BaseModel):
    audio: str  # base64-encoded audio data

class MessageRequest(BaseModel):
    message: str
    
class ChatItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatItem]
    system_prompt: Optional[str] = "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."
    temperature: Optional[float] = 0.7
    
# ─────────── Endpoint для транскрипции аудио ───────────
@app.post("/transcribe")
async def transcribe_audio(request: AudioTranscriptionRequest):
    try:
        # Декодируем base64 в бинарные данные
        try:
            audio_data = base64.b64decode(request.audio)
            print(f"✅ Получено аудио для транскрипции, размер: {len(audio_data)} байт")
        except Exception as e:
            print(f"❌ Ошибка декодирования base64: {e}")
            raise HTTPException(status_code=400, detail="Неверный формат base64")
        
        # Проверяем размер аудио
        if len(audio_data) < 100:
            return {"transcript": "Аудио слишком короткое или пустое"}
        
        # Создаем временный файл для аудио
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_filename = temp_file.name
            temp_file.write(audio_data)
        
        try:
            # Транскрибируем аудио с помощью OpenAI Whisper API
            print(f"🔄 Отправка аудио в Whisper API ({temp_filename})...")
            
            with open(temp_filename, 'rb') as audio_file:
                transcription = await client.audio.transcriptions.create(
                    model="whisper-1",  # Используем Whisper модель
                    file=audio_file,
                    language="ru"  # Явно указываем русский язык для лучшего распознавания
                )
            
            # Получаем текст транскрипции
            transcript = transcription.text
            print(f"✅ Транскрипция успешно получена: {transcript}")
            
            # Если транскрипция пустая, используем сообщение об ошибке
            if not transcript or transcript.strip() == "":
                transcript = "Не удалось распознать речь"
                print("⚠️ Пустая транскрипция")
            
            return {"transcript": transcript}
        
        except Exception as e:
            print(f"❌ Ошибка Whisper API: {e}")
            print(traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"Ошибка транскрипции: {str(e)}")
        
        finally:
            # Удаляем временный файл
            try:
                os.unlink(temp_filename)
            except Exception as e:
                print(f"⚠️ Не удалось удалить временный файл: {e}")
    
    except Exception as e:
        print(f"❌ Общая ошибка: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

# ─────────── Основной WebSocket для потоковой передачи ───────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    print(f"🔌 WebSocket клиент подключен: {client_id}")
    
    # Создаем задачу для отправки пингов
    ping_task = asyncio.create_task(send_ping_periodically(websocket, client_id))
    
    try:
        while True:
            # Получаем сообщение с таймаутом
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                # Отправляем пинг для проверки соединения
                await websocket.send_json({"type": "ping"})
                continue
                
            try:
                # Пытаемся распарсить как JSON
                try:
                    message = json.loads(data)
                    
                    # Обрабатываем разные типы сообщений
                    if isinstance(message, dict) and "type" in message:
                        # Структурированное сообщение с типом
                        msg_type = message["type"]
                        
                        # Обработка пинга
                        if msg_type == "ping":
                            await websocket.send_json({"type": "pong"})
                            continue
                            
                        # Обработка сообщения
                        elif msg_type == "message":
                            content = message.get("content", "")
                            if content:
                                asyncio.create_task(handle_message(content, websocket))
                            continue
                    
                    # Если это массив сообщений для чата
                    if "messages" in message:
                        asyncio.create_task(handle_chat(message, websocket))
                        continue
                        
                except json.JSONDecodeError:
                    # Если не JSON, считаем простым текстом
                    pass
                
                # По умолчанию обрабатываем как простое текстовое сообщение
                asyncio.create_task(handle_message(data, websocket))
                
            except Exception as e:
                print(f"❌ Ошибка обработки сообщения от {client_id}: {e}")
                print(traceback.format_exc())
                await websocket.send_json({
                    "type": "error",
                    "content": f"Ошибка: {str(e)}"
                })
                
    except WebSocketDisconnect:
        print(f"🔌 WebSocket клиент отключился: {client_id}")
    except Exception as e:
        print(f"❌ WebSocket ошибка: {e}")
        print(traceback.format_exc())
    finally:
        # Отменяем задачу пингов
        ping_task.cancel()
        print(f"🔌 Соединение закрыто для {client_id}")

# Функция для периодической отправки ping
async def send_ping_periodically(websocket: WebSocket, client_id: str):
    ping_counter = 0
    try:
        while True:
            await asyncio.sleep(25)  # Пингуем каждые 25 секунд
            ping_counter += 1
            try:
                await websocket.send_json({
                    "type": "ping",
                    "ping_id": f"server_ping_{ping_counter}"
                })
                print(f"♥️ Отправлен ping #{ping_counter} для {client_id}")
            except Exception as e:
                print(f"❌ Ошибка отправки ping: {e}")
                # Если произошла ошибка при отправке ping, прекращаем задачу
                break
    except asyncio.CancelledError:
        # Обработка отмены задачи
        print(f"🛑 Ping-задача для {client_id} отменена")
    except Exception as e:
        print(f"❌ Ошибка в ping-задаче для {client_id}: {e}")

# Обработка текстового сообщения
async def handle_message(message: str, websocket: WebSocket):
    try:
        print(f"📩 Получено сообщение: {message[:50]}...")
        
        # Отправляем сообщение о начале обработки
        await websocket.send_json({
            "type": "status",
            "content": "thinking"
        })
        
        # Получаем ответ от GPT-4o с потоковой передачей
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты русскоязычный голосовой помощник по имени Jarvis. Отвечай на русском языке. Твои ответы должны быть краткими и полезными."},
                {"role": "user", "content": message}
            ],
            temperature=0.7,
            stream=True
        )
        
        # Собираем полный ответ для TTS
        full_response = ""
        
        # Отправляем сообщение о начале передачи
        await websocket.send_json({
            "type": "response_start"
        })
        
        # Отправляем ответ по частям
        async for chunk in response:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_response += delta
                    await websocket.send_json({
                        "type": "chunk",
                        "content": delta
                    })
        
        # Отправляем полный ответ для TTS
        await websocket.send_json({
            "type": "complete",
            "content": full_response
        })
        
        print(f"✅ Отправлен полный ответ: {full_response[:100]}...")
        
    except Exception as e:
        print(f"❌ Ошибка обработки сообщения: {e}")
        print(traceback.format_exc())
        try:
            await websocket.send_json({
                "type": "error",
                "content": f"Ошибка: {str(e)}"
            })
        except Exception:
            pass

# Обработка запроса чата с несколькими сообщениями
async def handle_chat(chat_request: dict, websocket: WebSocket):
    try:
        # Преобразуем запрос в объект модели, если передали словарь
        if isinstance(chat_request, dict):
            try:
                chat_request = ChatRequest(**chat_request)
            except Exception as e:
                print(f"❌ Ошибка преобразования запроса чата: {e}")
                await websocket.send_json({
                    "type": "error",
                    "content": f"Неверный формат запроса: {str(e)}"
                })
                return
        
        # Извлекаем сообщения
        messages = []
        
        # Добавляем системный промпт
        if chat_request.system_prompt:
            messages.append({
                "role": "system", 
                "content": chat_request.system_prompt
            })
        
        # Добавляем сообщения из запроса
        for msg in chat_request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Отправляем сообщение о начале обработки
        await websocket.send_json({
            "type": "status",
            "content": "thinking"
        })
        
        # Вызываем модель GPT с потоковой передачей
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=chat_request.temperature if chat_request.temperature is not None else 0.7,
            stream=True
        )
        
        # Собираем полный ответ для TTS
        full_response = ""
        
        # Отправляем ответ по частям
        async for chunk in response:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_response += delta
                    await websocket.send_json({
                        "type": "chunk",
                        "content": delta
                    })
        
        # Отправляем полный ответ для TTS
        await websocket.send_json({
            "type": "complete",
            "content": full_response
        })
        
    except Exception as e:
        print(f"❌ Ошибка обработки чата: {e}")
        print(traceback.format_exc())
        try:
            await websocket.send_json({
                "type": "error",
                "content": f"Ошибка: {str(e)}"
            })
        except Exception:
            pass

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
        audio_content = audio_response.content  # Это уже байты
        
        # Кодируем в base64 для передачи в JSON
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')
        
        print(f"✅ TTS успешно создан, размер: {len(audio_base64) // 1024} КБ")
        return {"audio": audio_base64}
    except Exception as e:
        print(f"❌ TTS error: {e}")
        print(traceback.format_exc())
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

# ─────────── Одноразовый запрос к чату ───────────
@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        # Создаем список сообщений
        messages = []
        
        # Добавляем системный промпт
        if request.system_prompt:
            messages.append({
                "role": "system", 
                "content": request.system_prompt
            })
        
        # Добавляем сообщения из запроса
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Вызываем GPT API
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=request.temperature,
        )
        
        # Получаем ответ
        message = response.choices[0].message.content
        
        return {"response": message}
    
    except Exception as e:
        print(f"❌ Chat error: {e}")
        print(traceback.format_exc())
        error_details = str(e)
        raise HTTPException(status_code=500, detail=f"Chat error: {error_details}")

# ─────────── Health-check ───────────
@app.get("/")
async def root():
    return {
        "status": "Jarvis Voice Assistant running 🚀", 
        "version": "5.1",
        "services": {
            "websocket": "/ws",
            "transcription": "/transcribe",
            "tts": "/tts",
            "tts_stream": "/tts_stream",
            "chat": "/chat"
        }
    }

# Запуск приложения через uvicorn
if __name__ == "__main__":
    import uvicorn
    
    # Получаем порт из переменной окружения или используем 10000 по умолчанию
    port = int(os.getenv("PORT", 10000))
    
    # Запускаем сервер
    uvicorn.run(app, host="0.0.0.0", port=port)
