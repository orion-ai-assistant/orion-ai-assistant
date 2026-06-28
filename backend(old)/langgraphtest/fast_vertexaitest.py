import os
import time
import asyncio
from typing import List
import vertexai
from vertexai.generative_models import GenerativeModel, ChatSession, Content, Part

# --- AYARLAR ---
PROJECT_ID = "orionai-assistant-2604"
LOCATION = "europe-west3"

# Vertex AI Başlatma
vertexai.init(project=PROJECT_ID, location=LOCATION)
# En hızlı yanıt için gemini-2.5-flash kullanıyoruz
model = GenerativeModel("gemini-2.5-flash")

async def chat_loop():
    # Hafızalı bir sohbet oturumu başlatıyoruz
    chat = model.start_chat(
        history=[],
        config={
            "thinking_config": {"thinking_budget": 0},
        },
    )
    
    print("\n--- Saf Vertex AI Hız Testi Başladı ---")
    print("Çıkmak için 'exit' yazın.\n")

    # İlk istek her zaman yavaştır (SDK ısınması), bunu önden yapalım
    print("⚙️ SDK Isıtılıyor (İlk bağlantı kuruluyor)...")
    await model.generate_content_async("hi")
    print("✅ Hazır!\n")

    while True:
        user_input = input("💬 Sen: ")
        if user_input.lower() in ['exit', 'quit']:
            break

        start_time = time.perf_counter()
        first_token_time = None
        full_response = ""

        print("🤖 Cevap: ", end="", flush=True)

        try:
            # Stream=True yaparak ilk token'ın ne zaman geldiğini yakalıyoruz
            responses = await chat.send_message_async(
                user_input,
                stream=True,
                config={
                    "thinking_config": {"thinking_budget": 0},
                },
            )
            
            async for response in responses:
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                    ttft = (first_token_time - start_time) * 1000
                
                chunk = response.text
                full_response += chunk
                print(chunk, end="", flush=True)

            end_time = time.perf_counter()
            total_time = (end_time - start_time) * 1000

            print(f"\n\n--- HIZ RAPORU ---")
            print(f"⏱️ İlk Parça (TTFT): {ttft:.0f} ms")
            print(f"⏱️ Toplam Süre: {total_time:.0f} ms")
            print(f"------------------\n")

        except Exception as e:
            print(f"\n⚠️ Hata: {e}")

if __name__ == "__main__":
    asyncio.run(chat_loop())