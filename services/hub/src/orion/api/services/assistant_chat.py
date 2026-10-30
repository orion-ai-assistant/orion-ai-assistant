"""The user's one "Asistanla konuş" conversation, shared by all of their devices.

PostgreSQL (`orion_chats.assistant`) is the source of truth and Redis caches the
pointer. A new pointer is announced on the user's stream (`assistant_chat`), so
a device showing another conversation switches to it. Whichever device speaks
last is answered: a new turn supersedes the chat's active one (the worker stops it).
"""
import logging

from fastapi import HTTPException
from redis.asyncio import Redis

from orion.contracts.constants import ASSISTANT_CHAT_KEY_PREFIX, ROOM_USER_PREFIX
from orion.contracts.events import StreamEvent
from orion.contracts.http import JobCreateRequest, JobCreateResponse
from orion.kernel.config import get_runtime_settings
from orion.kernel.registry import get_assistant_chat_db, set_assistant_chat_db

logger = logging.getLogger(__name__)


def _key(user_id: str) -> str:
    return f'{ASSISTANT_CHAT_KEY_PREFIX}{user_id}'


def _lock(redis: Redis, user_id: str):
    # Two devices speaking at once must not open two assistant chats.
    return redis.lock(f'{ASSISTANT_CHAT_KEY_PREFIX}lock:{user_id}', timeout=120, blocking_timeout=10)


async def _cache(redis: Redis, user_id: str, chat_id: str) -> None:
    settings = await get_runtime_settings(redis, user_id)
    await redis.set(_key(user_id), chat_id, ex=settings.redis_cache_ttl_seconds)


async def current_assistant_chat(redis: Redis, user_id: str) -> str | None:
    from orion.api.services.job_service import check_chat_access
    cached = await redis.get(_key(user_id))
    if cached:
        if (await check_chat_access(redis, user_id, cached)).state == 'ok':
            return cached
        await redis.delete(_key(user_id))
    try:
        chat_id = await get_assistant_chat_db(user_id)
    except Exception as exc:
        # Not "no chat yet": a new chat would split the conversation in two.
        logger.exception('Could not read the assistant chat of %s', user_id)
        raise HTTPException(503, 'Asistan sohbeti okunamadı. Biraz sonra tekrar deneyin.') from exc
    if chat_id:
        await _cache(redis, user_id, chat_id)
    return chat_id


async def forget_assistant_chat(redis: Redis, user_id: str, chat_id: str) -> None:
    """A deleted chat stops being the assistant chat (its DB row is already gone)."""
    if await redis.get(_key(user_id)) == chat_id:
        await redis.delete(_key(user_id))


async def _adopt(redis: Redis, user_id: str, chat_id: str) -> None:
    try:
        await set_assistant_chat_db(chat_id, user_id)
    except Exception:
        # Redis still shares it while cached; the next adoption persists it.
        logger.exception('Could not persist the assistant chat %s of %s', chat_id, user_id)
    await _cache(redis, user_id, chat_id)
    await redis.publish(f'{ROOM_USER_PREFIX}{user_id}', StreamEvent.assistant_chat(chat_id).model_dump_json())


async def claim_assistant_chat(redis: Redis, user_id: str, chat_id: str) -> str:
    """A device's own earlier assistant chat becomes the shared one, unless one exists."""
    from orion.api.services.job_service import ensure_chat_access
    async with _lock(redis, user_id):
        current = await current_assistant_chat(redis, user_id)
        if current:
            return current
        await ensure_chat_access(redis, user_id, chat_id)
        await _adopt(redis, user_id, chat_id)
        return chat_id


async def create_assistant_message(redis: Redis, payload: JobCreateRequest) -> JobCreateResponse:
    """Post into the shared assistant chat, whichever chat the device showed.

    The device's chat (an older client's own, or one reset elsewhere meanwhile)
    is only used when there is no shared chat yet and it still exists.
    """
    from orion.api.services.job_service import check_chat_access, create_job
    user_id = payload.user_id
    device_chat = payload.chat_id
    for attempt in range(3):
        async with _lock(redis, user_id):
            chat_id = await current_assistant_chat(redis, user_id)
            if not chat_id and device_chat:
                if (await check_chat_access(redis, user_id, device_chat)).state == 'ok':
                    chat_id = device_chat
                    await _adopt(redis, user_id, chat_id)
            if not chat_id:
                payload.chat_id = None
                created = await create_job(redis, payload)
                await _adopt(redis, user_id, created.chat_id)
                return created
        async with redis.lock(f'chat:lifecycle:{chat_id}', timeout=120, blocking_timeout=10):
            # Reset ("Sohbeti sıfırla") between the two locks: look for the chat again.
            if attempt < 2 and (await check_chat_access(redis, user_id, chat_id)).state == 'not_found':
                device_chat = None
                continue
            payload.chat_id = chat_id
            payload.tool_selection = None  # A draft's tools only apply to a new chat.
            return await create_job(redis, payload)
