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
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Awaitable
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from services.shared.environment import get_router_base_urls, get_tts_base_urls
from orion.contracts.settings import RuntimeSettings


def _get_router_api_key(settings: RuntimeSettings) -> str:
    """Orion Router ile iletişim için API anahtarını döner.
    Kullanıcı özel bir anahtar tanımlamadıysa varsayılan 'orion' admin secret kullanılır.
    """
    key = (getattr(settings, "router_api_key", "") or "").strip()
    if key and key != "sk-60f3eaf169d7c485-0icocf-0a3db541":
        return key
    return os.getenv("ROUTER_API_KEY", "").strip() or "orion"


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


def _chat_provider(model_group: str) -> str | None:
    lowered_model = (model_group or "").strip().lower()
    if lowered_model in ("", "none", "null", "default", "local", "local-chat", "local-model"):
        return "local"
    if "gemini" in lowered_model:
        return "gemini"
    if "openai" in lowered_model or "gpt" in lowered_model:
        return "openai"
    return None


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
    messages: list[dict[str, Any]],
    settings: RuntimeSettings,
    stop_checker: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """LLM streaming — thinking ve content tokenlarını ayrı ayrı yayınlar.

    Yields:
        ("thinking", token) — reasoning / chain-of-thought içeriği
        ("content",  token) — normal asistan cevabı
    """
    session = await get_session()

    urls = _router_urls("/v1/chat/completions")
    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
        "Content-Type": "application/json",
    }
    provider = _chat_provider(settings.router_model_group)
    if provider:
        headers["x-orion-provider"] = provider
    payload: dict[str, Any] = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": True,
        "temperature": settings.temperature,
    }
    thinking_level = (getattr(settings, "thinking_level", "") or "").strip()
    if thinking_level and thinking_level.lower() != "default":
        payload["thinking_level"] = thinking_level

    timeout = _stream_timeout(settings)

    streamed_content = ""
    streamed_thinking = ""

    async def _parse_buffer(buf: bytes) -> AsyncIterator[tuple[str, str]]:
        """Parse a single SSE line buffer and yield typed tokens."""
        nonlocal streamed_content, streamed_thinking
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
            # Router error can be a string or a dict with detailed info
            if isinstance(error, dict):
                error_msg = error.get("message") or error.get("detail") or str(error)
            else:
                error_msg = str(error)
            raise RuntimeError(error_msg)

        if metrics := data.get("metrics"):
            yield ("metrics", json.dumps(metrics))
            
        choices = data.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}

        # 1. API-level reasoning_content (DeepSeek / OpenRouter thinking models)
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if reasoning:
            streamed_thinking += reasoning
            yield ("thinking", reasoning)

        # 2. Normal content
        content = delta.get("content") or ""
        if content:
            streamed_content += content
            yield ("content", content)

        # Some Router providers finish a streamed response with an aggregate
        # message instead of one last delta. Emit only its missing suffix.
        message = choices[0].get("message") or {}
        final_reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if final_reasoning:
            if final_reasoning.startswith(streamed_thinking):
                reasoning_suffix = final_reasoning[len(streamed_thinking):]
                if reasoning_suffix:
                    streamed_thinking = final_reasoning
                    yield ("thinking", reasoning_suffix)
            elif not streamed_thinking:
                streamed_thinking = final_reasoning
                yield ("thinking", final_reasoning)

        final_content = message.get("content") or ""
        if final_content:
            # Treat the Router's aggregate message as authoritative. Chunked
            # deltas can end on a partial token or differ slightly after
            # provider normalization, so suffix matching alone is not enough.
            streamed_content = final_content
            yield ("content_snapshot", final_content)

    last_error = None
    async def _chunks_until_stopped(response):
        iterator = response.content.iter_any().__aiter__()
        pending = None
        try:
            while True:
                pending = asyncio.create_task(anext(iterator))
                while True:
                    ready, _ = await asyncio.wait({pending}, timeout=0.05)
                    if ready:
                        break
                    if stop_checker and await stop_checker():
                        # Drain a read that completed during the cancellation check.
                        if pending.done():
                            try:
                                yield pending.result()
                            except StopAsyncIteration:
                                pass
                        response.close()
                        return
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
                yield chunk
                # The entire received chunk is parsed before closing upstream.
                if stop_checker and await stop_checker():
                    response.close()
                    return
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    for url in urls:
        yielded_any = False
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise RuntimeError(f"HTTP {response.status} - {error_text}")
                
                raw_buffer = b""
                async for chunk in _chunks_until_stopped(response):
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
                
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            if yielded_any:
                # Veri göndermeye başladıktan sonra hata aldıysak fallback yapamayız, hatayı fırlat.
                raise
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")


