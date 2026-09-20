import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from redis.asyncio import Redis

from orion.contracts.constants import CHAT_HISTORY_KEY_PREFIX, CHAT_STATE_KEY_PREFIX, CHAT_STOP_KEY_PREFIX, HISTORY_CACHE_TTL
from orion.kernel.config import RuntimeSettings, get_runtime_settings
from orion.kernel.registry import insert_messages, get_chat_history_db
from orion.worker.infra.context import JobContext
from orion.worker.services.router import llama_stream_chat_typed, generate_tts


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tokenize(text: str) -> list[str]:
    """Tokenize text preserving exact spaces and newlines."""
    if not text:
        return []
    tokens = re.split(r'(\s+)', text)
    return [t for t in tokens if t]


def _clean_text_for_tts(text: str) -> str:
    """Clean markdown and code blocks for more natural speech synthesis."""
    if not text:
        return ""
    # Remove code blocks ```...```
    cleaned = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code `...`
    cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
    # Remove markdown headers (#, ##, etc.)
    cleaned = re.sub(r'^\s*#+\s*', '', cleaned, flags=re.MULTILINE)
    # Remove markdown bold/italic markers (*, _)
    cleaned = re.sub(r'[*_]{1,3}', '', cleaned)
    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)
    # Clean excessive whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or text.strip()



def _format_router_fallback_message(error: Exception, settings: RuntimeSettings) -> str:
    err_str = str(error)
    err_lower = err_str.lower()

    is_conn_error = (
        isinstance(error, (aiohttp.ClientConnectorError, ConnectionRefusedError, OSError))
        or "cannot connect" in err_lower
        or "connection refused" in err_lower
        or "no valid router url" in err_lower
        or "connect call failed" in err_lower
    )

    if is_conn_error:
        return (
            "⚠️ **Yönlendirici (Router) veya AI Modeline Bağlanılamadı**\n\n"
            "Orion Hub sisteminiz aktif ve mesajınızı kabul etti; ancak yanıt oluşturacak **Yönlendirici (Router)** veya **LLM** servisi şu anda çalışmıyor.\n\n"
            "📊 **Servis Durumu:**\n"
            "- **Orion Hub:** ✅ Çevrimiçi\n"
            "- **Veritabanı / Redis:** ✅ Bağlı\n"
            "- **AI Servisi (Router/LLM):** ❌ Kapalı / Çevrimdışı\n\n"
            "💡 **Ne Yapabilirsiniz?**\n"
            "1. Installer kontrol panelinden veya terminalden **LLM** veya **Router** servisini başlatın.\n"
            "2. Servis aktif olduğunda mesajınızı tekrar iletebilirsiniz.\n\n"
            f"> *Teknik Detay: {err_str}*"
        )
    elif isinstance(error, asyncio.TimeoutError) or "timeout" in err_lower:
        return (
            "⚠️ **Model Yanıt Zaman Aşımı**\n\n"
            f"AI modeline bağlanıldı ancak belirlenen süre içinde ({settings.llm_timeout_seconds}s) yanıt alınamadı.\n"
            "Model yüksek yük altında veya henüz hazır olmayabilir. Lütfen bir süre sonra tekrar deneyin.\n\n"
            f"> *Teknik Detay: {err_str}*"
        )
    else:
        return (
            "⚠️ **Yönlendirici (Router) Hatası:**\n\n"
            f"```text\n{err_str}\n```\n"
            "Lütfen servis loglarını kontrol edin veya modeli yeniden başlatın."
        )


def fake_completion(prompt: str) -> str:
    return (
        "This is a simulated model response for Phase 1. "
        "Your message was accepted and processed through Redis streams and SSE delivery. "
        f"Original prompt: {prompt}"
    )


def _history_key(chat_id: str) -> str:
    return f"{CHAT_HISTORY_KEY_PREFIX}{chat_id}"


