# 📁 main.py — обновлённая версия под openai>=1.0.0

import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            prompt = await websocket.receive_text()
            await handle_message(prompt, websocket)
    except WebSocketDisconnect:
        print("Клиент отключился")

async def handle_message(message: str, websocket: WebSocket):
    try:
        stream = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": message}],
            stream=True
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                await websocket.send_text(delta)

        await websocket.send_text("[DONE]")

    except Exception as e:
        await websocket.send_text(f"Ошибка: {str(e)}")
