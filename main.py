from fastapi import FastAPI, WebSocket
import openai
import os
import asyncio

openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("✅ WebSocket подключен")
    try:
        while True:
            data = await websocket.receive_text()
            print(f"📥 Получено от клиента: {data}")
            
            # Отправляем в OpenAI для генерации ответа
            response = await openai.chat.completions.acreate(
                model="gpt-4o",
                messages=[{"role": "user", "content": data}],
                stream=True
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.get("content"):
                    content = chunk.choices[0].delta.content
                    await websocket.send_text(content)

            await websocket.send_text("[DONE]")
            print("✅ Ответ отправлен клиенту")

    except Exception as e:
        print(f"❌ Ошибка в WebSocket: {e}")
        await websocket.close()

@app.get("/")
async def root():
    return {"message": "Jarvis Server is Running 🚀"}
