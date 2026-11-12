import json
import logging
from datetime import datetime, timezone
import time
from uuid import uuid4
from fastapi import HTTPException
from redis.asyncio import Redis

from orion.contracts.access import ChatAccessResult
from orion.contracts.constants import (
    ACTIVE_TURN_KEY_PREFIX, CHAT_META_KEY_PREFIX, CHAT_STATE_KEY_PREFIX, CHAT_STOP_KEY_PREFIX,
    ROOM_USER_PREFIX, STREAM_NAME, CHAT_USER_INDEX_PREFIX, CHAT_HISTORY_KEY_PREFIX,
    TURN_STOP_KEY_PREFIX,
)
from orion.contracts.events import StreamEvent
from orion.contracts.http import JobCreateRequest, JobCreateResponse, JobStatusResponse, JobStopResponse
from orion.contracts.queue import JobQueueRecord
from orion.kernel.chat_titles import initial_chat_title
from orion.kernel.config import get_runtime_settings
from orion.kernel.job_safety import job_key, WAITING_KEY, QUEUE_TIMEOUT_SECONDS, INIT_JOURNAL
from orion.kernel.registry import (
    upsert_chat, rename_chat_db, touch_chat_db, get_user_chats_db, get_chat_db,
    get_chat_history_db, delete_chat_db, get_turn_audio_db, get_all_chats_db
)

logger = logging.getLogger(__name__)

# --- Helpers ---
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def get_key(prefix: str, identifier: str) -> str:
    return f"{prefix}{identifier}"

def chat_access_message(state: str) -> str:
    return "Chat not found" if state == "not_found" else "Access denied"

# ---------------------------------------------------------------------------
# Access Control
# ---------------------------------------------------------------------------

async def check_chat_access(redis: Redis, user_id: str, chat_id: str) -> ChatAccessResult:
    """Whose chat this is, from its PostgreSQL row (one indexed read from the pool, ~0.5 ms).

    Redis no longer keeps chat metadata: a second copy had to be written on every new chat, rename and
    delete, and could disagree with the row. A database that does not answer is a 503, never "not found".
    """
    try:
        persistent = await get_chat_db(chat_id)
    except Exception as exc:
        logger.exception("Chat access could not be read for %s", chat_id)
        raise HTTPException(status_code=503, detail="Sohbet şu anda okunamıyor.") from exc
    if persistent is None:
        return ChatAccessResult(state="not_found", meta={})
    if persistent["user_id"] != user_id:
        return ChatAccessResult(state="forbidden", meta=persistent)
    return ChatAccessResult(state="ok", meta=persistent)

async def ensure_chat_access(redis: Redis, user_id: str, chat_id: str) -> dict:
    access = await check_chat_access(redis, user_id, chat_id)
    if access.state != "ok":
        status_code = 404 if access.state == "not_found" else 403
        raise HTTPException(status_code=status_code, detail=chat_access_message(access.state))
    return access.meta

# ---------------------------------------------------------------------------
# Core Services
# ---------------------------------------------------------------------------

