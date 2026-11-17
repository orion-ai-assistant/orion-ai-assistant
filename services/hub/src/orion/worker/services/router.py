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
import base64
import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Awaitable
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from services.shared.environment import get_router_base_urls
from orion.contracts.settings import RuntimeSettings
from orion.kernel.router_models import get_model_provider


def _get_router_api_key(settings: RuntimeSettings) -> str:
    """The requesting account's Router key. The Router administrator password is never a fallback."""
    key = (getattr(settings, "router_api_key", "") or "").strip()
    if key and key != "sk-60f3eaf169d7c485-0icocf-0a3db541":
        return key
    key = os.getenv("ROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Router erişimi için kullanıcı hesabıyla giriş yapın.")
    return key


# ---------------------------------------------------------------------------
#  Session Manager — Tek seferlik, paylaşımlı HTTP oturumu
# ---------------------------------------------------------------------------

_session: ClientSession | None = None


async def get_session() -> ClientSession:
    """Mevcut paylaşımlı ClientSession'ı döner; henüz yoksa oluşturur."""
    global _session
    if _session is None or _session.closed:
        from orion.kernel.router_bootstrap import bootstrap_local_router
        await bootstrap_local_router()
        from orion.kernel.pinned_router import router_session
        _session = router_session()
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
    # Model names are not provider identities: GPT and Gemini can be routed
    # through OpenRouter, and named groups can contain multiple providers.
    return None


async def _chat_headers(settings: RuntimeSettings) -> dict[str, str]:
    """Use identical provider routing for streaming replies and title requests."""
    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
        "Content-Type": "application/json",
    }
    provider = _chat_provider(settings.router_model_group)
    if provider is None:
        try:
            provider = await get_model_provider(settings.router_model_group, "chat")
        except RuntimeError:
            # Router can still resolve the model/group if its catalog is temporarily unavailable.
            provider = None
    if provider:
        headers["x-orion-provider"] = provider
    return headers


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
    tools: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """LLM streaming — thinking ve content tokenlarını ayrı ayrı yayınlar.

    Yields:
        ("thinking", token) — reasoning / chain-of-thought içeriği
        ("content",  token) — normal asistan cevabı
    """
    session = await get_session()

    urls = _router_urls("/v1/chat/completions")
    headers = await _chat_headers(settings)
    payload: dict[str, Any] = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": True,
    }
    if settings.temperature is not None:
        payload["temperature"] = settings.temperature
    thinking_level = (getattr(settings, "thinking_level", "") or "").strip()
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if thinking_level and thinking_level.lower() != "default":
        payload["thinking_level"] = thinking_level

    timeout = _stream_timeout(settings)

    streamed_content = ""
    streamed_thinking = ""
    from orion.worker.services.tool_stream import ToolCallAccumulator
    tool_accumulator = ToolCallAccumulator()

    async def _parse_buffer(buf: bytes) -> AsyncIterator[tuple[str, str]]:
        """Parse a single SSE line buffer and yield typed tokens."""
        nonlocal streamed_content, streamed_thinking
        line = buf.strip()
        if not line:
            return
        if line.startswith(b"data:"):
            line = line[len(b"data:"):].strip()
        if line == b"[DONE]":
            tool_accumulator.done = True
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
        if tool_accumulator.feed(choices[0]):
            # Marks upstream progress too: never retry another provider after a call fragment.
            yield ("tool_delta", "")

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
                        
                if not stop_checker or not await stop_checker():
                    calls = tool_accumulator.complete()
                    if calls:
                        yield ("tool_calls", json.dumps(calls))
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
    headers = await _chat_headers(settings)
    payload: dict[str, Any] = {
        "model": settings.router_model_group,
        "messages": messages,
        "stream": False,
    }
    if settings.temperature is not None:
        payload["temperature"] = settings.temperature
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

NATIVE_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/bmp", "image/gif"}


