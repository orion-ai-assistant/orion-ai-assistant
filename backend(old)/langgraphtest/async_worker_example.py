import argparse
import os
import json
import asyncio
import time
import redis.asyncio as redis
from typing import AsyncGenerator

# Google Cloud SDK
import vertexai
from vertexai.generative_models import GenerativeModel

# 1. ÇEVRE DEĞİŞKENLERİ VE AYARLAR
PROJECT_ID = os.getenv("PROJECT_ID", "gen-lang-client-0430976171")
LOCATION = os.getenv("LOCATION", "europe-west1")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "C:/Users/krsta/AppData/Roaming/gcloud/application_default_credentials.json",
)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS

# Vertex AI Başlatma
vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel("gemini-2.5-flash")  # En hızlı model

def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def get_inbound_queue() -> str:
    return os.getenv("INBOUND_QUEUE", "inbound_messages")


def get_outbound_queue() -> str:
    return os.getenv("OUTBOUND_QUEUE", "outbound_messages")

# 2. GERÇEK VERTEX AI STREAM ÇAĞRISI
async def call_vertex_ai_stream(message: str) -> AsyncGenerator[str, None]:
    """
    Vertex AI'a asenkron ve akan (stream) şekilde sorar.
    Burada 'await' dediğimiz her an, Python 100 farklı kişiye bakabilir.
    """
    try:
        # Google'a isteği gönderiyoruz
        responses = await model.generate_content_async(message, stream=True)
        
        async for response in responses:
            # Her bir parça (chunk) geldiğinde yield ile dışarı fırlatıyoruz
            chunk_text = response.text
            if chunk_text:
                yield chunk_text
    except Exception as e:
        yield f"⚠️ Hata oluştu: {str(e)}"

# 3. İSTEK İŞLEME MANTIĞI
async def process_request(data_raw: str, r: redis.Redis) -> None:
    """Her kullanıcı isteğini bağımsız bir görev (task) olarak işler."""
    data = json.loads(data_raw)
    user_id = data.get("user_id", "unknown")
    text = data.get("text", "")

    print(f"📩 [{user_id}] sorgusu alınıyor: {text[:50]}...")
    start_time = time.monotonic()
    first_token_ms: float | None = None
    first_token: str | None = None
    tokens: list[str] = []

    async for token in call_vertex_ai_stream(text):
        if first_token_ms is None:
            first_token_ms = (time.monotonic() - start_time) * 1000
            first_token = token
            print(
                f"⏱️ [{user_id}] ilk token {first_token_ms:.0f}ms içinde geldi: {first_token!r}"
            )
        tokens.append(token)

    final_text = "".join(tokens)
    end_time = time.monotonic()
    latency_ms = int((end_time - start_time) * 1000)

    result_payload = {
        "user_id": user_id,
        "text": final_text,
        "latency_ms": latency_ms,
        "first_token_ms": int(first_token_ms or 0),
        "first_token": first_token,
    }
    await r.lpush(get_outbound_queue(), json.dumps(result_payload, ensure_ascii=False))

    print(f"✅ [{user_id}] tamamlandı: toplam {latency_ms}ms, ilk token {result_payload['first_token_ms']}ms")
    print(f"💬 [{user_id}] cevap: {final_text[:200]}{'...' if len(final_text) > 200 else ''}")

# 5. ANA WORKER DÖNGÜSÜ
async def worker_loop(max_concurrency: int | None = None) -> None:
    """Redis kuyruğunu dinleyen ana motor."""
    r = redis.from_url(get_redis_url(), decode_responses=True)
    print(f"🚀 Orion Agent Başlatıldı | Proje: {PROJECT_ID} | Konum: {LOCATION}")

    semaphore = (
        asyncio.Semaphore(max_concurrency)
        if max_concurrency is not None and max_concurrency > 0
        else asyncio.Semaphore(50)
    )

    async def semaphore_wrapper(data_raw: str) -> None:
        async with semaphore:
            await process_request(data_raw, r)

    while True:
        # Redis'ten yeni görev bekle (Blocking Pop)
        result = await r.brpop(get_inbound_queue())
        if not result:
            continue
            
        _, data_raw = result
        
        # KRİTİK: create_task sayesinde kod bekleme yapmadan bir sonrakine geçer.
        # 100 kişi gelirse 100 tane task aynı anda arka planda Google'dan cevap bekler.
        asyncio.create_task(semaphore_wrapper(data_raw))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LangGraph Vertex AI async worker")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=50,
        help="Aynı anda kaç LLM isteğinin paralel çalışacağı (default 50)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(worker_loop(max_concurrency=args.max_concurrency))
    except KeyboardInterrupt:
        print("🛑 Worker durduruldu.")