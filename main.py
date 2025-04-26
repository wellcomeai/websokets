from fastapi import FastAPI, WebSocket
from openai import AsyncOpenAI
import os, asyncio

"""Jarvis WebSocket‑сервер — версия, совместимая с openai‑python ≥ 1.0.0
(в 1.x синтаксис .acreate исчез, используется клиент AsyncOpenAI)
"""

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = f"{ws.client.host}:{ws.client.port}"
    print(f"🔌 WS connected {cid}", flush=True)

    try:
        while True:
            # 1️⃣ получаем текст от клиента
            text = await ws.receive_text()
            print(f"📥 {cid} → {text!r}", flush=True)

            # 2️⃣ запрашиваем GPT‑4o в режиме стрима
            stream = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": text}],
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    await ws.send_text(delta)

            # 3️⃣ сигнал конца
            await ws.send_text("[DONE]")
            print(f"✅ answer sent to {cid}", flush=True)

    except Exception as e:
        print(f"❌ WS error {cid}: {e}", flush=True)
        await ws.close()


@app.get("/")
async def root():
    return {"status": "Jarvis server running 🚀"}