def to_png(data: bytes) -> bytes:
    """Any image Pillow reads, as PNG (alpha kept, palette/CMYK made RGB)."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.seek(0)
        frame = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
    out = io.BytesIO()
    frame.save(out, format="PNG")
    return out.getvalue()


def image_part(data: bytes, mime: str = "image/png") -> dict[str, Any]:
    """One image as an embedding content part (llama-server with an mmproj). llama-server decodes only what
    stb_image reads (JPEG, PNG, BMP, GIF) and hands the rest (WebP, AVIF, TIFF, HEIC...) to ffmpeg, which an Orion
    PC does not have ("failed to decode webp buffer"): those go as PNG (the first frame of an animation)."""
    if mime.lower() not in NATIVE_IMAGE_MIMES:
        data, mime = to_png(data), "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def audio_part(data: bytes, fmt: str = "wav") -> dict[str, Any]:
    """One audio clip as an embedding content part (needs an audio mmproj)."""
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "input_audio", "input_audio": {"data": encoded, "format": fmt}}


def embedding_input(content: "str | list[dict[str, Any]]") -> "str | list[dict[str, Any]]":
    """Request `input`: text stays a string, content parts nest one level
    deeper ({"content": [...]}). llama-server rejects parts placed directly
    in `input` and ignores media in the old /embedding route."""
    if isinstance(content, str):
        return content
    return [{"content": list(content)}]


async def generate_embeddings(
    text: "str | list[dict[str, Any]]", settings: RuntimeSettings
) -> list[float]:
    """Metin gömme (embedding) isteğini seçilen sağlayıcıya yönlendirir.

    `text` may also be a list of content parts (text/image_url/input_audio
    dicts, see image_part/audio_part) for multimodal embedding models."""
    return (await generate_embeddings_usage(text, settings))[0]


async def generate_embeddings_usage(
    text: "str | list[dict[str, Any]]", settings: RuntimeSettings
) -> tuple[list[float], int | None]:
    """generate_embeddings and the tokens the server says the input took (None when it does not say)."""
    session = await get_session()

    urls = _router_urls("/v1/embeddings")
    api_key = _get_router_api_key(settings)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-orion-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "input": embedding_input(text),
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
            tokens = (data.get("usage") or {}).get("prompt_tokens")
            return elements[0]["embedding"], int(tokens) if isinstance(tokens, (int, float)) and tokens > 0 else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("No valid router URL found.")


# Token estimate without the server's tokenizer (which the Router does not expose). Measured with llama.cpp b12378 on
# EmbeddingGemma 2 and Jina v5 (characters per token): English 4.5, Turkish 3.2-3.6, Russian 3.0-3.6, Arabic 2.5-2.8,
# Hindi 2.1-3.8, code 2.0-2.5, Korean 1.7-2.1, Japanese 1.5-2.2, Chinese 1.3-1.8. The estimate is deliberately low
# (too many tokens counted only shortens a piece; too few would make the server refuse the input).
EMBED_TOKENS_PER_CJK_CHAR = 1.0   # Han, kana, hangul: one token per character at worst.
EMBED_TOKENS_PER_CHAR = 0.55      # Everything else: about 1.8 characters per token.
EMBED_TOKEN_MARGIN = 32           # Start/end tokens and the estimate's error.


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (0x2E80 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF or 0x20000 <= code <= 0x2FFFF)


def estimate_tokens(text: str) -> int:
    return int(sum(EMBED_TOKENS_PER_CJK_CHAR if _is_cjk(c) else EMBED_TOKENS_PER_CHAR for c in text)) + 1


def _cut_by_tokens(text: str, budget: float, boundaries: tuple[str, ...]) -> int:
    """Index to cut `text` at: the last boundary in its second half before `budget` estimated tokens run out
    (the budget's end when there is none)."""
    spent, end = 0.0, len(text)
    for index, char in enumerate(text):
        spent += EMBED_TOKENS_PER_CJK_CHAR if _is_cjk(char) else EMBED_TOKENS_PER_CHAR
        if spent > budget:
            end = index
            break
    if end >= len(text):
        return len(text)
    window = text[:end]
    for mark in boundaries:
        found = window.rfind(mark)
        if found > end // 2:
            return found + len(mark)
    return max(end, 1)


_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "; ", " ")


def _costs(text: str) -> list[float]:
    """Running estimated tokens: costs[i] = tokens of text[:i] (estimate_tokens without its +1 and rounding)."""
    from itertools import accumulate
    return [0.0, *accumulate(EMBED_TOKENS_PER_CJK_CHAR if _is_cjk(c) else EMBED_TOKENS_PER_CHAR for c in text)]