async def create_job(redis: Redis, payload: JobCreateRequest) -> JobCreateResponse:
    from orion.api.services.tool_service import read_selection, save_selection, validate_selection
    from orion.worker.tools._registry import get_registry
    if payload.chat_id and payload.tool_selection is not None:
        raise HTTPException(422, "Use the chat tools endpoint to change an existing chat")
    if payload.tool_selection is not None:
        validate_selection(payload.tool_selection)
    settings = await get_runtime_settings(redis, payload.user_id)
    now = utc_now()
    turn_id = str(uuid4())
    chat_id = payload.chat_id or str(uuid4())
    channel = get_key(ROOM_USER_PREFIX, payload.user_id)

    # 1. Access Check (Sadece mevcut chat_id gelmişse)
    if payload.chat_id:
        access = await check_chat_access(redis, payload.user_id, chat_id)
        if not access.allowed:
            raise HTTPException(404 if access.state == "not_found" else 403, chat_access_message(access.state))

    title = initial_chat_title(payload.input.text) if not payload.chat_id else access.meta.get("name", "Yeni sohbet")

    try:
        if payload.chat_id:
            enabled_tools = (await read_selection(redis, payload.user_id, chat_id))["enabled_tools"]
        else:
            enabled_tools = get_registry().enabled(payload.tool_selection if payload.tool_selection is not None else settings.tool_selection)
            if payload.tool_selection is not None:
                await upsert_chat(chat_id=chat_id, user_id=payload.user_id, title=title)
                await save_selection(redis, payload.user_id, chat_id, payload.tool_selection)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

    # Normalize before any job payload, UI echo or message history retains it.
    if payload.input.attachments:
        from orion.api.services.input_audio import normalize_input_audio
        payload.input.attachments = [await normalize_input_audio(item) for item in payload.input.attachments]

    # The durable Hub owns media. Admission cannot enqueue a dangling reference.
    from orion.contracts.http import Attachment
    from orion.kernel.media_store import store_attachments
    if payload.input.attachments is None and payload.input.images:
        payload.input.attachments = [Attachment(
            name='Dosya', mime_type=image.split(';', 1)[0][5:] if image.startswith('data:') else 'image/jpeg',
            data=image if image.startswith(('data:', 'http://', 'https://')) else f'data:image/jpeg;base64,{image}',
        ) for image in payload.input.images]
        payload.input.images = None
    if any(not item.isText and (item.media_id or (item.data or '').startswith('data:'))
           for item in payload.input.attachments or []):
        try:
            await upsert_chat(chat_id=chat_id, user_id=payload.user_id, title=title)
            stored = await store_attachments(chat_id, payload.user_id, payload.input.display_attachments())
            payload.input.attachments = [Attachment.model_validate(item) for item in stored]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception('Media persistence failed before job admission')
            raise HTTPException(503, 'Medya kaydedilemedi. Ekleri koruyup tekrar göndermeyi deneyin.') from exc
    # Legacy metadata can contain a second copy of every attachment.
    payload.input.metadata.pop('attachments', None)

    if payload.chat_id:
        await _keep_superseded_turn(redis, chat_id, settings.redis_cache_ttl_seconds)

    # The chat row is the chat's only record (list, access, messages' foreign key): it is written before the
    # turn is queued, and a database that cannot take it refuses the send instead of losing the chat.
    try:
        await upsert_chat(chat_id=chat_id, user_id=payload.user_id, title=title)
    except Exception as exc:
        logger.exception("Chat %s could not be saved before admission", chat_id)
        raise HTTPException(503, "Sohbet kaydedilemedi. Mesajınızı koruyup tekrar göndermeyi deneyin.") from exc

    # 2. Prepare Data
    state_key = get_key(CHAT_STATE_KEY_PREFIX, chat_id)

    status_mapping = {
        "chat_id": chat_id, "status": "queued", "user_id": payload.user_id,
        "stream_mode": payload.stream_mode, "created_at": now, "updated_at": now, "channel": channel,
        "active_turn_id": turn_id,
        "current_prompt": payload.input.text,
        "current_user_message": json.dumps({
            "role": "user", "content": payload.input.text,
            "display_text": payload.input.metadata.get("display_text", payload.input.text),
            "attachments": payload.input.display_attachments(),
            "turn_id": turn_id,
        }),
        "partial_text": "", "partial_thinking": "", "partial_tools": "[]", "partial_display": "[]",
    }

    queue_record = JobQueueRecord(
        enabled_tools=json.dumps(enabled_tools),
        user_id=payload.user_id, chat_id=chat_id, turn_id=turn_id, channel=channel,
        created_at=now, stream_mode=payload.stream_mode, payload=payload.model_dump_json(exclude_none=True)
    )

    # 3. Atomic Execution (Pipeline)
    pipe = redis.pipeline()
    # CRITICAL: Delete any previous stop key BEFORE queueing the new generation.
    # This ensures the new worker starts with a clean slate.
    stop_key = get_key(CHAT_STOP_KEY_PREFIX, chat_id)
    active_turn_key = get_key(ACTIVE_TURN_KEY_PREFIX, chat_id)
    active_turn_ttl = max(settings.llm_timeout_seconds + settings.stop_key_ttl_seconds, 120)
    pipe.delete(stop_key)
    pipe.set(active_turn_key, turn_id, ex=active_turn_ttl)
    pipe.hset(state_key, mapping=status_mapping)
    # No expiry until a terminal result: missing journals never authorize replay.
    # A retried admission transaction must never reset an existing started or
    # terminal marker after an ambiguous Redis response.
    pipe.eval(INIT_JOURNAL, 1, job_key(turn_id), turn_id, chat_id,
              json.dumps({'turn_id': turn_id, 'chat_id': chat_id, 'channel': channel}))
    pipe.zadd(WAITING_KEY, {turn_id: time.time() + QUEUE_TIMEOUT_SECONDS})
    pipe.hdel(state_key, 'error', 'result_json')
    pipe.expire(state_key, max(settings.result_ttl_seconds, QUEUE_TIMEOUT_SECONDS + 60))
    pipe.publish(channel, StreamEvent.user_message(chat_id=chat_id, text=payload.input.text, turn_id=turn_id, attachments=payload.input.display_attachments(), display_text=payload.input.metadata.get("display_text", payload.input.text)).model_dump_json())
    pipe.publish(channel, StreamEvent.accepted(chat_id=chat_id, status="queued", turn_id=turn_id).model_dump_json())
    pipe.xadd(STREAM_NAME, fields=queue_record.model_dump(mode="json"))

    await pipe.execute()
    
    # Verify stop key was deleted (for debugging)
    stop_key_exists = await redis.exists(stop_key)
    if stop_key_exists:
        logger.warning("create_job: stop_key still exists after delete for chat %s — cleaning up explicitly", chat_id)
        await redis.delete(stop_key)

    return JobCreateResponse(chat_id=chat_id, status="queued", created_at=now, turn_id=turn_id,
                             generation_id=turn_id, attachments=payload.input.display_attachments())


