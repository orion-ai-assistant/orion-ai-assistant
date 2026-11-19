"""Chat compaction (docs/hafiza-tasarimi.md §17b): a long "Asistanla konuş" conversation is turned into a summary and memory
items by ONE call of the background model (the memory writer, memory_extract), which gets the whole not-yet-compacted
conversation and answers {summary, entities, ops} (the same ops as the writer: facts, events, relation changes, notes).

- The summary becomes the conversation's first message from now on (`orion_mem_chat_state.summary`, `compacted_upto` = the last
  message it covers; registry.get_compaction_db, job_processor.load_history). The older messages stay stored and shown for the
  user; they are not sent to the model.
- The memory items go through the writer's candidate pipeline (quality gate): written live (add-only); a change to something known is a card.
- Triggers: the user's "Sohbeti sıkıştır" (`compact`); automatically (a) when the chat is IDLE: usage >= `chat_compact_idle_percent`
  (40) of the chat model's context length AND no message for `chat_compact_idle_minutes` (30), checked by a light periodic loop
  (`idle_loop`), and (b) before the next request is sent when usage >= `chat_compact_force_percent` (90) (`force_before_send`).
  Context length: the Router's model catalog, else `chat_context_tokens`, else 128000. Usage is the REAL
  count of the last answer (prompt + completion tokens of the Router's usage); after a compaction, until the next answer, an
  estimate of the summary + the messages since. There is no extraction after every answer any more.
Every table has user_id; every read filters by it."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from orion.kernel import memory, memory_collections, memory_entities as me, memory_extract as mx

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT = 128000  # Assumed when no context length is known.

COMPACT_NOTE = (
    "compaction: the \"summary\" REPLACES the older messages for the assistant, so it is the assistant's only memory of them. Write it "
    "in the language of the conversation as a faithful continuation summary. Keep what the assistant needs to go on, and match "
    "the kind of conversation. TECHNICAL (code, tools, configs, debugging): keep file names and paths, function/class/API names, "
    "commands, versions, decisions and the reasons for them, what was done, what is still open, and the EXACT error messages of "
    "unresolved problems; short code identifiers verbatim. PERSONAL (life, family, feelings, plans): keep the facts the user "
    "told, names, dates, numbers, what they wish and what is still to do. Mixed: both. Drop greetings, repetition, and anything "
    "solved and no longer relevant. Length follows the conversation: a short talk gets a few sentences; a long, dense one may use "
    "up to about 1500 words, never more, and never longer than a fifth of the text it replaces. Also file the memory items.")
MAX_ROWS = 4000
SUMMARY_CHARS = 12000  # A hard cap on the stored summary (about 1500 words are asked for).
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
    got = {"prompt_tokens": int(usage.get("prompt_tokens") or 0), "completion_tokens": int(usage.get("completion_tokens") or 0),
           "at": usage.get("at")}
    if usage.get("estimated"):
        got["estimated"] = True  # A stopped answer the Router reported no usage for: a rough count.
    if isinstance(usage.get("breakdown"), dict):
        got["breakdown"] = usage["breakdown"]
    return got


BREAKDOWN_KEYS = ("system", "profile", "tools", "messages", "tool_results")


def scale_breakdown(parts: dict, total: int) -> dict | None:
    """`parts` ({system, profile, tools, messages, tool_results}: estimated tokens of what a request carried) scaled so that they
    add up to `total` (the Router's real prompt_tokens). None when there is nothing to scale (no estimate or no total)."""
    parts = {k: max(0, int(parts.get(k) or 0)) for k in BREAKDOWN_KEYS}
    have = sum(parts.values())
    if have <= 0 or total <= 0:
        return None
    # The estimate of the conversation text is the one that is off (tokenizers differ most on the user's words), so the fixed
    # parts keep their own estimate and `messages` takes the rest: a tool result's count stays the same turn after turn (a
    # proportional scaling made it grow with the chat). If the fixed parts alone exceed the total, scale everything.
    fixed = have - parts["messages"]
    if fixed < total:
        return {**parts, "messages": total - fixed}
    scaled = {k: int(v * total / have) for k, v in parts.items()}
    scaled[max(parts, key=parts.get)] += total - sum(scaled.values())  # The rounding rest goes to the biggest part.
    return scaled


def _message_tokens(message: dict) -> int:
    content = message.get("content")
    if isinstance(content, list):  # Multimodal parts: only the text counts here (the scaling absorbs the rest).
        content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    n = memory.estimate_tokens(str(content or ""))
    if message.get("tool_calls"):
        n += memory.estimate_tokens(json.dumps(message["tool_calls"], ensure_ascii=False, default=str))
    return n


def request_parts(messages: list[dict], system_text: str, profile_text: str, tools_tokens: int) -> dict:
    """Estimated tokens of each part of the LAST request of a turn: `messages` is the whole message list it carried (system first,
    the chat history, this turn's tool rounds), `system_text` the user's own system prompt, `profile_text` the profile + "soon"
    blocks (both inside the system message), `tools_tokens` the tool schemas. Estimates only (memory.estimate_tokens)."""
    parts = {"system": memory.estimate_tokens(system_text or ""), "profile": memory.estimate_tokens(profile_text or ""),
             "tools": int(tools_tokens or 0), "messages": 0, "tool_results": 0}
    for m in messages:
        if m.get("role") == "system":
            continue  # Counted by `system` and `profile`.
        if m.get("role") == "tool":
            parts["tool_results"] += _message_tokens(m)
        else:
            parts["messages"] += _message_tokens(m)
    return parts


def used_of(last: dict | None, last_id: int | None, compacted_upto: int | None, summary_tokens: int,
            tokens_since: int) -> tuple[int, bool, dict | None]:
    """(used_tokens, estimated, breakdown) of a chat. After a compaction, until an answer newer than it reports its usage, the
    size is an estimate: the summary + the messages since. Otherwise the last answer's REAL count (prompt + completion tokens);
    with no usage at all, the estimate too."""
    fresh = last is not None and not (compacted_upto and (last_id or 0) <= compacted_upto)
    if fresh:
        return int(last["prompt_tokens"]) + int(last["completion_tokens"]), bool(last.get("estimated")), last.get("breakdown")
    return summary_tokens + tokens_since, True, None


async def state(user_id: str, chat_id: str) -> dict:
    """{"running", "compacted_upto", "compacted_at", "summary_tokens", "messages_since", "tokens_since", "summary" (None before the
    first compaction), "compacted_turn_id" (turn of the last covered message: the summary starts after it), "last_usage"
    (the last assistant answer's usage, None when it has none), "used_tokens" (the chat's size now: the last answer's
    prompt + completion tokens, or an estimate after a compaction: see used_of), "used_estimated", "breakdown" ({system, profile,
    tools, messages, tool_results}: estimated shares scaled to the real prompt tokens of the last answer, None when unknown)}."""
    conn = await mx._connect()
    try:
        row = await conn.fetchrow("select summary, compacted_upto, compacted_at from orion_mem_chat_state where user_id = $1 "
                                  "and chat_id = $2", user_id, chat_id)
        messages, _ = await _uncompacted(conn, chat_id)
        turn_id = None
        if row and row["compacted_upto"]:
            turn_id = await conn.fetchval("select turn_id from orion_chat_messages where chat_id = $1 and id = $2",
                                          chat_id, row["compacted_upto"]) or None
        last = await conn.fetchrow("select id, content_json from orion_chat_messages where chat_id = $1 and role = 'assistant' "
                                   "and strpos(content_json, '\"usage\"') > 0 order by id desc limit 1", chat_id)
    finally:
        await conn.close()
    compacted = bool(row and row["compacted_upto"])
    usage = usage_of(last["content_json"]) if last else None
    summary_tokens = memory.estimate_tokens(row["summary"]) if compacted else 0
    tokens_since = estimate(messages)
    used, estimated, breakdown = used_of(usage, last["id"] if last else None, row["compacted_upto"] if row else None,
                                         summary_tokens, tokens_since)
    return {"running": chat_id in _running, "compacted_upto": row["compacted_upto"] if row else None,
            "compacted_at": row["compacted_at"].isoformat() if compacted and row["compacted_at"] else None,
            "summary_tokens": summary_tokens,
            "summary": row["summary"] if compacted else None, "compacted_turn_id": turn_id,
            "last_usage": usage, "used_tokens": used, "used_estimated": estimated, "breakdown": breakdown,
            "messages_since": len(messages), "tokens_since": tokens_since}


async def compact(user_id: str, chat_id: str, settings) -> dict:
    """Compact the assistant chat now: one model call, then the summary and the items are stored. ValueError when it is not the
    user's assistant chat, the memory is off or there is nothing to compact; BadOutput when the model's answer is not JSON.
    {"summary_tokens", "candidates", "compacted_upto"}."""
    if not mx.enabled(settings):
        raise ValueError("Hafıza kapalı (memory_v2).")
    if chat_id in _running:
        raise ValueError("Bu sohbet zaten sıkıştırılıyor.")
    _running.add(chat_id)
    guard = None
    try:
        guard = await mx._connect()  # One compaction per chat across the Hub's processes (a session lock on this connection).
        if not await guard.fetchval("select pg_try_advisory_lock(hashtext($1))", "compact:" + chat_id):
            raise ValueError("Bu sohbet zaten sıkıştırılıyor.")
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
        summary = "\n".join(line.rstrip() for line in str(parsed.get("summary") or "").strip().splitlines())[:SUMMARY_CHARS] or None
        if not summary:
            raise mx.BadOutput("the model's answer has no summary")
        last = messages[-1]["id"]
        conn = await mx._connect()
        try:
            candidates = await mx.build_candidates(conn, user_id, parsed, messages, None)
            run_id = await conn.fetchval(
                "insert into orion_mem_extraction_run (user_id, chat_id, mode, prefilter, state, model, tokens_in, tokens_out, "
                "candidates, first_message, last_message, started_at, finished_at) values ($1, $2, $3, 'passed:compaction', "
                "'done', $4, $5, $6, $7, $8, $9, now(), now()) returning id", user_id, chat_id, "live",
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
        for cid, c in zip(ids, candidates):
            await mx.apply_candidate(user_id, settings, run_id, chat_id, cid, c)
        return {"summary_tokens": memory.estimate_tokens(summary), "candidates": len(candidates), "compacted_upto": last}
    finally:
        _running.discard(chat_id)
        if guard is not None:
            await guard.close()  # Closing the connection releases the lock.


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


async def context_length(settings) -> int:
    """The chat model's context length in tokens: from the Router's model catalog (the `context_length` of the chat model's entry; a
    group: its smallest), else the setting `chat_context_tokens`, else DEFAULT_CONTEXT (128000: the Router has no value for the model
    or cannot be reached; the meter and the compaction go on with that)."""
    try:
        from orion.kernel.router_models import get_router_catalog
        catalog = await get_router_catalog(include_voices=False)
        for model in catalog["models"]:
            if model.get("name") == settings.router_model_group and model.get("capability") == "chat":
                if int(model.get("context_length") or 0) > 0:
                    return int(model["context_length"])
    except Exception:
        pass
    return int(getattr(settings, "chat_context_tokens", 0) or 0) or DEFAULT_CONTEXT


async def usage_state(user_id: str, chat_id: str, settings) -> tuple[int, int, dict] | None:
    """(context length, used tokens, state) or None when the context length is unknown / memory is off."""
    window = await context_length(settings)
    if window <= 0 or not mx.enabled(settings):
        return None
    got = await state(user_id, chat_id)
    return window, got["used_tokens"], got


async def force_before_send(user_id: str, chat_id: str, settings) -> bool:
    """Before a request of the assistant chat is sent: when the chat's size is at `chat_compact_force_percent` (90) of the context
    length, compact it NOW (waits for it), so the request carries the summary. True when it was compacted. Never raises: a chat
    does not break for it."""
    try:
        if chat_id in _running:
            return False
        got = await usage_state(user_id, chat_id, settings)
        if got is None or not got[2]["messages_since"]:
            return False
        window, used, _ = got
        if used < window * int(getattr(settings, "chat_compact_force_percent", 90)) / 100:
            return False
        await compact(user_id, chat_id, settings)
        return True
    except Exception:
        logger.warning("Forced compaction of chat %s failed", chat_id[:8], exc_info=True)
        return False


IDLE_EVERY = 300  # Seconds between the checks of the idle loop.
IDLE_START = 120
_idle_checked: dict[str, int] = {}  # chat id -> the last message id it was judged at (no second look until a new message).


async def idle_check(now: datetime | None = None) -> int:
    """One pass: every assistant chat whose last message is at least `chat_compact_idle_minutes` old (30), which has uncompacted
    messages and whose size is at `chat_compact_idle_percent` (40) of the context length, is compacted in the background. Returns
    how many were started. Cheap: one query for the candidates; a chat is judged once per last message."""
    from orion.kernel.config import get_runtime_settings
    conn = await mx._connect()
    try:
        rows = await conn.fetch(
            "select c.id, c.user_id, m.last_id, m.last_at from orion_chats c join lateral (select max(id) as last_id, "
            "max(created_at) as last_at from orion_chat_messages where chat_id = c.id) m on m.last_id is not null "
            "where c.assistant and m.last_at < now() - interval '1 minute' and m.last_at > now() - interval '7 days' "
            "and m.last_id > coalesce((select compacted_upto from orion_mem_chat_state s where s.chat_id = c.id "
            "and s.user_id = c.user_id), 0)")
    finally:
        await conn.close()
    started = 0
    for r in rows:
        if _idle_checked.get(r["id"]) == r["last_id"] or r["id"] in _running:
            continue
        try:
            settings = await get_runtime_settings(None, r["user_id"])
            idle = (now or datetime.now().astimezone()) - r["last_at"]
            if idle.total_seconds() < int(getattr(settings, "chat_compact_idle_minutes", 30)) * 60:
                continue  # Not idle long enough yet: looked at again next time.
            _idle_checked[r["id"]] = r["last_id"]
            got = await usage_state(r["user_id"], r["id"], settings)
            if got is None:
                continue
            window, used, _ = got
            if used >= window * int(getattr(settings, "chat_compact_idle_percent", 40)) / 100:
                start(r["user_id"], r["id"], settings)
                started += 1
        except Exception:
            logger.warning("Idle compaction check of chat %s failed", str(r["id"])[:8], exc_info=True)
    return started


async def idle_loop() -> None:
    """The periodic job (started by the Hub): idle_check every IDLE_EVERY seconds."""
    await asyncio.sleep(IDLE_START)
    while True:
        try:
            await idle_check()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Idle compaction pass failed", exc_info=True)
        await asyncio.sleep(IDLE_EVERY)
