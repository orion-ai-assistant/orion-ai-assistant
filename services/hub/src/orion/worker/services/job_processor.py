import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from redis.asyncio import Redis
from redis.exceptions import WatchError

from orion.contracts.constants import ACTIVE_TURN_KEY_PREFIX, CHAT_STATE_KEY_PREFIX, TURN_STOP_KEY_PREFIX
from orion.kernel.config import RuntimeSettings, get_runtime_settings
from orion.kernel.registry import insert_messages, get_chat_history_db
from orion.worker.infra.context import JobContext
from orion.worker.services.chat_titles import generate_chat_title
from orion.worker.services.attachments import user_content
from orion.worker.services.router import llama_stream_chat_typed
from orion.worker.services.adaptive_tts import AdaptiveTts
from orion.worker.services.tool_runner import ToolConversation
from orion.worker.services.tool_history import HISTORY_SLIDE_TURNS, model_history
from orion.worker.services.tool_display import DisplayTimeline
from orion.kernel.media_store import hydrate_model_media
from orion.contracts.events import StreamEvent
from orion.kernel.job_safety import job_key, WAITING_KEY, TERMINAL_TTL


def _tts_reason(error: object) -> str:
    """Why speech failed, for the client's notice: the TTS service's own message when the error carries one
    ("Böyle bir ses klonu bulunamadı: ..."), else the error itself, short."""
    import json, re
    text = str(error or "")
    # The Router may wrap the service's JSON in its own ({"error": "... {\"detail\": ...}"}): unwrap twice.
    for _ in range(2):
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            break
        try:
            body = json.loads(match.group(0))
        except ValueError:
            break
        inner = body.get("detail") or body.get("error") if isinstance(body, dict) else None
        if not isinstance(inner, str) or not inner.strip():
            break
        text = inner
    return text.strip()[:300]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tokenize(text: str) -> list[str]:
    """Tokenize text preserving exact spaces and newlines."""
    if not text:
        return []
    tokens = re.split(r'(\s+)', text)
    return [t for t in tokens if t]


def _format_router_fallback_message(error: Exception, settings: RuntimeSettings) -> str:
    err_str = str(error)
    err_lower = err_str.lower()

    if "openrouter http error 401" in err_lower:
        return (
            "OpenRouter kimlik doğrulaması başarısız (401). "
            "Hub isteği Orion Router’a ulaştı; OpenRouter, Router’ın kullandığı sağlayıcı anahtarını kabul etmedi.\n\n"
            "Orion Router panelindeki OpenRouter anahtar havuzunu kontrol edin: "
            "geçersiz veya şifresi çözülemeyen kaydı düzeltin ya da devre dışı bırakın. "
            "Hub’daki Router API anahtarı, OpenRouter sağlayıcı anahtarından ayrıdır."
        )

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
            f"```text\n{err_str}\n```"
        )


def fake_completion(prompt: str) -> str:
    return (
        "This is a simulated model response for Phase 1. "
        "Your message was accepted and processed through Redis streams and SSE delivery. "
        f"Original prompt: {prompt}"
    )