async def _keep_superseded_turn(redis: Redis, chat_id: str, cache_ttl_seconds: int) -> None:
    """A new turn replaces a running one: its question stays, as after a stop.

    The Worker of the replaced turn then saves nothing; whichever of the two
    claims the turn's history first writes it.
    """
    from orion.kernel.registry import insert_messages
    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    turn_id = state.get("active_turn_id")
    if state.get("status") not in {"queued", "processing"} or not turn_id or not state.get("current_user_message"):
        return
    if not await redis.hsetnx(job_key(turn_id), "history_saved", "1"):
        return
    assistant = {"role": "assistant", "content": state.get("partial_text", ""), "turn_id": turn_id}
    if state.get("partial_thinking"):
        assistant["thinking"] = state["partial_thinking"]
    for field, key in (("partial_tools", "tool_activity"), ("partial_display", "display_parts")):
        value = json.loads(state.get(field) or "[]")
        if value:
            assistant[key] = value
    messages = [{**json.loads(state["current_user_message"]), "turn_id": turn_id}, assistant]
    try:
        await insert_messages(chat_id=chat_id, messages=messages)
    except Exception:
        logger.exception("Superseded turn of chat %s could not be saved", chat_id)


async def stop_job(redis: Redis, user_id: str, chat_id: str, turn_id: str | None = None) -> JobStopResponse:
    settings = await get_runtime_settings(redis, user_id)
    await ensure_chat_access(redis, user_id, chat_id)
    now = utc_now()
    active_turn_id = await redis.get(get_key(ACTIVE_TURN_KEY_PREFIX, chat_id))
    if not active_turn_id:
        state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
        active_turn_id = state.get("active_turn_id")
    if turn_id and turn_id != active_turn_id:
        raise HTTPException(404, "Turn not found in this chat")
    resolved_turn_id = turn_id or active_turn_id

    if not resolved_turn_id:
        return JobStopResponse(chat_id=chat_id, turn_id=None, status="stopping", updated_at=now)

    pipe = redis.pipeline()
    pipe.set(get_key(TURN_STOP_KEY_PREFIX, resolved_turn_id), "1", ex=settings.stop_key_ttl_seconds)
    pipe.hset(get_key(CHAT_STATE_KEY_PREFIX, chat_id), mapping={"updated_at": now})
    await pipe.execute()

    try:
        await touch_chat_db(chat_id, now)
    except Exception:
        logger.exception("Could not persist stop timestamp for chat %s", chat_id)

    return JobStopResponse(chat_id=chat_id, turn_id=resolved_turn_id, status="stopping", updated_at=now)