def split_by_tokens(text: str, budget: float) -> list[str]:
    """Pieces of at most `budget` estimated tokens, cut at a paragraph, line, sentence or word boundary. Linear in
    the text's length (one running sum, cuts found by bisection): a 600k-character paragraph used to be re-measured
    from its start for every piece (minutes of CPU on the event loop)."""
    import bisect
    rest = text.strip()
    costs = _costs(rest)
    pieces: list[str] = []
    start, n = 0, len(rest)
    while start < n and int(costs[n] - costs[start]) + 1 > budget:
        # Up to the last character that still fits the budget (as _cut_by_tokens counts), from `start`.
        end = bisect.bisect_right(costs, costs[start] + budget, lo=start + 1) - 1
        end = min(max(end, start + 1), n)
        cut = end
        window = rest[start:end]
        half = (end - start) // 2
        for mark in _BOUNDARIES:
            found = window.rfind(mark)
            if found > half:
                cut = start + found + len(mark)
                break
        piece = rest[start:cut].strip()
        if piece:
            pieces.append(piece)
        start = cut
        while start < n and rest[start].isspace():
            start += 1
    if start < n:
        pieces.append(rest[start:].strip())
    return [piece for piece in pieces if piece]


def split_exact(text: str, budget: float) -> list[str]:
    """split_by_tokens without losing a character: pieces of at most `budget` estimated tokens cut at a paragraph,
    line, sentence or word boundary, nothing stripped, so "".join(pieces) == text exactly."""
    import bisect
    costs = _costs(text)
    pieces: list[str] = []
    start, n = 0, len(text)
    while start < n and int(costs[n] - costs[start]) + 1 > budget:
        end = bisect.bisect_right(costs, costs[start] + budget, lo=start + 1) - 1
        end = min(max(end, start + 1), n)
        cut = end
        window = text[start:end]
        half = (end - start) // 2
        for mark in _BOUNDARIES:
            found = window.rfind(mark)
            if found > half:
                cut = start + found + len(mark)
                break
        pieces.append(text[start:cut])
        start = cut
    if start < n:
        pieces.append(text[start:])
    return pieces


def chunk_text_exact(text: str, target_tokens: int = 400) -> list[str]:
    """chunk_text that loses nothing: whole paragraphs (each with its blank-line separator) are packed up to the
    target, a longer paragraph is split at sentences or lines (split_exact); the chunks' concatenation is `text`
    byte for byte (newlines, CRLF, any script)."""
    import re
    units, pos = [], 0
    for match in re.finditer(r"\n[ \t\r]*\n(?:[ \t\r]*\n)*", text):
        units.append(text[pos:match.end()])
        pos = match.end()
    if pos < len(text):
        units.append(text[pos:])
    chunks: list[str] = []
    current, current_tokens = "", 0
    for unit in units:
        for piece in split_exact(unit, target_tokens):
            tokens = estimate_tokens(piece)
            if current and current_tokens + tokens > target_tokens:
                chunks.append(current)
                current = ""
            current += piece
            current_tokens = estimate_tokens(current)
    if current:
        chunks.append(current)
    return chunks


def split_for_embedding(text: str, prefix: str, context_tokens: int) -> list[str]:
    """Pieces that fit the server's context together with the prefix. This is only the overflow guard; memory
    code should chunk its text first (chunk_text) so every piece is a meaningful unit."""
    budget = max(32, context_tokens - EMBED_TOKEN_MARGIN - estimate_tokens(prefix))
    return split_by_tokens(text, budget) or [""]


def chunk_text(text: str, target_tokens: int = 400) -> list[str]:
    """Meaningful chunks of about `target_tokens`: whole paragraphs are packed together up to the target, a longer
    paragraph is split at sentences. Use this before embedding what memory stores."""
    chunks: list[str] = []
    current, current_tokens = "", 0
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        for piece in split_by_tokens(paragraph, target_tokens):
            tokens = estimate_tokens(piece)
            if current and current_tokens + tokens > target_tokens:
                chunks.append(current)
                current = ""
            if current:
                current = f"{current}\n\n{piece}"
                current_tokens = estimate_tokens(current)  # Bounded: current stays under the target.
            else:
                current, current_tokens = piece, tokens
    if current:
        chunks.append(current)
    return chunks


