from fastapi import FastAPI, WebSocket
import openai, os, asyncio

# ключ берётся из переменной окружения OPENAI_API_KEY
openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    client = f"{ws.client.host}:{ws.client.port}"
    print(f"🔌 WS connected {client}", flush=True)

    try:
        while True:
            # 1) ждём текст от браузера
            text = await ws.receive_text()
            print(f"📥 {client} → {text!r}", flush=True)

            # 2) стримим ответ GPT-4o
            stream = await openai.chat.completions.acreate(
                model="gpt-4o",
                messages=[{"role": "user", "content": text}],
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    await ws.send_text(delta)

            # 3) сигнал конца потока
            await ws.send_text("[DONE]")
            print(f"✅ answer sent to {client}", flush=True)

    except Exception as e:
        print(f"❌ WS error {client}: {e}", flush=True)
        await ws.close()


@app.get("/")
async def root():
    return {"status": "Jarvis server running 🚀"}