async def get_job(redis: Redis, user_id: str, chat_id: str) -> JobStatusResponse:
    await ensure_chat_access(redis, user_id, chat_id)
    raw = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))

    if not raw:
        raise HTTPException(status_code=404, detail="Chat state not found")

    return JobStatusResponse(
        chat_id=raw["chat_id"],
        status=raw["status"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        result=json.loads(raw["result_json"]) if raw.get("result_json") else None,
        error=raw.get("error"),
        turn_id=raw.get('active_turn_id'),
        retry_text=raw.get('current_prompt') if raw['status'] == 'failed' else None,
    )

# ---------------------------------------------------------------------------
# Chat List — Cache-Aside: PostgreSQL is source of truth
# ---------------------------------------------------------------------------

async def get_user_chats(redis: Redis, user_id: str, limit: int | None = None,
                         before: tuple[datetime, str] | None = None) -> list[dict]:
    """Return the user's chat list.

    Redis is the hot path. PostgreSQL is used only when the Redis index or
    metadata has expired, then the recovered chats are hydrated back into Redis.
    The assistant chat is left out: it has its own page.
    """
    from orion.api.services.assistant_chat import hide_assistant_chat
    try:
        chats = await get_user_chats_db(user_id, limit=limit, before=before, include_assistant=False)
    except Exception as exc:
        logger.exception("get_user_chats_db failed for user %s", user_id)
        raise HTTPException(status_code=503, detail="Sohbet listesi şu anda okunamıyor.") from exc
    channel = get_key(ROOM_USER_PREFIX, user_id)
    # An assistant chat that only Redis knows yet (adopted before its row was flagged) is left out too.
    return await hide_assistant_chat(redis, user_id, [{**chat, "channel": channel} for chat in chats])


def parse_chat_cursor(value: str | None) -> tuple[datetime, str] | None:
    """`before` of a chat list page: "<updated_at ISO>|<chat_id>" of the last chat already shown."""
    if not value:
        return None
    stamp, separator, chat_id = value.partition("|")
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        moment = None
    if not separator or not chat_id or moment is None or moment.tzinfo is None:
        raise HTTPException(status_code=422, detail="Geçersiz sayfa imleci.")
    return moment, chat_id


def _append_processing_state(history: list[dict], state: dict[str, str]) -> None:
    current_prompt = state.get("current_prompt", "")
    partial_text = state.get("partial_text", "")
    partial_thinking = state.get("partial_thinking", "")
    active_turn_id = state.get("active_turn_id")

    prompt_already_persisted = False
    if current_prompt:
        prompt_already_persisted = bool(
            history
            and history[-1].get("role") == "user"
            and history[-1].get("content") == current_prompt
        )
        if history and history[-1].get("role") == "assistant":
            previous = history[-2] if len(history) > 1 else None
            prompt_already_persisted = bool(
                previous
                and previous.get("role") == "user"
                and previous.get("content") == current_prompt
            )
        if active_turn_id and state.get("current_user_message"):
            prompt_already_persisted = any(msg.get("turn_id") == active_turn_id and msg.get("role") == "user" for msg in history)
        if not prompt_already_persisted:
            history.append(json.loads(state["current_user_message"]) if state.get("current_user_message") else {"role": "user", "content": current_prompt})

    # Keep an explicit in-progress item even before the first token. The UI
    # uses it to preserve/rehydrate the active turn across chat navigation.
    assistant_entry = {"role": "assistant", "content": partial_text, "partial": True}
    assistant_entry["tool_activity"] = json.loads(state.get("partial_tools", "[]"))
    assistant_entry["display_parts"] = json.loads(state.get("partial_display", "[]"))
    if active_turn_id:
        assistant_entry["turn_id"] = active_turn_id
    if partial_thinking:
        assistant_entry["thinking"] = partial_thinking
    history.append(assistant_entry)

# ---------------------------------------------------------------------------
# Chat History — Cache-Aside with Hydration
# ---------------------------------------------------------------------------



async def search_chats(user_id: str, query: str) -> list[dict]:
    """Messages of the user that hold the query, each with its chat, an excerpt and its date.

    The query counts as one phrase; a message holding all its longer words
    apart also matches, after the phrase matches. Chat names holding the
    phrase come first (clients hold only a page of the chat list).
    """
    from orion.kernel.chat_search import excerpt, query_words
    from orion.kernel.registry import search_chat_messages_db
    words = query_words(query)
    if not words or max(map(len, words)) < 2:
        return []
    from orion.kernel.registry import search_chat_titles_db
    try:
        titles = await search_chat_titles_db(user_id, words)
        rows = await search_chat_messages_db(user_id, words)
    except Exception as exc:
        logger.exception("Chat search failed for user %s", user_id)
        raise HTTPException(status_code=503, detail="Sohbetlerde şu anda aranamıyor.") from exc
    # Name matches first (role "title", the name as the excerpt), then message matches.
    return [
        {"chat_id": row["chat_id"], "name": row["title"], "role": "title", "snippet": row["title"],
         "created_at": row["updated_at"].isoformat()}
        for row in titles
    ] + [
        {"chat_id": row["chat_id"], "name": row["title"], "role": row["role"],
         "snippet": excerpt(row["plain_text"] or "", words),
         "created_at": row["created_at"].isoformat() if row.get("created_at") else None}
        for row in rows
    ]


async def index_chat_search() -> None:
    """Once per start: make messages saved by older versions searchable and windowable, a batch at a time."""
    import asyncio
    from orion.kernel.registry import index_message_turns, index_unsearchable_messages
    try:
        while await index_unsearchable_messages():
            await asyncio.sleep(0.05)
        while await index_message_turns():
            await asyncio.sleep(0.05)
    except Exception:
        logger.exception("Older messages could not be prepared for the chat search")


def history_window(history: list[dict], limit: int | None = None, before_turn: str | None = None) -> list[dict]:
    """The last `limit` messages, or the `limit` before the first message of `before_turn`, plus one more when
    older ones exist (a client keeping weeks of one chat loads it a window at a time and knows from the extra
    message that there is more). An unknown `before_turn` gives nothing."""
    end = len(history)
    if before_turn is not None:
        end = next((index for index, message in enumerate(history) if message.get('turn_id') == before_turn), None)
        if end is None:
            return []
    if limit is None:
        return history[:end]
    return history[max(0, end - limit - 1):end]


async def get_chat_history(redis: Redis, user_id: str, chat_id: str, include_audio: bool = True,
                           limit: int | None = None, before_turn: str | None = None,
                           max_chars: int | None = None) -> list[dict]:
    """Return the full message history for a chat.

    Strategy:
    1. Ensure the requesting user owns this chat.
    2. Check Redis for a cached history list (Cache Hit → return immediately).
    3. On Cache Miss, fetch from PostgreSQL and hydrate Redis with a 1-hour TTL.
    4. Append any in-progress (streaming) state from Redis state key so the
       UI always reflects the live generation.
    """
    await ensure_chat_access(redis, user_id, chat_id)
    from orion.kernel.chat_history import visible_history
    if limit is None and before_turn is None and max_chars is None:
        history = visible_history(await _load_history(chat_id))
        live = True
    else:
        # Only the window is read: a chat of thousands of messages costs what its last page costs.
        from orion.kernel.registry import get_chat_window_db
        try:
            window = await get_chat_window_db(chat_id, limit, before_turn, max_chars)
        except Exception as exc:
            logger.exception("Chat window could not be read for %s", chat_id)
            raise HTTPException(status_code=503, detail="Sohbet şu anda okunamıyor.") from exc
        if window is None:
            return []
        messages, size = window
        history = visible_history(messages)[-size:] if size else []
        live = before_turn is None

    # Append in-progress tokens for the currently streaming message (if any)
    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id)) if live else {}
    if state and state.get("status") in {"queued", "processing"}:
        _append_processing_state(history, state)

    if not include_audio:
        from urllib.parse import quote
        from orion.kernel.audio_metadata import metadata
        history = [
            {**msg, 'audio': metadata(msg['audio'],
                f"/api/v1/chats/{quote(chat_id, safe='')}/turns/{quote(msg['turn_id'], safe='')}/audio")}
            if msg.get('turn_id') and isinstance(msg.get('audio'), dict) and msg['audio'].get('audio') else msg
            for msg in history
        ]
    return history