async def llama_stream_chat(
    messages: list[dict[str, Any]],
    settings: RuntimeSettings,
    stop_checker: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncGenerator[str, None]:
    """Backward-compatible wrapper — yields only content tokens (thinking tokens are dropped).

    New code should prefer llama_stream_chat_typed().
    """
    async for kind, token in llama_stream_chat_typed(messages, settings, stop_checker=stop_checker):
        if kind == "content":
            yield token


# ---------------------------------------------------------------------------
#  LLM Chat - Non-Streaming (Synchronous)
# ---------------------------------------------------------------------------

async def llama_chat(messages: list[dict[str, Any]], settings: RuntimeSettings) -> str:
    """LLM sohbet isteğini tek seferde (non-streaming) yönlendirir."""
    session = await get_session()

    urls = _router_urls("/v1/chat/completions")
    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": False,
        "temperature": settings.temperature,
    }
    thinking_level = (getattr(settings, "thinking_level", "") or "").strip()
    if thinking_level and thinking_level.lower() != "default":
        payload["thinking_level"] = thinking_level

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
    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
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

def _tts_urls(path: str = "/v1/audio/speech") -> list[str]:
    """TTS servis endpointleri (Router öncelikli, fallback olarak doğrudan TTS servisi)."""
    urls: list[str] = []
    for base in get_router_base_urls():
        urls.append(f"{_base_url(base)}{path}")
    for base in get_tts_base_urls():
        urls.append(f"{_base_url(base)}{path}")
    return urls


async def generate_tts(
    text: str,
    settings: RuntimeSettings,
    voice: str | None = None,
    response_format: str = "wav",
) -> tuple[bytes, str, int | None]:
    """TTS üretimi için seçilen sağlayıcıya (öncelikle Orion Router) istek atar ve (audio_bytes, format, sample_rate) döner."""
    session = await get_session()

    raw_model = (getattr(settings, "tts_model", "") or "").strip()
    lowered_model = raw_model.lower()

    if lowered_model in ("", "none", "null", "default"):
        tts_model = "local-tts"
        provider = "local"
    else:
        # Use exact model specified by the user without auto-correcting
        tts_model = raw_model
        if "gemini" in lowered_model:
            provider = "gemini"
        elif "openai" in lowered_model or "tts-1" in lowered_model:
            provider = "openai"
        elif lowered_model in ("voxcpm", "voxcpm2", "local", "local-model", "local-tts"):
            provider = "local"
        else:
            raise ValueError(
                f"Desteklenmeyen TTS modeli: {tts_model}. "
                "Model adında gemini, openai veya local kullanın."
            )

    raw_voice = voice if voice is not None else getattr(settings, "tts_voice", "")
    voice_name = (raw_voice or "").strip()

    # Normalize voice: if empty, default, or mismatched cross-provider leftover (e.g. alloy on Gemini)
    if voice_name.lower() in ("default", "none", "null", ""):
        voice_name = None
    elif provider == "gemini" and voice_name.lower() == "alloy":
        voice_name = None
    elif provider == "local" and voice_name.lower() == "alloy":
        voice_name = None

    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
        "Content-Type": "application/json",
    }
    if provider:
        headers["x-orion-provider"] = provider

    payload = {
        "model": tts_model,
        "input": text,
        "response_format": response_format,
    }
    if voice_name:
        payload["voice"] = voice_name

    timeout_sec = getattr(settings, "tts_timeout_seconds", 15)
    timeout = _request_timeout(timeout_sec)

    router_urls = [f"{_base_url(base)}/v1/audio/speech" for base in get_router_base_urls()]
    local_tts_urls = [f"{_base_url(base)}/v1/audio/speech" for base in get_tts_base_urls()]

    # 1. Router Call
    router_err = None
    for url in router_urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.read()
                    sr_header = response.headers.get("X-Sample-Rate")
                    sample_rate = int(sr_header) if sr_header and sr_header.isdigit() else None
                    ct = response.headers.get("Content-Type", "")
                    fmt = "wav" if "wav" in ct else response_format
                    return data, fmt, sample_rate
                else:
                    err_text = await response.text()
                    try:
                        err_json = json.loads(err_text)
                        err_detail = err_json.get("detail") or err_text
                    except Exception:
                        err_detail = err_text
                    router_err = RuntimeError(f"Router TTS Hatası ({response.status}): {err_detail}")
                    # If Router responded with an error, DO NOT fall back to local TTS for non-local models!
                    if provider != "local":
                        raise router_err
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            router_err = e
            continue

    if provider != "local" and router_err:
        raise router_err

    # 2. Local TTS fallback (ONLY for local models like voxcpm2 when router is unreachable)
    if provider == "local":
        for url in local_tts_urls:
            try:
                async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.read()
                        sr_header = response.headers.get("X-Sample-Rate")
                        sample_rate = int(sr_header) if sr_header and sr_header.isdigit() else None
                        ct = response.headers.get("Content-Type", "")
                        fmt = "wav" if "wav" in ct else response_format
                        return data, fmt, sample_rate
            except Exception:
                continue

    if router_err:
        raise router_err
    raise RuntimeError("TTS servisine ulaşılamadı.")


