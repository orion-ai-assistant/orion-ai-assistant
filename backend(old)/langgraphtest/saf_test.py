#!/usr/bin/env python3

import asyncio
import time
from statistics import mean

from google import genai
from google.genai.types import GenerateContentConfig, HttpOptions, ThinkingConfig

# 🔥 AYARLAR
PROJECT_ID = "orionai-assistant-2604"
LOCATION = "europe-west3"
MODEL = "gemini-2.5-flash"

TOTAL_REQUESTS = 1
PROMPT = "hi"

client = genai.Client(
    http_options=HttpOptions(api_version="v1"),
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

start_event = asyncio.Event()


async def warmup_connection():
    # İlk bağlantıyı açmak ve Vertex'te ısınma maliyetini tek seferde almak için
    # tek seferlik bir dummy istek gönderiyoruz.
    async for _ in await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=".",
        config=GenerateContentConfig(
            thinking_config=ThinkingConfig(thinking_budget=0),
        ),
    ):
        pass


async def fetch_stream_response():
    text = ""
    async for chunk in await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=PROMPT,
        config=GenerateContentConfig(
            thinking_config=ThinkingConfig(thinking_budget=0),
        ),
    ):
        if chunk.text:
            text += chunk.text
    return text


async def single_request(i):
    await start_event.wait()

    start = time.perf_counter()
    text = await fetch_stream_response()
    elapsed = (time.perf_counter() - start) * 1000
    return i, elapsed


async def main():
    print(f"\n💣 {TOTAL_REQUESTS} request hazırlanıyor...\n")

    await warmup_connection()
    print("🔧 Vertex bağlantısı ısıtıldı, ikinci kısımda artık aynı bağlantıyı kullanıyoruz.\n")

    tasks = [asyncio.create_task(single_request(i)) for i in range(TOTAL_REQUESTS)]

    await asyncio.sleep(1)

    print("🚀 HEPSİ AYNI ANDA GÖNDERİLDİ!\n")
    t0 = time.perf_counter()

    start_event.set()

    results = []

    # 🔥 GELDİKÇE YAZDIR
    for future in asyncio.as_completed(tasks):
        i, elapsed = await future
        print(f"[{i}] ⚡ {elapsed:.1f} ms")
        results.append(elapsed)

    total_time = (time.perf_counter() - t0) * 1000

    print("\n📊 SONUÇ:")
    print(f"Toplam süre: {total_time:.1f} ms")
    print(f"Ortalama: {mean(results):.1f} ms")
    print(f"En hızlı: {min(results):.1f} ms")
    print(f"En yavaş: {max(results):.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())