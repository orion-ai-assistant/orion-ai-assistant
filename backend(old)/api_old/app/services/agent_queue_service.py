from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from common.agent_queue_contract import AGENT_QUEUE_KEY, cancel_version_key, events_channel
from api.app.store.redis_client import get_redis_client
from log import Logger

logger = Logger(__file__)


async def enqueue_agent_task(
    session_id: str,
    user_id: str,
    chat_id: str,
    messages: list[dict[str, Any]],
) -> str:
    r = get_redis_client()
    request_id = str(uuid.uuid4())
    cancel_value = await r.get(cancel_version_key(chat_id))
    try:
        cancel_version = int(cancel_value) if cancel_value is not None else 0
    except ValueError:
        cancel_version = 0
    payload = {
        "task_id": str(uuid.uuid4()),
        "request_id": request_id,
        "session_id": session_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "cancel_version": cancel_version,
        "messages": messages,
    }
    await r.rpush(AGENT_QUEUE_KEY, json.dumps(payload, ensure_ascii=False))
    logger.debug(lambda: f"Enqueued agent task for chat={chat_id} session={session_id} request_id={request_id}")
    return request_id


async def dequeue_agent_task(timeout: int = 1) -> Optional[Dict[str, Any]]:
    r = get_redis_client()
    data = await r.blpop(AGENT_QUEUE_KEY, timeout=timeout)
    if not data:
        return None

    _, payload_json = data
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        logger.error("Invalid JSON in agent queue payload")
        return None

    return payload


async def publish_agent_event(chat_id: str, event: dict[str, Any]) -> None:
    r = get_redis_client()
    await r.publish(events_channel(chat_id), json.dumps(event, ensure_ascii=False))


def get_events_pattern() -> str:
    return f"{events_channel('*')}"


async def get_cancel_version(chat_id: str) -> int:
    r = get_redis_client()
    value = await r.get(cancel_version_key(chat_id))
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


async def bump_cancel_version(chat_id: str) -> int:
    r = get_redis_client()
    return int(await r.incr(cancel_version_key(chat_id)))


async def is_task_canceled(chat_id: str, task_cancel_version: int) -> bool:
    current = await get_cancel_version(chat_id)
    return current > task_cancel_version


async def process_agent_task(task: Dict[str, Any]) -> None:
    # Legacy path kept for compatibility with old queue_worker entrypoint.
    session_id = task.get("session_id")
    user_id = task.get("user_id")
    chat_id = task.get("chat_id")
    messages = task.get("messages")

    if not (session_id and user_id and chat_id and isinstance(messages, list)):
        logger.warning(lambda: f"Invalid agent task payload, skipping: {task}")
        return

    try:
        from api.app.services.agent_service import stream_agent_reply
        from api.app.services.session_service import save_session

        final_text, final_messages = await stream_agent_reply(
            session_id=session_id,
            messages=messages,
            user_id=user_id,
            chat_id=chat_id,
            thread_id=f"{user_id}:{chat_id}",
        )

        if final_text.strip() and final_messages:
            await save_session(chat_id, final_messages)

    except Exception as exc:
        logger.error(lambda: f"Failed to process agent queued task for session={session_id}: {exc}")
