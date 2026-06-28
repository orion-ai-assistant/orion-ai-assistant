"""
Redis-backed chat store.

Replaces mock_store.py's in-memory dicts with Redis so that
chat/message/file data is shared across all workers and containers.

Key schema
----------
chat:{chat_id}              Hash   — chat metadata (created_by, created_at)
chat:{chat_id}:members      Set    — user_ids that may access the chat
chat:{chat_id}:messages     List   — JSON-encoded message dicts (newest at tail)
chat:{chat_id}:files        List   — JSON-encoded file dicts

All public functions are **async** because the API runs on an ASGI event loop.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from config.settings.api_server import REDIS_URL
from log import Logger

logger = Logger(__file__)

# ── Redis connection pool (lazy init) ────────────────────────────────────────
_pool: aioredis.Redis | None = None

# Son N mesajı tut — sonsuz büyümeyi engelle
_MAX_MESSAGES_PER_CHAT: int = 200
_MAX_FILES_PER_CHAT: int = 100


def _get_redis() -> aioredis.Redis:
    """Return (and lazily create) the module-level async Redis client."""
    global _pool
    if _pool is None:
        url = REDIS_URL or "redis://localhost:6379"
        _pool = aioredis.from_url(url, decode_responses=True)
        logger.info(lambda: f"Redis store connected: {url}")
    return _pool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Chat CRUD ────────────────────────────────────────────────────────────────

async def create_chat(chat_id: str, created_by: str) -> Dict[str, Any]:
    """Create a new chat and automatically add the creator as a member."""
    r = _get_redis()

    key = f"chat:{chat_id}"
    exists = await r.exists(key)
    if exists:
        raise ValueError(f"Chat already exists: {chat_id}")

    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "chat_id": chat_id,
        "created_by": created_by,
        "created_at": _now_iso(),
    })
    # Creator otomatik olarak üye olur
    pipe.sadd(f"chat:{chat_id}:members", created_by)
    await pipe.execute()

    return await get_chat(chat_id)  # type: ignore[return-value]


async def get_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    r = _get_redis()
    data = await r.hgetall(f"chat:{chat_id}")
    if not data:
        return None

    members = await r.smembers(f"chat:{chat_id}:members")
    return {
        "chat_id": data.get("chat_id", chat_id),
        "created_by": data.get("created_by", "system"),
        "members": sorted(members),
        "created_at": data.get("created_at", ""),
    }


async def chat_exists(chat_id: str) -> bool:
    r = _get_redis()
    return bool(await r.exists(f"chat:{chat_id}"))


async def add_chat_member(chat_id: str, user_id: str) -> None:
    r = _get_redis()
    if not await r.exists(f"chat:{chat_id}"):
        raise ValueError(f"Chat not found: {chat_id}")
    await r.sadd(f"chat:{chat_id}:members", user_id)


async def user_can_access_chat(chat_id: str, user_id: str) -> bool:
    r = _get_redis()
    return bool(await r.sismember(f"chat:{chat_id}:members", user_id))


# ── Messages ─────────────────────────────────────────────────────────────────

async def add_message(
    chat_id: str,
    sender: str,
    content: str,
    message_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    r = _get_redis()

    # Chat yoksa otomatik oluştur (ensure_chat davranışı)
    if not await r.exists(f"chat:{chat_id}"):
        await r.hset(f"chat:{chat_id}", mapping={
            "chat_id": chat_id,
            "created_by": "system",
            "created_at": _now_iso(),
        })

    message: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "sender": sender,
        "message_type": message_type,
        "content": content,
        "metadata": metadata or {},
        "created_at": _now_iso(),
    }

    msg_key = f"chat:{chat_id}:messages"
    pipe = r.pipeline()
    pipe.rpush(msg_key, json.dumps(message, ensure_ascii=False))
    # LTRIM: sadece son N mesajı tut, bellek taşmasını engelle
    pipe.ltrim(msg_key, -_MAX_MESSAGES_PER_CHAT, -1)
    await pipe.execute()

    return message


async def get_messages(
    chat_id: str,
    limit: int = 50,
    before_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    r = _get_redis()
    raw_items = await r.lrange(f"chat:{chat_id}:messages", 0, -1)
    items: List[Dict[str, Any]] = [json.loads(item) for item in raw_items]

    if before_id:
        before_index = next(
            (i for i, m in enumerate(items) if m["id"] == before_id),
            None,
        )
        if before_index is not None:
            items = items[:before_index]

    if limit <= 0:
        return []

    return items[-limit:]


async def get_all_messages(chat_id: str) -> List[Dict[str, Any]]:
    r = _get_redis()
    raw_items = await r.lrange(f"chat:{chat_id}:messages", 0, -1)
    return [json.loads(item) for item in raw_items]


async def chat_has_activity(chat_id: str) -> bool:
    r = _get_redis()
    length = await r.llen(f"chat:{chat_id}:messages")
    return length > 0


# ── Files ─────────────────────────────────────────────────────────────────────

async def add_file(
    chat_id: str,
    filename: Optional[str],
    content_type: Optional[str],
    size_bytes: int,
    description: str = "",
) -> Dict[str, Any]:
    r = _get_redis()

    # Chat yoksa otomatik oluştur
    if not await r.exists(f"chat:{chat_id}"):
        await r.hset(f"chat:{chat_id}", mapping={
            "chat_id": chat_id,
            "created_by": "system",
            "created_at": _now_iso(),
        })

    file_item: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "filename": filename or "unknown",
        "content_type": content_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "description": description,
        "created_at": _now_iso(),
    }

    file_key = f"chat:{chat_id}:files"
    pipe = r.pipeline()
    pipe.rpush(file_key, json.dumps(file_item, ensure_ascii=False))
    pipe.ltrim(file_key, -_MAX_FILES_PER_CHAT, -1)
    await pipe.execute()

    return file_item


async def get_files(chat_id: str) -> List[Dict[str, Any]]:
    r = _get_redis()
    raw_items = await r.lrange(f"chat:{chat_id}:files", 0, -1)
    return [json.loads(item) for item in raw_items]