async def embed_text(text: str, settings: RuntimeSettings, kind: str = "document", title: str = "") -> list[list[float]]:
    """One vector per piece of `text`. `kind` is "document" (what is stored) or "query" (what is searched with).
    The local model's prefix is added and text past its context is split; a cloud model gets the text as it is.
    `title` (documents only) goes into models whose document prefix takes one (EmbeddingGemma: "title: ... | text: ")."""
    try:
        local = await get_model_provider(settings.router_model_group, "embed") == "local"
    except Exception:
        local = False
    prefix, context = "", 0
    if local:
        from services.shared import local_parts
        profile = local_parts.embedding_profile()
        prefix, context = profile[f"{kind}_prefix"], profile["context"]
        clean = " ".join(title.split()).replace("|", "/")
        if clean and kind == "document" and prefix.startswith("title: none"):
            prefix = prefix.replace("title: none", f"title: {clean}", 1)
    pieces = split_for_embedding(text, prefix, context) if context else [text]
    return [await generate_embeddings(prefix + piece, settings) for piece in pieces]


# Backward Compatibility Alias
tei_embed = generate_embeddings


# ---------------------------------------------------------------------------
#  TTS (Text-to-Speech)
# ---------------------------------------------------------------------------

def _tts_urls(path: str = "/v1/audio/speech") -> list[str]:
    """TTS travels exclusively through the trusted HTTPS Router."""
    urls: list[str] = []
    for base in get_router_base_urls():
        urls.append(f"{_base_url(base)}{path}")
    return urls


async def _tts_request(
    text: str,
    settings: RuntimeSettings,
    voice: str | None = None,
    response_format: str = "wav",
):
    """Prepare the shared authenticated Router request for both TTS transports."""
    session = await get_session()

    raw_model = (getattr(settings, "tts_model", "") or "").strip()
    lowered_model = raw_model.lower()

    if lowered_model in ("", "none", "null", "default"):
        tts_model = "local-tts"
        provider = "local"
    else:
        # Use exact model specified by the user without auto-correcting.
        tts_model = raw_model
        try:
            provider = await get_model_provider(tts_model, "tts")
        except RuntimeError:
            if "gemini" in lowered_model:
                provider = "gemini"
            elif "openai" in lowered_model or "tts-1" in lowered_model:
                provider = "openai"
            elif lowered_model in ("voxcpm", "voxcpm2", "local", "local-model", "local-tts"):
                provider = "local"
            else:
                # As for chat: the Router can still resolve the model while the shared catalog is unavailable.
                provider = None

    # The voice saved for this model wins over the global one: a voice chosen for another engine (a Gemini voice
    # left in tts_voice) is not sent to this one. A model saved with no voice ("Varsayılan") uses its engine's own.
    model_voices = getattr(settings, "tts_model_voices", {}) or {}
    if voice is not None:
        raw_voice = voice
    elif tts_model in model_voices:
        raw_voice = model_voices[tts_model]
    else:
        raw_voice = getattr(settings, "tts_voice", "")
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
        # The Router session closes its own end after each response (force_close). Without this header it also
        # sends "Connection: close", and a Router on Python 3.11 then drops the tail of a large audio body when it
        # closes the TLS connection right after streaming it (about 4 of 100 parts, always cut at the same byte).
        "Connection": "keep-alive",
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
    if getattr(settings, "tts_language", ""):
        payload["language"] = settings.tts_language

    timeout_sec = getattr(settings, "tts_timeout_seconds", 15)
    timeout = _request_timeout(timeout_sec)

    router_urls = [f"{_base_url(base)}/v1/audio/speech" for base in get_router_base_urls()]

    return session, headers, payload, timeout, router_urls, provider