async def load_history(redis: Redis, chat_id: str, max_messages: int) -> list[dict[str, Any]]:
    """Return the recent message history for a chat, with Cache-Aside hydration.

    1. Try Redis first (fast path).
    2. On cache miss, fall back to PostgreSQL and hydrate Redis so subsequent
       calls within the TTL window hit the cache.
    """
    if max_messages <= 0:
        return []

    key = _history_key(chat_id)
    raw_items = await redis.lrange(key, -max_messages, -1)

    # Cache Hit
    if raw_items:
        history: list[dict[str, Any]] = []
        for item in raw_items:
            try:
                data = json.loads(item)
            except json.JSONDecodeError:
                continue
            role = data.get("role")
            content = data.get("content")
            if role and content:
                history.append({"role": role, "content": content})
        return history

    # Cache Miss — hydrate from PostgreSQL
    logging.info("Worker cache miss for chat history %s — hydrating from PostgreSQL", chat_id)
    try:
        db_history = await get_chat_history_db(chat_id)
    except Exception:
        logging.exception("get_chat_history_db failed for chat %s — returning empty history", chat_id)
        return []

    if db_history:
        pipe = redis.pipeline()
        for msg in db_history:
            pipe.rpush(key, json.dumps(msg))
        pipe.expire(key, HISTORY_CACHE_TTL)
        await pipe.execute()
        logging.info(
            "Hydrated %d messages into Redis for chat %s (TTL=%ds)",
            len(db_history), chat_id, HISTORY_CACHE_TTL,
        )

    # Return only the last max_messages entries
    return [
        msg for msg in db_history[-max_messages:]
        if msg.get("role") and msg.get("content")
    ]


async def append_history(
    redis: Redis,
    chat_id: str,
    messages: list[dict[str, Any]],
    max_messages: int,
    settings: RuntimeSettings,
) -> None:
    """Append messages using Write-Through: Redis (hot cache) + PostgreSQL (durable store).

    Redis receives the write first to keep the active UI responsive.
    The DB write is fire-and-forget inside a try-except so a transient DB
    failure never interrupts the ongoing chat stream.
    """
    if not messages or max_messages <= 0:
        return

    # 1. Write to Redis (hot cache — 1-hour TTL for active sessions)
    key = _history_key(chat_id)
    pipe = redis.pipeline()
    for message in messages:
        pipe.rpush(key, json.dumps(message))
    pipe.ltrim(key, -max_messages, -1)
    pipe.expire(key, HISTORY_CACHE_TTL)
    await pipe.execute()

    # 2. Write-Through: persist to PostgreSQL
    try:
        await insert_messages(chat_id=chat_id, messages=messages)
    except Exception:
        # Non-fatal: messages are already in Redis and the stream continues.
        # The missing DB write will be visible in logs for investigation.
        logging.exception(
            "Write-Through insert_messages failed for chat %s — messages are in Redis but not persisted to DB",
            chat_id,
        )


async def should_stop(redis: Redis, chat_id: str) -> bool:
    stop_key = f"{CHAT_STOP_KEY_PREFIX}{chat_id}"
    return bool(await redis.exists(stop_key))