async def get_turn_audio(redis: Redis, user_id: str, chat_id: str, turn_id: str) -> dict:
    # Ownership is checked before reading any recording, including DB fallback.
    await ensure_chat_access(redis, user_id, chat_id)
    try:
        audio = await get_turn_audio_db(chat_id, turn_id)
    except Exception:
        logger.exception('Direct audio lookup failed; trying cached history')
        audio = None
    if audio is not None:
        return await _audio_payload(chat_id, audio)
    history = await get_chat_history(redis, user_id, chat_id)
    def find(messages):
        return next((msg['audio'] for msg in messages
                     if msg.get('turn_id') == turn_id and msg.get('role') == 'assistant'
                     and isinstance(msg.get('audio'), dict) and msg['audio'].get('audio')), None)
    audio = find(history)
    if audio is None:
        audio = find(await get_chat_history_db(chat_id))
    if audio is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    return await _audio_payload(chat_id, audio)


async def _audio_payload(chat_id, audio):
    if audio.get('media_id'):
        from orion.kernel.media_store import read_media
        import base64
        _, raw = await read_media(chat_id, audio['media_id'])
        return {**audio, 'audio': base64.b64encode(raw).decode('ascii')}
    return audio


async def _load_history(chat_id: str) -> list[dict]:
    """A chat's saved messages, from PostgreSQL: the one record of them.

    Redis no longer keeps a copy. The copy was a second place to write (Redis first, PostgreSQL after, so a
    failed database write looked saved for an hour and was then gone), held whole chats in memory, and an
    expired copy that a write recreated showed only that turn. One indexed read from the connection pool
    costs about as much as reading and decoding the copy did. Redis keeps only what lives while a turn
    runs: its state, stream, stop and claim keys.
    """
    try:
        return await get_chat_history_db(chat_id)
    except Exception:
        logger.exception("get_chat_history_db failed for chat %s", chat_id)
        return []


