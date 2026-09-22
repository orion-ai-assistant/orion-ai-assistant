import json
import logging
from datetime import datetime, timezone
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
from orion.kernel.config import get_runtime_settings
from orion.kernel.registry import (
    upsert_chat, rename_chat_db, touch_chat_db, get_user_chats_db, get_chat_db,
    get_chat_history_db, delete_chat_db
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
    raw = await redis.hgetall(get_key(CHAT_META_KEY_PREFIX, chat_id))
    if not raw:
        persistent = await get_chat_db(chat_id)
        if persistent is None:
            return ChatAccessResult(state="not_found", meta={})
        if persistent["user_id"] != user_id:
            return ChatAccessResult(state="forbidden", meta=persistent)
        settings = await get_runtime_settings(redis, user_id)
        meta_key = get_key(CHAT_META_KEY_PREFIX, chat_id)
        pipe = redis.pipeline()
        pipe.hset(meta_key, mapping=persistent)
        pipe.expire(meta_key, settings.redis_cache_ttl_seconds)
        await pipe.execute()
        return ChatAccessResult(state="ok", meta=persistent)
    if raw.get("user_id") != user_id:
        return ChatAccessResult(state="forbidden", meta=raw)
    return ChatAccessResult(state="ok", meta=raw)

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
    settings = await get_runtime_settings(redis, payload.user_id)
    now = utc_now()
    turn_id = str(uuid4())
    chat_id = payload.chat_id or str(uuid4())
    channel = get_key(ROOM_USER_PREFIX, payload.user_id)

    # 1. Access Check (Sadece mevcut chat_id gelmişse)
    if payload.chat_id:
        access = await check_chat_access(redis, payload.user_id, chat_id)
        if not access.allowed:
            await redis.publish(channel, StreamEvent.error(chat_id, chat_access_message(access.state)).model_dump_json())
            return JobCreateResponse(chat_id=chat_id, status="failed", created_at=now, turn_id=None, generation_id=None)

    # 2. Prepare Data
    state_key = get_key(CHAT_STATE_KEY_PREFIX, chat_id)
    meta_key = get_key(CHAT_META_KEY_PREFIX, chat_id)

    status_mapping = {
        "chat_id": chat_id, "status": "queued", "user_id": payload.user_id,
        "stream_mode": payload.stream_mode, "created_at": now, "updated_at": now, "channel": channel,
        "active_turn_id": turn_id,
    }

    queue_record = JobQueueRecord(
        user_id=payload.user_id, chat_id=chat_id, turn_id=turn_id, channel=channel,
        created_at=now, stream_mode=payload.stream_mode, payload=payload.model_dump_json()
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
    pipe.expire(state_key, settings.result_ttl_seconds)
    pipe.publish(channel, StreamEvent.user_message(chat_id=chat_id, text=payload.input.text, turn_id=turn_id).model_dump_json())
    pipe.publish(channel, StreamEvent.accepted(chat_id=chat_id, status="queued", turn_id=turn_id).model_dump_json())
    pipe.xadd(STREAM_NAME, fields=queue_record.model_dump(mode="json"))

    # Meta Güncelleme: Yeni chat ise her şeyi yaz, mevcut ise sadece güncellenme zamanını.
    meta_update = {"updated_at": now, "channel": channel}
    if not payload.chat_id:
        meta_update.update({"chat_id": chat_id, "user_id": payload.user_id, "created_at": now})

    pipe.hset(meta_key, mapping=meta_update)
    pipe.expire(meta_key, settings.redis_cache_ttl_seconds)

    # ZSET Update (Redis chat index — kept for fast active-session lookups)
    timestamp = datetime.now(timezone.utc).timestamp()
    pipe.zadd(get_key(CHAT_USER_INDEX_PREFIX, payload.user_id), {chat_id: timestamp})

    await pipe.execute()
    
    # Verify stop key was deleted (for debugging)
    stop_key_exists = await redis.exists(stop_key)
    if stop_key_exists:
        logger.warning("create_job: stop_key still exists after delete for chat %s — cleaning up explicitly", chat_id)
        await redis.delete(stop_key)

    # 4. Write-Through: Persist chat metadata to PostgreSQL
    try:
        await upsert_chat(chat_id=chat_id, user_id=payload.user_id)
    except Exception:
        # Non-fatal: Redis already accepted the job; DB write failure must not block the user.
        logger.exception("Write-Through upsert_chat failed for chat %s — continuing", chat_id)

    return JobCreateResponse(chat_id=chat_id, status="queued", created_at=now, turn_id=turn_id, generation_id=turn_id)


async def stop_job(redis: Redis, user_id: str, chat_id: str, turn_id: str | None = None) -> JobStopResponse:
    settings = await get_runtime_settings(redis, user_id)
    await ensure_chat_access(redis, user_id, chat_id)
    now = utc_now()
    resolved_turn_id = turn_id
    if not resolved_turn_id:
        resolved_turn_id = await redis.get(get_key(ACTIVE_TURN_KEY_PREFIX, chat_id))
    if not resolved_turn_id:
        state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
        resolved_turn_id = state.get("active_turn_id")

    if not resolved_turn_id:
        return JobStopResponse(chat_id=chat_id, turn_id=None, status="stopping", updated_at=now)

    pipe = redis.pipeline()
    pipe.set(get_key(TURN_STOP_KEY_PREFIX, resolved_turn_id), "1", ex=settings.stop_key_ttl_seconds)
    pipe.hset(get_key(CHAT_STATE_KEY_PREFIX, chat_id), mapping={"updated_at": now})
    pipe.hset(get_key(CHAT_META_KEY_PREFIX, chat_id), mapping={"updated_at": now})
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
        error=raw.get("error")
    )

# ---------------------------------------------------------------------------
# Chat List — Cache-Aside: PostgreSQL is source of truth
# ---------------------------------------------------------------------------

async def get_user_chats(redis: Redis, user_id: str) -> list[dict]:
    """Return the user's chat list.

    Redis is the hot path. PostgreSQL is used only when the Redis index or
    metadata has expired, then the recovered chats are hydrated back into Redis.
    """
    settings = await get_runtime_settings(redis, user_id)
    cache_ttl = settings.redis_cache_ttl_seconds
    chat_ids = await redis.zrevrange(get_key(CHAT_USER_INDEX_PREFIX, user_id), 0, -1)
    if chat_ids:
        pipe = redis.pipeline()
        for chat_id in chat_ids:
            pipe.hgetall(get_key(CHAT_META_KEY_PREFIX, chat_id))
        cached_chats = [meta for meta in await pipe.execute() if meta]
        if len(cached_chats) == len(chat_ids):
            return cached_chats

    # Redis cache miss or incomplete metadata: recover from durable storage.
    try:
        db_chats = await get_user_chats_db(user_id)
    except Exception:
        logger.exception("get_user_chats_db failed for user %s, falling back to Redis ZSET", user_id)
        return cached_chats if chat_ids else []

    if db_chats:
        hydrated_chats = []
        pipe = redis.pipeline()
        for chat in db_chats:
            chat_id = chat["chat_id"]
            meta = {
                "chat_id": chat_id,
                "user_id": chat["user_id"],
                "name": chat.get("name", "New Chat"),
                "created_at": chat["created_at"],
                "updated_at": chat["updated_at"],
                "channel": get_key(ROOM_USER_PREFIX, user_id),
            }
            hydrated_chats.append(meta)
            pipe.hset(get_key(CHAT_META_KEY_PREFIX, chat_id), mapping=meta)
            pipe.expire(get_key(CHAT_META_KEY_PREFIX, chat_id), cache_ttl)
            pipe.zadd(get_key(CHAT_USER_INDEX_PREFIX, user_id), {chat_id: _chat_timestamp(chat["updated_at"])})
        await pipe.execute()
        return hydrated_chats
    return []


def _chat_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


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
        if not prompt_already_persisted:
            history.append({"role": "user", "content": current_prompt})

    # Keep an explicit in-progress item even before the first token. The UI
    # uses it to preserve/rehydrate the active turn across chat navigation.
    assistant_entry = {"role": "assistant", "content": partial_text, "partial": True}
    if active_turn_id:
        assistant_entry["turn_id"] = active_turn_id
    if partial_thinking:
        assistant_entry["thinking"] = partial_thinking
    history.append(assistant_entry)

# ---------------------------------------------------------------------------
# Chat History — Cache-Aside with Hydration
# ---------------------------------------------------------------------------



async def get_chat_history(redis: Redis, user_id: str, chat_id: str) -> list[dict]:
    """Return the full message history for a chat.

    Strategy:
    1. Ensure the requesting user owns this chat.
    2. Check Redis for a cached history list (Cache Hit → return immediately).
    3. On Cache Miss, fetch from PostgreSQL and hydrate Redis with a 1-hour TTL.
    4. Append any in-progress (streaming) state from Redis state key so the
       UI always reflects the live generation.
    """
    await ensure_chat_access(redis, user_id, chat_id)
    settings = await get_runtime_settings(redis, user_id)
    history = await _load_history_with_hydration(redis, chat_id, settings.redis_cache_ttl_seconds)

    # Append in-progress tokens for the currently streaming message (if any)
    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    if state and state.get("status") == "processing":
        _append_processing_state(history, state)

    return history


async def _load_history_with_hydration(
    redis: Redis,
    chat_id: str,
    cache_ttl_seconds: int,
) -> list[dict]:
    """Core Cache-Aside logic shared between user and admin history reads."""
    history_key = get_key(CHAT_HISTORY_KEY_PREFIX, chat_id)
    raw_items = await redis.lrange(history_key, 0, -1)

    # Cache Hit
    if raw_items:
        history = []
        for item in raw_items:
            try:
                history.append(json.loads(item))
            except json.JSONDecodeError:
                continue
        return history

    # Cache Miss → fetch from PostgreSQL and hydrate Redis
    logger.info("Cache miss for chat history %s — hydrating from PostgreSQL", chat_id)
    try:
        db_history = await get_chat_history_db(chat_id)
    except Exception:
        logger.exception("get_chat_history_db failed for chat %s", chat_id)
        db_history = []

    if db_history:
        pipe = redis.pipeline()
        for msg in db_history:
            pipe.rpush(history_key, json.dumps(msg))
        pipe.expire(history_key, cache_ttl_seconds)
        await pipe.execute()
        logger.info(
            "Hydrated %d messages into Redis for chat %s (TTL=%ds)",
            len(db_history), chat_id, cache_ttl_seconds,
        )

    return db_history


async def get_chat_history_admin(redis: Redis, chat_id: str) -> list[dict]:
    meta_exists = await redis.exists(get_key(CHAT_META_KEY_PREFIX, chat_id))
    if not meta_exists:
        # Admin can still read from DB even if Redis TTL has expired
        db_history = await get_chat_history_db(chat_id)
        if not db_history:
            raise HTTPException(status_code=404, detail="Chat not found")
        return db_history

    settings = await get_runtime_settings(redis, "global")
    history = await _load_history_with_hydration(redis, chat_id, settings.redis_cache_ttl_seconds)

    state = await redis.hgetall(get_key(CHAT_STATE_KEY_PREFIX, chat_id))
    if state and state.get("status") == "processing":
        _append_processing_state(history, state)

    return history

# ---------------------------------------------------------------------------
# Rename / Delete
# ---------------------------------------------------------------------------

async def rename_chat(redis: Redis, user_id: str | None, chat_id: str, name: str, admin: bool = False) -> dict:
    meta_key = get_key(CHAT_META_KEY_PREFIX, chat_id)
    if not admin:
        meta = await ensure_chat_access(redis, user_id, chat_id)
        owner_id = meta.get("user_id", user_id)
    else:
        raw = await redis.hgetall(meta_key)
        if not raw:
            raise HTTPException(status_code=404, detail="Chat not found")
        owner_id = raw.get("user_id")

    updated_at = utc_now()
    try:
        await rename_chat_db(chat_id, name, updated_at)
    except Exception as exc:
        logger.exception("Could not persist chat rename for %s", chat_id)
        raise HTTPException(status_code=503, detail="Sohbet adı kalıcı olarak kaydedilemedi.") from exc

    await redis.hset(meta_key, mapping={"name": name, "updated_at": updated_at})

    # Broadcast to other devices
    if owner_id:
        channel = get_key(ROOM_USER_PREFIX, owner_id)
        await redis.publish(channel, StreamEvent.chat_rename(chat_id=chat_id, name=name).model_dump_json())

    return {"status": "ok", "chat_id": chat_id, "name": name}


async def delete_chat(redis: Redis, user_id: str | None, chat_id: str, admin: bool = False) -> dict:
    meta_key = get_key(CHAT_META_KEY_PREFIX, chat_id)

    if not admin:
        meta = await ensure_chat_access(redis, user_id, chat_id)
        owner_id = meta.get("user_id", user_id)
    else:
        raw = await redis.hgetall(meta_key)
        if not raw:
            raise HTTPException(status_code=404, detail="Chat not found")
        owner_id = raw.get("user_id")

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

    # Broadcast to other devices
    if owner_id:
        channel = get_key(ROOM_USER_PREFIX, owner_id)
        await redis.publish(channel, StreamEvent.chat_delete(chat_id=chat_id).model_dump_json())

    return {"status": "deleted", "chat_id": chat_id}


async def get_all_chats_admin(redis: Redis) -> list[dict]:
    """Scan all chat meta keys for admin panel."""
    pattern = f"{CHAT_META_KEY_PREFIX}*"
    cursor = 0
    all_keys = []
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=200)
        all_keys.extend(keys)
        if cursor == 0:
            break

    if not all_keys:
        return []

    pipe = redis.pipeline()
    for key in all_keys:
        pipe.hgetall(key)
    results = await pipe.execute()

    chats = [meta for meta in results if meta]
    chats.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return chats
