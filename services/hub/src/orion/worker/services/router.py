"""
Orion Service Router Client
===========================
Gelen AI isteklerini (LLM, Embedding, TTS) yeni özel router container'ına yönlendirir.
Yönlendirme kararı (local vs external), istek başlıklarındaki (X-Orion-Provider vb.)
değerlere göre router container'ı tarafında verilir.

Session Management:
  HTTP bağlantı havuzu (connection pool) performans için kritiktir. ClientSession
  uygulama başladığında bir kez açılır ve tüm isteklerde tekrar kullanılır.
  Worker kapanırken kapatılır.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from services.shared.environment import get_router_base_urls
from orion.contracts.settings import RuntimeSettings


# ---------------------------------------------------------------------------
#  Session Manager — Tek seferlik, paylaşımlı HTTP oturumu
# ---------------------------------------------------------------------------

_session: ClientSession | None = None


async def get_session() -> ClientSession:
    """Mevcut paylaşımlı ClientSession'ı döner; henüz yoksa oluşturur."""
    global _session
    if _session is None or _session.closed:
        connector = TCPConnector(limit=100, limit_per_host=20, enable_cleanup_closed=True)
        _session = ClientSession(connector=connector)
    return _session


async def close_session() -> None:
    """Worker kapanırken çağrılır, bağlantı havuzunu temiz bir şekilde kapatır."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ---------------------------------------------------------------------------
#  URL Helpers — DRY prensibine uygun, tekrarsız URL oluşturma
# ---------------------------------------------------------------------------

def _base_url(raw: str) -> str:
    """Trailing slash ve /v1 eki varsa çıkarır, temiz base döner."""
    url = raw.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _router_urls(path: str) -> list[str]:
    """Özel Router container'ı için olası tüm URL'leri üretir. path örn: '/v1/chat/completions'"""
    return [f"{_base_url(url)}{path}" for url in get_router_base_urls()]


# ---------------------------------------------------------------------------
#  Timeout Helpers
# ---------------------------------------------------------------------------

def _stream_timeout(settings: RuntimeSettings) -> ClientTimeout:
    """Streaming istekleri için: toplam süre yok, iki chunk arası max bekleme süresi var."""
    return ClientTimeout(
        total=None,
        connect=1.0,
        sock_read=float(settings.llm_timeout_seconds),
    )


def _request_timeout(total_seconds: int | float) -> ClientTimeout:
    """Tek seferlik (non-streaming) istekler için: toplam süre limiti var."""
    return ClientTimeout(total=float(total_seconds), connect=1.0)


# ---------------------------------------------------------------------------
#  LLM Chat - Streaming
# ---------------------------------------------------------------------------

async def llama_stream_chat_typed(
    messages: list[dict[str, Any]], settings: RuntimeSettings
) -> AsyncGenerator[tuple[str, str], None]:
    """LLM streaming — thinking ve content tokenlarını ayrı ayrı yayınlar.

    Yields:
        ("thinking", token) — reasoning / chain-of-thought içeriği
        ("content",  token) — normal asistan cevabı
    """
    session = await get_session()

    urls = _router_urls("/v1/chat/completions")
    headers = {
        "x-orion-api-key": settings.router_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": True,
        "temperature": settings.temperature,
        "thinking_level": settings.thinking_level,
    }

    timeout = _stream_timeout(settings)

    async def _parse_buffer(buf: bytes) -> AsyncIterator[tuple[str, str]]:
        """Parse a single SSE line buffer and yield typed tokens."""
        line = buf.strip()
        if not line:
            return
        if line.startswith(b"data:"):
            line = line[len(b"data:"):].strip()
        if line == b"[DONE]":
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        if error := data.get("error"):
            raise RuntimeError(error)
            
        choices = data.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}

        # 1. API-level reasoning_content (DeepSeek / OpenRouter thinking models)
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if reasoning:
            yield ("thinking", reasoning)

        # 2. Normal content
        content = delta.get("content") or ""
        if content:
            yield ("content", content)

    last_error = None
    for url in urls:
        yielded_any = False
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise RuntimeError(f"HTTP {response.status} - {error_text}")
                
                raw_buffer = b""
                async for chunk in response.content.iter_any():
                    if not chunk:
                        continue
                    raw_buffer += chunk
                    while b"\n" in raw_buffer:
                        line, raw_buffer = raw_buffer.split(b"\n", 1)
                        async for pair in _parse_buffer(line):
                            yielded_any = True
                            yield pair

                # Flush remaining buffer
                if raw_buffer.strip():
                    async for pair in _parse_buffer(raw_buffer):
                        yielded_any = True
                        yield pair
                        
                return  # Başarılı olduğunda tamamen çık
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if yielded_any:
                # Veri göndermeye başladıktan sonra hata aldıysak fallback yapamayız, hatayı fırlat.
                raise
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")


async def llama_stream_chat(messages: list[dict[str, Any]], settings: RuntimeSettings) -> AsyncGenerator[str, None]:
    """Backward-compatible wrapper — yields only content tokens (thinking tokens are dropped).

    New code should prefer llama_stream_chat_typed().
    """
    async for kind, token in llama_stream_chat_typed(messages, settings):
        if kind == "content":
            yield token


# ---------------------------------------------------------------------------
#  LLM Chat - Non-Streaming (Synchronous)
# ---------------------------------------------------------------------------

async def llama_chat(messages: list[dict[str, Any]], settings: RuntimeSettings) -> str:
    """LLM sohbet isteğini tek seferde (non-streaming) yönlendirir."""
    session = await get_session()

    urls = _router_urls("/v1/chat/completions")
    headers = {
        "x-orion-api-key": settings.router_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": False,
        "temperature": settings.temperature,
        "thinking_level": settings.thinking_level,
    }

    timeout = _request_timeout(settings.llm_timeout_seconds)

    last_error = None
    for url in urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                data: dict[str, Any] = await response.json()
            
            choices = data.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            content = message.get("content") or ""
            if not content:
                raise ValueError("LLM response missing message content")
            return content
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")


# ---------------------------------------------------------------------------
#  Embedding
# ---------------------------------------------------------------------------

async def generate_embeddings(text: str, settings: RuntimeSettings) -> list[float]:
    """Metin gömme (embedding) isteğini seçilen sağlayıcıya yönlendirir."""
    session = await get_session()

    urls = _router_urls("/v1/embeddings")
    headers = {
        "x-orion-api-key": settings.router_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "input": text,
        "model": settings.router_model_group,
    }
    timeout = _request_timeout(settings.embed_timeout_seconds)

    last_error = None
    for url in urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                data = await response.json()

            elements = data.get("data") or []
            if not elements or "embedding" not in elements[0]:
                raise ValueError("Embedding response missing embedding data")
            return elements[0]["embedding"]
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")


# Backward Compatibility Alias
tei_embed = generate_embeddings


# ---------------------------------------------------------------------------
#  TTS (Text-to-Speech)
# ---------------------------------------------------------------------------

async def generate_tts(text: str, settings: RuntimeSettings, voice: str = "alloy") -> bytes:
    """TTS üretimi için seçilen sağlayıcıya göre istek atar ve ses verisini (bytes) döner."""
    session = await get_session()

    urls = _router_urls("/v1/audio/speech")
    headers = {
        "x-orion-api-key": settings.router_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.router_model_group,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }
    timeout = _request_timeout(30)

    last_error = None
    for url in urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")
