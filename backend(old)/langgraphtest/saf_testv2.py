#!/usr/bin/env python3

import asyncio
import time
from statistics import mean

from google import genai
from google.genai.types import GenerateContentConfig, HttpOptions, ThinkingConfig

PROJECT_ID = "gen-lang-client-0430976171"
LOCATION = "europe-west3"
MODEL = "gemini-2.5-flash"
TOTAL_REQUESTS = 100
PROMPT = "hi"

# ✅ httpx_client verme — SDK kendi AsyncClient'ını yönetsin
client = genai.Client(
    http_options=HttpOptions(api_version="v1"),
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

start_event = asyncio.Event()

STREAM_CONFIG = GenerateContentConfig(
    thinking_config=ThinkingConfig(thinking_budget=0),
)


async def fetch_stream_response():
    text = ""
    stream = await client.aio.models.generate_content_stream(
        model=MODEL,
        contents=PROMPT,
        config=STREAM_CONFIG,
    )
    async for chunk in stream:
        if chunk.text:
            text += chunk.text
    return text


async def warmup():
    print("🔥 Bağlantı ısıtılıyor...")
    await asyncio.gather(*[fetch_stream_response() for _ in range(5)])
    print("✅ Hazır!\n")


async def single_request(i):
    await start_event.wait()
    start = time.perf_counter()
    await fetch_stream_response()
    elapsed = (time.perf_counter() - start) * 1000
    return i, elapsed


async def main():
    await warmup()

    print(f"💣 {TOTAL_REQUESTS} request hazırlanıyor...\n")
    tasks = [asyncio.create_task(single_request(i)) for i in range(TOTAL_REQUESTS)]
    await asyncio.sleep(0.05)

    print("🚀 HEPSİ AYNI ANDA GÖNDERİLDİ!\n")
    t0 = time.perf_counter()
    start_event.set()

    results = []
    for future in asyncio.as_completed(tasks):
        i, elapsed = await future
        print(f"[{i}] ⚡ {elapsed:.1f} ms")
        results.append(elapsed)

    total_time = (time.perf_counter() - t0) * 1000
    print("\n📊 SONUÇ:")
    print(f"Toplam süre  : {total_time:.1f} ms")
    print(f"Ortalama     : {mean(results):.1f} ms")
    print(f"En hızlı     : {min(results):.1f} ms")
    print(f"En yavaş     : {max(results):.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())