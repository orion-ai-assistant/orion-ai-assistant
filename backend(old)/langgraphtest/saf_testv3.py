#!/usr/bin/env python3
"""
Gerçek paralel TTFT testi — ilk tokenlar terminalde canlı görünür.
"""

import asyncio
import time
import random
from statistics import mean, stdev

from google import genai
from google.genai.types import Content, GenerateContentConfig, HttpOptions, Part, ThinkingConfig

PROJECT_ID = "gen-lang-client-0430976171"
LOCATION   = "europe-west3"
MODEL      = "gemini-2.5-flash"

STREAM_CONFIG = GenerateContentConfig(
    thinking_config=ThinkingConfig(thinking_budget=0),
)

client = genai.Client(
    http_options=HttpOptions(api_version="v1"),
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

PROMPTS = [
    "hi", "hello", "what's 2+2?", "who are you?",
    "tell me a joke", "translate 'good morning' to French",
]

# ── TTFT ölçümü + canlı print ─────────────────────────────────────────────────
async def measure_ttft(idx: int, fired_at: float, total: int) -> dict:
    prompt   = random.choice(PROMPTS)
    contents = [Content(role="user", parts=[Part(text=prompt)])]
    ttft     = None

    try:
        stream = await client.aio.models.generate_content_stream(
            model=MODEL,
            contents=contents,
            config=STREAM_CONFIG,
        )
        async for chunk in stream:
            if ttft is None and chunk.text:
                ttft        = (time.perf_counter() - fired_at) * 1000
                first_token = chunk.text.strip()[:30].replace("\n", " ")

                if ttft < 400:   color = "\033[92m"   # yeşil
                elif ttft < 700: color = "\033[93m"   # sarı
                else:            color = "\033[91m"   # kırmızı
                reset = "\033[0m"

                bar = "█" * min(int(ttft / 20), 50)
                print(f"  [{idx:03d}/{total}] {color}{ttft:>6.0f}ms{reset}  {bar}  '{first_token}'")
                break

        return {"ttft": ttft, "success": True}
    except Exception as e:
        print(f"  [{idx:03d}/{total}] \033[91m❌ HATA\033[0m  {str(e)[:60]}")
        return {"ttft": None, "success": False}

# ── Tek round ─────────────────────────────────────────────────────────────────
async def run_round(n: int) -> list[float]:
    print(f"\n{'─'*58}")
    print(f"  🚀  {n} istek aynı anda fırlatılıyor...")
    print(f"  \033[92mYeşil\033[0m <400ms  ·  \033[93mSarı\033[0m <700ms  ·  \033[91mKırmızı\033[0m ≥700ms")
    print(f"{'─'*58}")

    fired_at = time.perf_counter()
    results  = await asyncio.gather(*[measure_ttft(i+1, fired_at, n) for i in range(n)])

    ttfts  = [r["ttft"] for r in results if r["success"] and r["ttft"]]
    errors = n - len(ttfts)
    print(f"\n  ✅ {len(ttfts)} başarılı  ❌ {errors} hata")
    return ttfts

# ── Stats ─────────────────────────────────────────────────────────────────────
def c(ms):
    if ms < 400:   return f"\033[92m{ms:>6.0f}ms\033[0m"
    if ms < 700:   return f"\033[93m{ms:>6.0f}ms\033[0m"
    return               f"\033[91m{ms:>6.0f}ms\033[0m"

def print_stats(ttfts: list[float], label: str):
    if not ttfts:
        return
    s   = sorted(ttfts)
    idx = lambda pct: s[min(int(len(s) * pct), len(s)-1)]
    print(f"\n  {label}")
    print(f"  {'p50':<8}: {c(idx(0.50))}")
    print(f"  {'p75':<8}: {c(idx(0.75))}")
    print(f"  {'p90':<8}: {c(idx(0.90))}")
    print(f"  {'p99':<8}: {c(idx(0.99))}")
    print(f"  {'min/max':<8}: {c(min(s))} / {c(max(s))}")

# ── Warmup ────────────────────────────────────────────────────────────────────
async def warmup():
    print("🔥  Isınıyor (5 istek)...")
    contents = [Content(role="user", parts=[Part(text="hi")])]
    await asyncio.gather(*[
        client.aio.models.generate_content(model=MODEL, contents=contents)
        for _ in range(5)
    ])
    print("✅  Hazır!")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    await warmup()

    all_results = {}
    for n in [10, 50, 100]:
        ttfts = await run_round(n)
        all_results[n] = ttfts
        await asyncio.sleep(2)

    print(f"\n{'═'*58}")
    print(f"  📊  TTFT — PARALEL YÜK KARŞILAŞTIRMASI")
    print(f"{'═'*58}")
    for n, ttfts in all_results.items():
        print_stats(ttfts, f"🔀  {n} eşzamanlı istek")
    print(f"\n{'═'*58}\n")

if __name__ == "__main__":
    asyncio.run(main())