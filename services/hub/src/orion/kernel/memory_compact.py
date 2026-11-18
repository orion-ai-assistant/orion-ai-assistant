"""Chat compaction (docs/hafiza-tasarimi.md §17b): a long "Asistanla konuş" conversation is turned into a summary and memory
items by ONE call of the background model (the memory writer, memory_extract), which gets the whole not-yet-compacted
conversation and answers {summary, entities, ops} (the same ops as the writer: facts, events, relation changes, notes).

- The summary becomes the conversation's first message from now on (`orion_mem_chat_state.summary`, `compacted_upto` = the last
  message it covers; registry.get_compaction_db, job_processor.load_history). The older messages stay stored and shown for the
  user; they are not sent to the model.
- The memory items go through the writer's candidate pipeline (quality gate): added directly only when automatic writing is
  switched on (shadow otherwise, as the extraction always was); a change to something known is a card.
- Triggers: the user's "Sohbeti sıkıştır" (`compact`), or automatically after an answer when the conversation passes
  `chat_compact_percent` (default 70) of the chat model's context length (the Router's model catalog, else `chat_context_tokens`; 0 =
  unknown: no automatic compaction), measured with the REAL token count of the last request (stream usage) (`maybe_compact`).
  There is no extraction after every answer any more.
Every table has user_id; every read filters by it."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from orion.kernel import memory, memory_collections, memory_entities as me, memory_extract as mx

logger = logging.getLogger(__name__)

COMPACT_NOTE = ("compaction: the summary REPLACES the older messages for the assistant. Make it a faithful continuation summary "
                "in the language of the conversation: what was decided, what is open, names, numbers and the user's wishes, "
                "as short as it can be without losing what the assistant needs to go on. Also file the memory items.")
MAX_ROWS = 4000
_running: set[str] = set()


def estimate(messages: list[dict]) -> int:
    return sum(memory.estimate_tokens(m["text"]) for m in messages)


async def _uncompacted(conn, chat_id: str) -> tuple[list[dict], int]:
    upto = await conn.fetchval("select compacted_upto from orion_mem_chat_state where chat_id = $1 limit 1", chat_id) or 0
    rows = await conn.fetch("select id, content_json, created_at from orion_chat_messages where chat_id = $1 and id > $2 "
                            "order by id limit $3", chat_id, upto, MAX_ROWS)
    return [m for m in (mx._message(r) for r in rows) if m], upto


def usage_of(content_json) -> dict | None:
    """{"prompt_tokens", "completion_tokens", "at"} of a stored assistant message (None: it has no usage)."""
    try:
        usage = json.loads(content_json).get("usage")
    except (TypeError, ValueError, AttributeError):
        return None
    if not isinstance(usage, dict) or usage.get("prompt_tokens") is None:
        return None
    return {"prompt_tokens": int(usage.get("prompt_tokens") or 0), "completion_tokens": int(usage.get("completion_tokens") or 0),
            "at": usage.get("at")}


async def state(user_id: str, chat_id: str) -> dict:
    """{"running", "compacted_upto", "compacted_at", "summary_tokens", "messages_since", "tokens_since", "summary" (None before the
    first compaction), "compacted_turn_id" (turn of the last covered message: the summary starts after it), "last_usage"
    (the last assistant answer's usage, None when it has none)}."""
    conn = await mx._connect()
    try:
        row = await conn.fetchrow("select summary, compacted_upto, compacted_at from orion_mem_chat_state where user_id = $1 "
                                  "and chat_id = $2", user_id, chat_id)
        messages, _ = await _uncompacted(conn, chat_id)
        turn_id = None
        if row and row["compacted_upto"]:
            turn_id = await conn.fetchval("select turn_id from orion_chat_messages where chat_id = $1 and id = $2",
                                          chat_id, row["compacted_upto"]) or None
        last = await conn.fetchrow("select content_json from orion_chat_messages where chat_id = $1 and role = 'assistant' "
                                   "and strpos(content_json, '\"usage\"') > 0 order by id desc limit 1", chat_id)
    finally:
        await conn.close()
    compacted = bool(row and row["compacted_upto"])
    return {"running": chat_id in _running, "compacted_upto": row["compacted_upto"] if row else None,
            "compacted_at": row["compacted_at"].isoformat() if compacted and row["compacted_at"] else None,
            "summary_tokens": memory.estimate_tokens(row["summary"]) if compacted else 0,
            "summary": row["summary"] if compacted else None, "compacted_turn_id": turn_id,
            "last_usage": usage_of(last["content_json"]) if last else None,
            "messages_since": len(messages), "tokens_since": estimate(messages)}


async def compact(user_id: str, chat_id: str, settings) -> dict:
    """Compact the assistant chat now: one model call, then the summary and the items are stored. ValueError when it is not the
    user's assistant chat, the memory is off or there is nothing to compact; BadOutput when the model's answer is not JSON.
    {"summary_tokens", "candidates", "compacted_upto"}."""
    if not mx.enabled(settings):
        raise ValueError("Hafıza kapalı (memory_v2 ve sohbetten çıkarım).")
    if chat_id in _running:
        raise ValueError("Bu sohbet zaten sıkıştırılıyor.")
    _running.add(chat_id)
    try:
        mx.remember(user_id, settings)
        conn = await mx._connect()
        try:
            await memory_collections.ensure_user(conn, user_id)
            chat = await conn.fetchrow("select assistant from orion_chats where id = $1 and user_id = $2", chat_id, user_id)
            if chat is None or not chat["assistant"]:
                raise ValueError("Yalnızca Asistanla konuş sohbeti sıkıştırılır.")
            messages, _ = await _uncompacted(conn, chat_id)
            if not messages:
                raise ValueError("Sıkıştırılacak mesaj yok.")
            previous = await conn.fetchval("select summary from orion_mem_chat_state where user_id = $1 and chat_id = $2 "
                                           "and compacted_upto is not null", user_id, chat_id)
            keys, entities = await mx._entity_context(conn, user_id, [m["text"] for m in messages if m["role"] == "user"])
            live = bool(await conn.fetchval("select live from orion_mem_extraction_state where user_id = $1", user_id))
            now = datetime.now().astimezone()
        finally:
            await conn.close()
        payload = {"now": now.isoformat(timespec="minutes"), "today": now.date().isoformat(), "timezone": now.tzname(), "compact": COMPACT_NOTE,
                   "previous_summary": previous or "", "known_entities": entities,
                   "new_messages": [{"id": m["id"], "role": m["role"], "time": m["at"].isoformat(timespec="minutes") if m["at"] else None,
                                     "text": m["text"]} for m in messages]}
        request = [{"role": "system", "content": mx.SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        text, usage = await mx._call_model(settings, request)
        parsed = mx.parse_output(text)
        summary = " ".join(str(parsed.get("summary") or "").split()) or None
        if not summary:
            raise mx.BadOutput("the model's answer has no summary")
        last = messages[-1]["id"]
        conn = await mx._connect()
        try:
            candidates = await mx.build_candidates(conn, user_id, parsed, messages, not live, None)
            run_id = await conn.fetchval(
                "insert into orion_mem_extraction_run (user_id, chat_id, mode, prefilter, state, model, tokens_in, tokens_out, "
                "candidates, first_message, last_message, started_at, finished_at) values ($1, $2, $3, 'passed:compaction', "
                "'done', $4, $5, $6, $7, $8, $9, now(), now()) returning id", user_id, chat_id, "live" if live else "shadow",
                mx.model_of(settings), mx._count(usage.get("prompt_tokens")), mx._count(usage.get("completion_tokens")),
                len(candidates), messages[0]["id"], last)
            ids = []
            async with conn.transaction():
                for c in candidates:
                    ids.append(await conn.fetchval(
                        "insert into orion_mem_candidate (user_id, run_id, chat_id, first_message, last_message, op, bucket, "
                        "unsupported_risk, dedupe, merge_target, sensitive, verdict, reason) values ($1, $2, $3, $4, $5, $6, "
                        "$7, $8, $9, $10, $11, $12, $13) returning id", user_id, run_id, chat_id, c["first"], c["last"],
                        json.dumps(c["op"], ensure_ascii=False), c["bucket"], c["unsupported_risk"], c["dedupe"],
                        c["merge_target"], c["sensitive"], c["verdict"], c["reason"]))
                await conn.execute(
                    "insert into orion_mem_chat_state (user_id, chat_id, extracted_upto, summary, summary_upto, summary_stale, "
                    "summary_at, compacted_upto, compacted_at) values ($1, $2, $3, $4, $3, false, now(), $3, now()) "
                    "on conflict (user_id, chat_id) do update set extracted_upto = greatest($3, coalesce("
                    "orion_mem_chat_state.extracted_upto, 0)), summary = $4, summary_upto = $3, summary_stale = false, "
                    "summary_at = now(), compacted_upto = $3, compacted_at = now()", user_id, chat_id, last, summary)
        finally:
            await conn.close()
        try:  # One embedding per compaction: meaning search finds older talks through their summaries.
            await memory.add_note(user_id, summary, settings, source_kind="chat_summary",
                                  title=f"Sohbet özeti {now.date().isoformat()} (mesaj {messages[0]['id']}-{last})")
        except Exception:
            logger.warning("The summary of chat %s could not be embedded", chat_id[:8], exc_info=True)
        if live:
            for cid, c in zip(ids, candidates):
                await mx.apply_candidate(user_id, settings, run_id, chat_id, cid, c)
        return {"summary_tokens": memory.estimate_tokens(summary), "candidates": len(candidates), "compacted_upto": last}
    finally:
        _running.discard(chat_id)


async def search_messages(user_id: str, words: list[str], span=None, limit: int = 10) -> list[dict]:
    """Past talk of the user's "Asistanla konuş" chat by KEYWORD and date, straight in PostgreSQL (the messages' folded text,
    trigram/LIKE; nothing is embedded per message): [{"chat_id", "message_id", "role", "date", "text"}], the newest first. `words`: every
    word must be in the message; `span`: (first, last) days (memory_events.parse_range). Meaning search of older talks goes
    through the compaction summaries (one embedding per compaction: source_kind 'chat_summary')."""
    from orion.kernel.chat_search import like_pattern
    folded = [w for w in (memory.fold(x) for x in words) if len(w) >= 2]
    if not folded and not span:
        return []
    params: list = [user_id]
    where = ["c.user_id = $1", "c.assistant", "m.role in ('user', 'assistant')", "m.plain_text <> ''"]
    for w in folded:
        params.append(like_pattern(w))
        where.append(f"m.search_text like ${len(params)}")
    if span and span[0] is not None:
        params.append(span[0])
        where.append(f"m.created_at >= ${len(params)}::date")
    if span and span[1] is not None:
        params.append(span[1])
        where.append(f"m.created_at < (${len(params)}::date + 1)")
    conn = await mx._connect()
    try:
        rows = await conn.fetch(
            f"select m.id, m.chat_id, m.role, m.plain_text, m.created_at from orion_chat_messages m join orion_chats c on "
            f"c.id = m.chat_id where {' and '.join(where)} order by m.id desc limit {max(1, min(int(limit), 30))}", *params)
    finally:
        await conn.close()
    return [{"chat_id": r["chat_id"], "message_id": r["id"], "role": r["role"], "date": r["created_at"].date().isoformat(),
             "text": " ".join((r["plain_text"] or "").split())[:300]} for r in rows]


def start(user_id: str, chat_id: str, settings) -> None:
    """Compact in the background (the API answers at once; `state` tells when it is done). Never raises."""
    async def job():
        try:
            await compact(user_id, chat_id, settings)
        except Exception:
            logger.warning("Chat %s could not be compacted", chat_id[:8], exc_info=True)
    try:
        asyncio.get_running_loop().create_task(job(), name=f"compact-{chat_id[:8]}")
    except RuntimeError:
        pass


CONTEXT_KEYS = ("context_length", "context_window", "max_input_tokens", "context")


async def context_length(settings) -> int:
    """The chat model's context length in tokens: from the Router's model catalog (the first of CONTEXT_KEYS the chat model's entry
    has), else the setting `chat_context_tokens` (0 = not known)."""
    try:
        from orion.kernel.router_models import get_router_catalog
        catalog = await get_router_catalog(include_voices=False)
        for model in catalog["models"]:
            if model.get("name") == settings.router_model_group and model.get("capability") == "chat":
                for key in CONTEXT_KEYS:
                    if int(model.get(key) or 0) > 0:
                        return int(model[key])
    except Exception:
        pass
    return int(getattr(settings, "chat_context_tokens", 0) or 0)


async def maybe_compact(user_id: str, chat_id: str, settings, used_tokens: int = 0) -> bool:
    """After an answer: start compaction when the conversation passes `chat_compact_percent` of the chat model's context length.
    The size is the REAL one: the last request's usage (prompt + completion tokens, from the stream); only when the stream gave no
    usage (`used_tokens` 0) a rough count of the not yet compacted messages (plus the summary) stands in. False when the context
    length is unknown (0), it is not reached, or a compaction is running."""
    window = await context_length(settings)
    if window <= 0 or not mx.enabled(settings) or chat_id in _running:
        return False
    if not used_tokens:
        try:
            got = await state(user_id, chat_id)
        except Exception:
            return False
        used_tokens = got["tokens_since"] + got["summary_tokens"]
    if used_tokens < window * int(getattr(settings, "chat_compact_percent", 70)) / 100:
        return False
    start(user_id, chat_id, settings)
    return True


def after_turn(chat_id: str, settings, used_tokens: int = 0) -> None:
    """A turn of the chat was saved (job_processor): compact it if it is the assistant chat and has grown past the threshold.
    Returns at once; never raises."""
    if not mx.enabled(settings):
        return

    async def job():
        try:
            conn = await mx._connect()
            try:
                row = await conn.fetchrow("select user_id, assistant from orion_chats where id = $1", chat_id)
            finally:
                await conn.close()
            if row and row["assistant"]:
                await maybe_compact(row["user_id"], chat_id, settings, used_tokens)
        except Exception:
            logger.warning("Compaction check of chat %s failed", chat_id[:8], exc_info=True)
    try:
        asyncio.get_running_loop().create_task(job(), name=f"compact-check-{chat_id[:8]}")
    except RuntimeError:
        pass