def _remove_failed_turns(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from orion.kernel.chat_history import successful_history
    return successful_history(history)


async def load_history(
    redis: Redis,
    chat_id: str,
    max_messages: int,
    cache_ttl_seconds: int,
) -> list[dict[str, Any]]:
    """The model's view of a chat's history, read from PostgreSQL (see job_service._load_history).

    The whole chat is read, not its tail: model_history counts turns from the chat's start so that
    consecutive requests share their prefix (the provider's prompt cache).
    """
    if max_messages <= 0:
        return []
    try:
        history = [message for message in await get_chat_history_db(chat_id) if message.get("role")]
    except Exception:
        logging.exception("get_chat_history_db failed for chat %s — returning empty history", chat_id)
        return []
    return await hydrate_model_media(chat_id, model_history(_remove_failed_turns(history), max_messages, HISTORY_SLIDE_TURNS))


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
    turn_id = messages[0].get('turn_id')
    if turn_id and not await redis.hsetnx(job_key(turn_id), 'history_saved', '1'):
        # A newer turn replaced this one and already saved it.
        logging.info('History of turn %s in chat %s is already saved', turn_id, chat_id)
        return

    # The live relay (including audio_done) has already completed. Compress only
    # the history copy here, so encoding cannot delay the PCM stream boundary.
    from orion.kernel.media_store import store_audio
    messages = [dict(message) for message in messages]
    for message in messages:
        if isinstance(message.get('audio'), dict) and message['audio'].get('audio'):
            try:
                message['audio'] = await store_audio(chat_id, message['audio'])
            except Exception:
                logging.exception('TTS media persistence failed; retaining inline history fallback')

    # PostgreSQL is the only record: a short outage (its supervisor restarting it) is waited out before the
    # turn is given up as unsaved. The turn stays "processing" until this returns, so no client reads a
    # history that misses it.
    for attempt, pause in enumerate((0.5, 1.0, 2.0, 4.0, None)):
        try:
            await insert_messages(chat_id=chat_id, messages=messages)
            return
        except Exception:
            if pause is None:
                logging.exception("Turn of chat %s could not be saved after %d tries", chat_id, attempt + 1)
                return
            logging.warning("Saving the turn of chat %s failed; trying again in %.1f s", chat_id, pause)
            await asyncio.sleep(pause)


def _redis_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _active_turn_key(chat_id: str) -> str:
    return f"{ACTIVE_TURN_KEY_PREFIX}{chat_id}"


def _turn_stop_key(turn_id: str) -> str:
    return f"{TURN_STOP_KEY_PREFIX}{turn_id}"


async def is_active_turn(redis: Redis, chat_id: str, turn_id: str) -> bool:
    current = _redis_text(await redis.get(_active_turn_key(chat_id)))
    return current == turn_id


async def should_stop(redis: Redis, turn_id: str) -> bool:
    stop_key = _turn_stop_key(turn_id)
    return bool(await redis.exists(stop_key))


async def hset_if_active(redis: Redis, chat_id: str, turn_id: str, state_key: str, field: str, value: str) -> bool:
    active_key = _active_turn_key(chat_id)
    async with redis.pipeline() as pipe:
        try:
            await pipe.watch(active_key)
            current = _redis_text(await pipe.get(active_key))
            if current != turn_id:
                await pipe.reset()
                return False
            pipe.multi()
            pipe.hset(state_key, field, value)
            await pipe.execute()
            return True
        except WatchError:
            return False


async def finalize_state_if_active(
    redis: Redis,
    chat_id: str,
    turn_id: str,
    state_key: str,
    mapping: dict[str, Any],
    ttl_seconds: int,
) -> bool:
    active_key = _active_turn_key(chat_id)
    async with redis.pipeline() as pipe:
        try:
            await pipe.watch(active_key)
            current = _redis_text(await pipe.get(active_key))
            if current != turn_id:
                await pipe.reset()
                return False
            pipe.multi()
            pipe.hset(state_key, mapping=mapping)
            pipe.expire(state_key, ttl_seconds)
            pipe.hset(job_key(turn_id), mapping={
                'phase': 'terminal', 'status': mapping['status'],
                'updated_at': mapping['updated_at'], 'error': mapping.get('error', ''),
                'result_json': mapping.get('result_json', '{}'),
            })
            pipe.expire(job_key(turn_id), TERMINAL_TTL)
            pipe.zrem(WAITING_KEY, turn_id)
            pipe.delete(active_key)
            pipe.delete(_turn_stop_key(turn_id))
            await pipe.execute()
            return True
        except WatchError:
            return False


async def process_message(redis: Redis, stream_id: str, fields: dict[str, str], consumer_name: str) -> None:
    context = JobContext.from_stream_fields(fields, redis)
    settings = await get_runtime_settings(redis, context.user_id)

    logging.info(
        "Worker %s picked chat %s turn=%s channel=%s prompt=%s",
        consumer_name,
        context.chat_id,
        context.turn_id,
        context.channel,
        context.prompt[:60],
    )

    state_key = f"{CHAT_STATE_KEY_PREFIX}{context.chat_id}"

    if not await is_active_turn(redis, context.chat_id, context.turn_id):
        logging.info(
            "Worker %s skipping stale turn %s for chat %s",
            consumer_name,
            context.turn_id,
            context.chat_id,
        )
        # A newer turn superseded it; every device still shows it as pending.
        await context.emit_done("stopped")
        return

    await redis.hset(
        state_key,
        mapping={
            "status": "processing",
            "updated_at": utc_now(),
            "worker_id": consumer_name,
            "active_turn_id": context.turn_id,
            "current_prompt": context.prompt,
            "partial_text": "",
            "partial_thinking": "",
            "partial_tools": "[]",
            "partial_display": "[]",
        },
    )

    adaptive_tts = None
    # No "audio" field (the plain chat) means speak, in both speech modes.
    is_audio_requested = context.audio_requested if context.request.input.audio is not None else True
    try:
        output_tokens: list[str] = []
        thinking_tokens: list[str] = []
        timeline = DisplayTimeline()
        display_parts = timeline.parts
        add_display = timeline.add

        if context.stream_mode == "continuous":
            raise ValueError("Continuous demo mode is disabled; use stream_mode=once for model responses.")
        else:
            # --- Single-shot mode: stream directly from LLM ---
            history = await load_history(
                redis,
                context.chat_id,
                settings.chat_history_max_messages,
                settings.redis_cache_ttl_seconds,
            )
            messages: list[dict[str, Any]] = []
            if settings.system_prompt:
                messages.append({"role": "system", "content": settings.system_prompt})
            messages.extend(history)

            current = {'role': 'user', 'content': user_content(context.request.input)}
            messages.extend(await hydrate_model_media(context.chat_id, [current]))

            user_message = {**messages[-1], "turn_id": context.turn_id}
            attachments = context.request.input.display_attachments()
            if attachments:
                user_message["attachments"] = attachments
                user_message["display_text"] = context.request.input.metadata.get("display_text", context.prompt)
                # Rebuild model parts from references on the next turn, not from a
                # second persisted copy of the media in content.
                user_message['content'] = context.prompt
            conversation = ToolConversation(context, json.loads(context.record.enabled_tools))

            # Stream tokens from LLM.
            # - token_delay_ms intentionally NOT applied here: LLM already provides natural pacing.
            #   Applying extra delay here would keep the stop button visible long after the LLM is done.
            # - Stop signal is checked BEFORE each token. If the LLM has already finished
            #   (llm_done=True), we never call aclose() — the stream is already exhausted.
            first_token = True
            stopped = False
            llm_done = False
            router_metrics: dict[str, Any] | None = None
            generation_started_at = time.perf_counter()
            first_token_at: float | None = None

            next_renewal = 0.0
            stop_check_lock = asyncio.Lock()
            try:
                async def _check_stop_unlocked() -> bool:
                    nonlocal next_renewal
                    if time.monotonic() >= next_renewal:
                        active_key = _active_turn_key(context.chat_id)
                        async with redis.pipeline() as pipe:
                            try:
                                await pipe.watch(active_key)
                                if _redis_text(await pipe.get(active_key)) != context.turn_id:
                                    return True
                                pipe.multi()
                                pipe.expire(active_key, max(settings.llm_timeout_seconds + settings.stop_key_ttl_seconds, 120))
                                await pipe.execute()
                            except WatchError:
                                return True
                        next_renewal = time.monotonic() + 30
                    return (
                        await should_stop(redis, context.turn_id)
                        or not await is_active_turn(redis, context.chat_id, context.turn_id)
                    )

                async def _check_stop():
                    # LLM and concurrent TTS must not WATCH/renew the same key simultaneously.
                    async with stop_check_lock:
                        return await _check_stop_unlocked()

                def arrival_ms():
                    requested_at = datetime.fromisoformat(context.record.created_at)
                    if requested_at.tzinfo is None:
                        requested_at = requested_at.replace(tzinfo=timezone.utc)
                    return max(1, round((datetime.now(timezone.utc) - requested_at).total_seconds() * 1000))

                # Every spoken reply starts while the model is still writing (assistant and plain chat alike).
                if settings.tts_enabled and is_audio_requested:
                    adaptive_tts = AdaptiveTts(context, settings, _check_stop, arrival_ms)

                stream = conversation.stream(messages, settings, _check_stop, llama_stream_chat_typed)
                token_count = 0
                thinking_token_count = 0
                content_token_count = 0
                
                logging.info("Worker %s: starting stream for chat %s", consumer_name, context.chat_id)
                async for kind, token in stream:
                    if kind in ("tool_call", "tool_result"):
                        if kind == "tool_call":
                            add_display("tool_call", json.loads(token)["call_id"])
                        if await hset_if_active(redis, context.chat_id, context.turn_id, state_key,
                                                "partial_tools", json.dumps(conversation.activity)):
                            await context._publish(StreamEvent(type=kind, chat_id=context.chat_id,
                                turn_id=context.turn_id, generation_id=context.turn_id, data=json.loads(token)))
                        await hset_if_active(redis, context.chat_id, context.turn_id, state_key,
                                             "partial_display", json.dumps(display_parts))
                        continue
                    token_count += 1
                    if kind == "metrics":
                        try:
                            router_metrics = json.loads(token)
                            logging.info("Worker %s received router metrics for chat %s: %s", consumer_name, context.chat_id, router_metrics)
                        except Exception:
                            pass
                        continue

                    if not await is_active_turn(redis, context.chat_id, context.turn_id):
                        stopped = True
                        logging.info(
                            "Worker %s: stopping stream for chat %s turn %s",
                            consumer_name,
                            context.chat_id,
                            context.turn_id,
                        )
                        break

                    # First token delay (optional artificial pre-roll)
                    if first_token and settings.first_token_delay_ms > 0:
                        await asyncio.sleep(settings.first_token_delay_ms / 1000)
                    first_token = False

                    if kind == "thinking":
                        add_display("thinking", token)
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        thinking_token_count += 1
                        thinking_tokens.append(token)
                        await context.emit_thinking(token)
                        # Log only first thinking token to avoid spam
                        if thinking_token_count == 1:
                            logging.info("Worker %s: first thinking token for chat %s", consumer_name, context.chat_id)
                        await hset_if_active(
                            redis,
                            context.chat_id,
                            context.turn_id,
                            state_key,
                            "partial_thinking",
                            "".join(thinking_tokens),
                        )
                    elif kind == "content":
                        add_display("content", token)
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        content_token_count += 1
                        output_tokens.append(token)
                        await context.emit_token(token)
                        if adaptive_tts:
                            await adaptive_tts.feed(token)
                        # Log only first content token to avoid spam
                        if content_token_count == 1:
                            logging.info("Worker %s: first content token for chat %s", consumer_name, context.chat_id)
                        # Flush partial text to Redis on every token for reconnect support
                        await hset_if_active(
                            redis,
                            context.chat_id,
                            context.turn_id,
                            state_key,
                            "partial_text",
                            "".join(output_tokens),
                        )
                    elif kind == "content_snapshot":
                        add_display("snapshot", token)
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        output_tokens[:] = [token]
                        await context.emit_snapshot(token)
                        if adaptive_tts:
                            await adaptive_tts.feed(token, snapshot=True)
                        await hset_if_active(
                            redis,
                            context.chat_id,
                            context.turn_id,
                            state_key,
                            "partial_text",
                            token,
                        )

                    if kind in ("thinking", "content", "content_snapshot"):
                        await hset_if_active(redis, context.chat_id, context.turn_id, state_key,
                                             "partial_display", json.dumps(display_parts))

                # Stream ended naturally — log final statistics
                logging.info(
                    "Worker %s: stream finished for chat %s — total_chunks=%d thinking_tokens=%d output_tokens=%d",
                    consumer_name, context.chat_id, token_count, len(thinking_tokens), len(output_tokens)
                )
                
                # Akış bittiğinde durdurma sinyali verilmiş miydi kontrol et
                stop_exists_after = await should_stop(redis, context.turn_id)
                active_after = await is_active_turn(redis, context.chat_id, context.turn_id)
                if stop_exists_after or not active_after:
                    stopped = True
                    logging.info(
                        "Worker %s: stop key PRESENT after stream for chat %s — treating as stopped",
                        consumer_name, context.chat_id
                    )
                else:
                    llm_done = True
                    logging.info(
                        "Worker %s: stream completed naturally for chat %s — no stop key",
                        consumer_name, context.chat_id
                    )

            except Exception as e:
                logging.exception("LLM stream failed for chat %s; sending error to user", context.chat_id)
                fallback_text = _format_router_fallback_message(e, settings)
                failure_total_ms = max(1, round((time.perf_counter() - generation_started_at) * 1000))
                failure_metrics = {
                    "first_token_ms": max(
                        1,
                        round((first_token_at - generation_started_at) * 1000),
                    ) if first_token_at is not None else None,
                    "total_ms": failure_total_ms,
                }
                failed_state = await redis.hgetall(state_key)
                finalized = await finalize_state_if_active(
                    redis,
                    context.chat_id,
                    context.turn_id,
                    state_key,
                    mapping={
                        "status": "failed",
                        "updated_at": utc_now(),
                        "error": fallback_text,
                        "result_json": json.dumps({"error": fallback_text, "metrics": failure_metrics}),
                    },
                    ttl_seconds=settings.result_ttl_seconds,
                )
                if finalized:
                    from orion.kernel.chat_history import persist_failed_turn
                    await persist_failed_turn(redis, context.chat_id, context.turn_id,
                                              user_message, fallback_text, failed_state, failure_metrics)
                    await context.emit_error(fallback_text, metrics=failure_metrics)
                logging.info("Worker %s failed chat %s with terminal error event", consumer_name, context.chat_id)
                return

            fallback_total_ms = round((time.perf_counter() - generation_started_at) * 1000, 2)
            fallback_ttft_ms = round((first_token_at - generation_started_at) * 1000, 2) if first_token_at else fallback_total_ms

            if not conversation.rounds and router_metrics and ("ttft_ms" in router_metrics or "total_duration_ms" in router_metrics):
                ttft = router_metrics.get("ttft_ms") or 0
                total_dur = router_metrics.get("total_duration_ms") or 0
                metrics = {
                    "first_token_ms": max(1, int(round(ttft))),
                    "total_ms": max(1, int(round(total_dur))),
                }
            else:
                metrics = {
                    "first_token_ms": max(1, int(round(fallback_ttft_ms))),
                    "total_ms": max(1, int(round(fallback_total_ms))),
                }

            if first_token_at is None:
                metrics["first_token_ms"] = None

            final_text = "".join(output_tokens)

            if not await is_active_turn(redis, context.chat_id, context.turn_id):
                logging.info(
                    "Worker %s dropping stale finalization for chat %s turn %s",
                    consumer_name,
                    context.chat_id,
                    context.turn_id,
                )
                # Superseded (possibly from another device): close it everywhere.
                await context.emit_done("stopped")
                return

            # Determine final status:
            # - llm_done=True  → LLM exhausted naturally; treat as completed regardless of stop key
            #   (stop was pressed after LLM finished — nothing to abort, response is complete)
            # - stopped=True   → Stop pressed while LLM was mid-generation; treat as stopped
            is_completed = llm_done or not stopped

            # --- Multimodal: Text-to-Speech (TTS) Integration ---
            await context.emit_text_done(metrics)
            audio_entry = None
            if adaptive_tts:
                if stopped:
                    await adaptive_tts.cancel()
                else:
                    audio_entry = await adaptive_tts.finish()
                if adaptive_tts.error:
                    await context._publish(StreamEvent(
                        type="warning", chat_id=context.chat_id,
                        turn_id=context.turn_id, generation_id=context.turn_id,
                        data={"code": "tts_quota_exhausted" if getattr(adaptive_tts.error, "status", None) == 429
                              else "tts_failed", "message": "Seslendirme tamamlanamadı. Yanıtı metin olarak okuyabilirsiniz.",
                              "reason": _tts_reason(adaptive_tts.error)},
                    ))

            thinking_text = "".join(thinking_tokens)
            assistant_entry = {"role": "assistant", "content": final_text, "turn_id": context.turn_id}
            if conversation.activity:
                assistant_entry["tool_activity"] = conversation.activity
                assistant_entry["model_content"] = conversation.final_content
                if conversation.direct_tool_call_id:
                    assistant_entry["direct_tool_call_id"] = conversation.direct_tool_call_id
            if audio_entry:
                assistant_entry["audio"] = audio_entry
            if metrics:
                assistant_entry["metrics"] = metrics
            if thinking_text:
                assistant_entry["thinking"] = thinking_text
            if display_parts:
                assistant_entry["display_parts"] = display_parts

            await append_history(
                redis,
                context.chat_id,
                [user_message, *conversation.transcript, assistant_entry],
                settings.chat_history_max_messages,
                settings,
            )

            if is_completed:
                res_dict: dict[str, Any] = {"text": final_text}
                if metrics:
                    res_dict["metrics"] = metrics
                finalized = await finalize_state_if_active(
                    redis,
                    context.chat_id,
                    context.turn_id,
                    state_key,
                    mapping={
                        "status": "completed",
                        "updated_at": utc_now(),
                        "result_json": json.dumps(res_dict),
                    },
                    ttl_seconds=settings.result_ttl_seconds,
                )
                if not finalized:
                    return
                await context.emit_done("completed", metrics=metrics)
                if llm_done:
                    await generate_chat_title(context, settings)
                if metrics:
                    logging.info(
                        "Worker %s completed chat %s with Router metrics (TTFT: %s ms, Total: %dms)",
                        consumer_name, context.chat_id, metrics["first_token_ms"], metrics["total_ms"]
                    )
                else:
                    logging.info("Worker %s completed chat %s", consumer_name, context.chat_id)
            else:
                res_dict = {"text": final_text, "stopped": True}
                if metrics:
                    res_dict["metrics"] = metrics
                finalized = await finalize_state_if_active(
                    redis,
                    context.chat_id,
                    context.turn_id,
                    state_key,
                    mapping={
                        "status": "stopped",
                        "updated_at": utc_now(),
                        "result_json": json.dumps(res_dict),
                    },
                    ttl_seconds=settings.result_ttl_seconds,
                )
                if not finalized:
                    return
                await context.emit_done("stopped", metrics=metrics)
                if metrics:
                    logging.info(
                        "Worker %s stopped (mid-generation) chat %s with Router metrics (TTFT: %s ms, Total: %dms)",
                        consumer_name, context.chat_id, metrics["first_token_ms"], metrics["total_ms"]
                    )
                else:
                    logging.info("Worker %s stopped (mid-generation) chat %s", consumer_name, context.chat_id)

        if context.stream_mode == "continuous":
            await redis.expire(state_key, settings.result_ttl_seconds)

        stop_key = _turn_stop_key(context.turn_id)
        await redis.delete(stop_key)

    except Exception as exc:
        failed_state = await redis.hgetall(state_key)
        finalized = await finalize_state_if_active(
            redis,
            context.chat_id,
            context.turn_id,
            state_key,
            mapping={
                "status": "failed",
                "updated_at": utc_now(),
                "error": str(exc),
            },
            ttl_seconds=settings.result_ttl_seconds,
        )
        if finalized:
            from orion.kernel.chat_history import persist_failed_turn
            user_message = {
                'role': 'user', 'content': context.prompt,
                'display_text': context.request.input.metadata.get('display_text', context.prompt),
                'attachments': context.request.input.display_attachments(),
            }
            await persist_failed_turn(redis, context.chat_id, context.turn_id,
                                      user_message, str(exc), failed_state)
            await context.emit_error(str(exc))
        logging.exception("Worker %s failed chat %s", consumer_name, context.chat_id)

    finally:
        if adaptive_tts:
            await adaptive_tts.cancel()