async def get_chat_history_admin(redis: Redis, chat_id: str) -> list[dict]:
    history = await _load_history(chat_id)
    if not history and not await get_chat_db(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")

    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    if state and state.get("status") in {"queued", "processing"}:
        _append_processing_state(history, state)

    return history

# ---------------------------------------------------------------------------
# Rename / Delete
# ---------------------------------------------------------------------------

async def _chat_owner(redis: Redis, user_id: str | None, chat_id: str, admin: bool) -> str | None:
    if not admin:
        meta = await ensure_chat_access(redis, user_id, chat_id)
        return meta.get("user_id", user_id)
    row = await get_chat_db(chat_id)
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    return row.get("user_id")


async def rename_chat(redis: Redis, user_id: str | None, chat_id: str, name: str, admin: bool = False) -> dict:
    owner_id = await _chat_owner(redis, user_id, chat_id, admin)

    updated_at = utc_now()
    try:
        await rename_chat_db(chat_id, name, updated_at, owner_id)
    except Exception as exc:
        logger.exception("Could not persist chat rename for %s", chat_id)
        raise HTTPException(status_code=503, detail="Sohbet adı kalıcı olarak kaydedilemedi.") from exc

    # Broadcast to other devices
    if owner_id:
        channel = get_key(ROOM_USER_PREFIX, owner_id)
        await redis.publish(channel, StreamEvent.chat_rename(chat_id=chat_id, name=name).model_dump_json())

    return {"status": "ok", "chat_id": chat_id, "name": name}


async def delete_chat(redis: Redis, user_id: str | None, chat_id: str, admin: bool = False) -> dict:
    meta_key = get_key(CHAT_META_KEY_PREFIX, chat_id)  # Only leftovers of older versions are deleted.
    owner_id = await _chat_owner(redis, user_id, chat_id, admin)

    try:
        await delete_chat_db(chat_id)
    except Exception as exc:
        logger.exception("Could not delete chat %s from PostgreSQL", chat_id)
        raise HTTPException(status_code=503, detail="Sohbet veritabanından silinemedi.") from exc

    # Remove all Redis keys for this chat after durable deletion succeeds.
    pipe = redis.pipeline()
    pipe.delete(meta_key)
    pipe.delete(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    pipe.delete(get_key(CHAT_HISTORY_KEY_PREFIX, chat_id))
    pipe.delete(get_key(CHAT_STOP_KEY_PREFIX, chat_id))
    pipe.delete(get_key(ACTIVE_TURN_KEY_PREFIX, chat_id))
    if owner_id:
        pipe.zrem(get_key(CHAT_USER_INDEX_PREFIX, owner_id), chat_id)
    await pipe.execute()
    if owner_id:
        from orion.api.services.assistant_chat import forget_assistant_chat
        await forget_assistant_chat(redis, owner_id, chat_id)

    # Broadcast to other devices
    if owner_id:
        channel = get_key(ROOM_USER_PREFIX, owner_id)
        await redis.publish(channel, StreamEvent.chat_delete(chat_id=chat_id).model_dump_json())

    return {"status": "deleted", "chat_id": chat_id}


async def discard_failed_chat(redis: Redis, user_id: str, chat_id: str) -> dict:
    """Compatibility for older clients: failed conversations now remain visible."""
    access = await check_chat_access(redis, user_id, chat_id)
    if access.state == 'not_found':
        return {'status': 'deleted', 'chat_id': chat_id}
    if not access.allowed:
        raise HTTPException(403, 'Access denied')
    return {'status': 'kept', 'chat_id': chat_id}


async def delete_failed_turn(redis: Redis, user_id: str, chat_id: str, key: str) -> dict:
    from orion.kernel.chat_history import remove_failed_turn, referenced_media_ids
    from orion.kernel.registry import delete_failed_turn_db
    await ensure_chat_access(redis, user_id, chat_id)
    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    if state.get('status') in {'queued', 'processing'}:
        raise HTTPException(409, 'Yanıt tamamlandıktan sonra silmeyi tekrar deneyin.')
    history = await _load_history(chat_id)
    try:
        remaining, removed = remove_failed_turn(history, key)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not removed:
        raise HTTPException(409, 'Mesaj değişmiş veya silinmiş. Sohbeti yenileyin.')
    try:
        deleted = await delete_failed_turn_db(chat_id, key, delete_empty=not remaining,
            removed_media=referenced_media_ids(removed), retained_media=referenced_media_ids(remaining))
    except Exception as exc:
        logger.exception('Could not delete failed turn for %s', chat_id)
        raise HTTPException(503, 'Mesaj veritabanından silinemedi.') from exc
    if deleted:
        pipe = redis.pipeline()
        for prefix in (CHAT_META_KEY_PREFIX, CHAT_STATE_KEY_PREFIX, CHAT_STOP_KEY_PREFIX, ACTIVE_TURN_KEY_PREFIX):
            pipe.delete(get_key(prefix, chat_id))
        pipe.zrem(get_key(CHAT_USER_INDEX_PREFIX, user_id), chat_id)
        await pipe.execute()
    event = (StreamEvent.chat_delete(chat_id) if deleted else
             StreamEvent(type='chat_history_changed', chat_id=chat_id))
    await redis.publish(get_key(ROOM_USER_PREFIX, user_id), event.model_dump_json())
    return {'status': 'deleted' if deleted else 'updated', 'chat_id': chat_id}


async def get_all_chats_admin(redis: Redis) -> list[dict]:
    """Every chat, for the admin panel (it listed only chats Redis happened to hold)."""
    try:
        return await get_all_chats_db()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Sohbetler şu anda okunamıyor.") from exc