async def process_message(redis: Redis, stream_id: str, fields: dict[str, str], consumer_name: str) -> None:
    context = JobContext.from_stream_fields(fields, redis)
    settings = await get_runtime_settings(redis, context.user_id)

    logging.info(
        "Worker %s picked chat %s channel=%s prompt=%s",
        consumer_name,
        context.chat_id,
        context.channel,
        context.prompt[:60],
    )

    state_key = f"{CHAT_STATE_KEY_PREFIX}{context.chat_id}"

    await redis.hset(
        state_key,
        mapping={
            "status": "processing",
            "updated_at": utc_now(),
            "worker_id": consumer_name,
            "current_prompt": context.prompt,
            "partial_text": "",
            "partial_thinking": "",
        },
    )

    try:
        output_tokens: list[str] = []
        thinking_tokens: list[str] = []

        if context.stream_mode == "continuous":
            # --- Continuous (demo) mode ---
            iteration = 0
            while True:
                if await should_stop(redis, context.chat_id):
                    await redis.hset(
                        state_key,
                        mapping={
                            "status": "stopped",
                            "updated_at": utc_now(),
                            "result_json": json.dumps({"text": "".join(output_tokens), "stopped": True}),
                        },
                    )
                    await context.emit_done("stopped")
                    logging.info("Worker %s stopped continuous chat %s", consumer_name, context.chat_id)
                    break

                iteration += 1
                text = f"continuous stream item {iteration} from prompt: {context.prompt}"
                for token in tokenize(text):
                    output_tokens.append(token)
                    await context.emit_token(token)
                    logging.info("Worker %s published token for chat %s: %s", consumer_name, context.chat_id, token)
                    if settings.token_delay_ms > 0:
                        await asyncio.sleep(settings.token_delay_ms / 1000)
        else:
            # --- Single-shot mode: stream directly from LLM ---
            history = await load_history(redis, context.chat_id, settings.chat_history_max_messages)
            messages: list[dict[str, Any]] = []
            if settings.system_prompt:
                messages.append({"role": "system", "content": settings.system_prompt})
            messages.extend(history)

            # Multimodal support
            if context.images:
                user_content: list[dict[str, Any]] = []
                for img in context.images:
                    img_url = img if img.startswith("data:") or img.startswith("http") else f"data:image/jpeg;base64,{img}"
                    user_content.append({"type": "text", "text": "<audio>"})
                    user_content.append({"type": "image_url", "image_url": {"url": img_url}})
                    user_content.append({"type": "text", "text": "</audio>\n"})
                user_content.append({"type": "text", "text": context.prompt})
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": context.prompt})

            user_message = messages[-1]

            # Stream tokens from LLM.
            # - token_delay_ms intentionally NOT applied here: LLM already provides natural pacing.
            #   Applying extra delay here would keep the stop button visible long after the LLM is done.
            # - Stop signal is checked BEFORE each token. If the LLM has already finished
            #   (llm_done=True), we never call aclose() — the stream is already exhausted.
            first_token = True
            stopped = False
            llm_done = False
            start_perf = time.perf_counter()
            first_token_ms: int | None = None

            try:
                async def _check_stop() -> bool:
                    return await should_stop(redis, context.chat_id)

                stream = llama_stream_chat_typed(messages, settings, stop_checker=_check_stop)
                async for kind, token in stream:
                    now_perf = time.perf_counter()
                    if first_token_ms is None:
                        first_token_ms = max(1, int((now_perf - start_perf) * 1000))

                    # First token delay (optional artificial pre-roll)
                    if first_token and settings.first_token_delay_ms > 0:
                        await asyncio.sleep(settings.first_token_delay_ms / 1000)
                    first_token = False

                    if kind == "thinking":
                        thinking_tokens.append(token)
                        await context.emit_thinking(token)
                        logging.info("Worker %s thinking token for chat %s", consumer_name, context.chat_id)
                        await redis.hset(state_key, "partial_thinking", "".join(thinking_tokens))
                    else:
                        output_tokens.append(token)
                        await context.emit_token(token)
                        logging.info("Worker %s published token for chat %s: %s", consumer_name, context.chat_id, token.rstrip())
                        # Flush partial text to Redis on every token for reconnect support
                        await redis.hset(state_key, "partial_text", "".join(output_tokens))

                # Akış bittiğinde durdurma sinyali verilmiş miydi kontrol et
                if await should_stop(redis, context.chat_id):
                    stopped = True
                    logging.info("Worker %s: LLM stream stopped upon user stop signal for chat %s", consumer_name, context.chat_id)
                else:
                    llm_done = True

            except Exception as e:
                logging.exception("LLM stream failed for chat %s; sending error to user", context.chat_id)
                fallback_text = _format_router_fallback_message(e, settings)
                output_tokens.append(fallback_text)
                await context.emit_token(fallback_text)
                await redis.hset(state_key, "partial_text", "".join(output_tokens))
                logging.info("Worker %s published error fallback immediately for chat %s", consumer_name, context.chat_id)

            total_ms = max(1, int((time.perf_counter() - start_perf) * 1000))
            metrics = {
                "first_token_ms": first_token_ms if first_token_ms is not None else total_ms,
                "total_ms": total_ms,
            }

            final_text = "".join(output_tokens)

            # Determine final status:
            # - llm_done=True  → LLM exhausted naturally; treat as completed regardless of stop key
            #   (stop was pressed after LLM finished — nothing to abort, response is complete)
            # - stopped=True   → Stop pressed while LLM was mid-generation; treat as stopped
            is_completed = llm_done or not stopped

            # --- Multimodal: Text-to-Speech (TTS) Integration ---
            is_audio_requested = context.audio_requested if context.request.input.audio is not None else True
            should_tts = settings.tts_enabled and is_audio_requested and bool(final_text.strip())
            if should_tts and not stopped:
                try:
                    tts_text = _clean_text_for_tts(final_text)
                    if tts_text:
                        logging.info(
                            "Worker %s generating TTS for chat %s (%d chars)",
                            consumer_name, context.chat_id, len(tts_text),
                        )
                        audio_bytes, audio_fmt, sample_rate = await generate_tts(
                            text=tts_text,
                            settings=settings,
                            voice=context.voice,
                        )
                        await context.emit_audio(
                            audio_data=audio_bytes,
                            format=audio_fmt,
                            sample_rate=sample_rate,
                            text=tts_text,
                        )
                        logging.info(
                            "Worker %s successfully emitted audio for chat %s (%d bytes)",
                            consumer_name, context.chat_id, len(audio_bytes),
                        )
                except Exception as tts_err:
                    # Graceful degradation: log and inform user via chat token
                    logging.warning(
                        "Worker %s: TTS generation failed for chat %s (%s)",
                        consumer_name, context.chat_id, tts_err,
                    )
                    try:
                        await context.emit_token(f"\n\n*[⚠️ TTS Uyarısı: {str(tts_err)}]*")
                    except Exception:
                        pass

            thinking_text = "".join(thinking_tokens)
            assistant_entry = {"role": "assistant", "content": final_text, "metrics": metrics}
            if thinking_text:
                assistant_entry["thinking"] = thinking_text

            await append_history(
                redis,
                context.chat_id,
                [user_message, assistant_entry],
                settings.chat_history_max_messages,
                settings,
            )

            if is_completed:
                await redis.hset(
                    state_key,
                    mapping={
                        "status": "completed",
                        "updated_at": utc_now(),
                        "result_json": json.dumps({"text": final_text, "metrics": metrics}),
                    },
                )
                await redis.expire(state_key, settings.result_ttl_seconds)
                await context.emit_done("completed", metrics=metrics)
                logging.info(
                    "Worker %s completed chat %s (TTFT: %dms, Total: %dms)",
                    consumer_name, context.chat_id, metrics["first_token_ms"], metrics["total_ms"]
                )
            else:
                await redis.hset(
                    state_key,
                    mapping={
                        "status": "stopped",
                        "updated_at": utc_now(),
                        "result_json": json.dumps({"text": final_text, "stopped": True, "metrics": metrics}),
                    },
                )
                await redis.expire(state_key, settings.result_ttl_seconds)
                await context.emit_done("stopped", metrics=metrics)
                logging.info(
                    "Worker %s stopped (mid-generation) chat %s (TTFT: %dms, Total: %dms)",
                    consumer_name, context.chat_id, metrics["first_token_ms"], metrics["total_ms"]
                )

        if context.stream_mode == "continuous":
            await redis.expire(state_key, settings.result_ttl_seconds)

        stop_key = f"{CHAT_STOP_KEY_PREFIX}{context.chat_id}"
        await redis.delete(stop_key)

    except Exception as exc:
        await redis.hset(
            state_key,
            mapping={
                "status": "failed",
                "updated_at": utc_now(),
                "error": str(exc),
            },
        )
        await context.emit_error(str(exc))
        logging.exception("Worker %s failed chat %s", consumer_name, context.chat_id)