async def generate_tts(
    text: str, settings: RuntimeSettings, voice: str | None = None,
    response_format: str = "wav",
) -> tuple[bytes, str, int | None]:
    session, headers, payload, timeout, router_urls, provider = await _tts_request(
        text, settings, voice, response_format,
    )
    # 1. Router Call
    router_err = None
    for url in router_urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.read()
                    sr_header = response.headers.get("X-Sample-Rate")
                    sample_rate = int(sr_header) if sr_header and sr_header.isdigit() else None
                    ct = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    # Report the returned media, not just the requested format.
                    # Otherwise a provider's MP3/OGG response is sent to native
                    # players as audio/wav and may fail during preparation.
                    fmt = {
                        "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
                        "audio/vnd.wave": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3",
                        "audio/ogg": "ogg", "audio/opus": "opus", "audio/aac": "aac",
                        "audio/flac": "flac", "audio/x-flac": "flac", "audio/pcm": "pcm",
                    }.get(ct, response_format)
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

    if router_err:
        raise router_err
    raise RuntimeError("TTS servisine ulaşılamadı.")




class TtsStreamError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


async def stream_tts(
    text: str, settings: RuntimeSettings, voice: str | None = None,
) -> AsyncIterator[tuple[bytes, dict[str, Any]]]:
    """Stream verified PCM; preserve compressed/container responses for file playback.

    A transport failure may retry only before any bytes have been delivered.
    Closing this generator closes the HTTP response, including on cancellation.
    """
    session, headers, payload, timeout, urls, provider = await _tts_request(text, settings, voice, "pcm")
    # Request streaming from every provider. The response media type decides
    # whether to relay PCM immediately or collect a completed WAV batch. The
    # local TTS streams PCM from an engine that can (VoxCPM2) and answers with
    # one whole WAV from one that cannot (OmniVoice).
    payload["stream"] = True
    if provider not in ("gemini", "local"):
        payload["response_format"] = "wav"
    # The Router answers only when the first audio exists, and a provider under load took 20 s for one short sentence
    # (2026-10-31, Gemini 3.1 TTS). The wait grows with the text; the setting can only make it longer.
    wait = max(float(settings.tts_timeout_seconds), 30.0 + len(text) / 10)
    timeout = ClientTimeout(total=None, connect=1.0, sock_read=wait)
    started = False
    last_error = None
    for url in urls:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status != 200:
                    raw = await response.text()
                    try:
                        body = json.loads(raw)
                        detail = body.get("detail") or body.get("error", {}).get("message") or raw
                    except (ValueError, AttributeError):
                        detail = raw
                    raise TtsStreamError(f"Router TTS ({response.status}): {detail}", response.status)
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                if mime == "audio/pcm":
                    try:
                        rate = int(response.headers.get("X-Audio-Sample-Rate") or response.headers["X-Sample-Rate"])
                        local_pcm = provider == "local" and "X-Sample-Rate" in response.headers
                        channels = int(response.headers.get("X-Audio-Channels", "1" if local_pcm else ""))
                        bits = int(response.headers.get("X-Audio-Bits-Per-Sample", "16" if local_pcm else ""))
                    except (KeyError, ValueError) as exc:
                        raise TtsStreamError("PCM format headers missing or invalid") from exc
                    if not 8000 <= rate <= 192000 or channels not in (1, 2) or bits != 16:
                        raise TtsStreamError("Unsupported PCM format")
                    info = {"format": "pcm", "sample_rate": rate, "channels": channels,
                            "bits_per_sample": bits, "encoding": "pcm_s16le"}
                else:
                    fmt = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
                           "audio/mp3": "mp3", "audio/ogg": "ogg", "audio/aac": "aac",
                           "audio/flac": "flac"}.get(mime)
                    if not fmt:
                        raise TtsStreamError(f"Unsupported audio content type: {mime}")
                    info = {"format": fmt}
                from orion.worker.services.pcm_normalizer import PcmNormalizer
                normalizer = PcmNormalizer(rate, channels) if mime == "audio/pcm" and (rate, channels) != (24000, 1) else None
                if normalizer:
                    info = {"format": "pcm", "sample_rate": 24000, "channels": 1,
                            "bits_per_sample": 16, "encoding": "pcm_s16le"}
                async for chunk in response.content.iter_chunked(4096):
                    if chunk:
                        started = True
                        audio = normalizer.feed(chunk) if normalizer else chunk
                        if audio:
                            yield audio, info
                if normalizer:
                    audio = normalizer.feed(b"", final=True)
                    if audio:
                        yield audio, info
                if not started:
                    raise TtsStreamError("TTS returned no audio")
                return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            if started:
                raise
            last_error = exc
    raise TtsStreamError(f"TTS service unavailable: {last_error!r}") from last_error
