from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from log import Logger
from ..core.stream_constants import SocketEventName
from ..services.agent_service import stream_agent_reply
from ..services.message_factory import MessageFactory
from ..services.messenger_service import Messenger
from ..store.redis_store import add_message

logger = Logger(__file__)


def _read_mock_chunk_delay_seconds() -> float:
    raw = os.getenv("WS_MOCK_CHUNK_DELAY_SECONDS", "0.0")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


MOCK_CHUNK_DELAY_SECONDS = _read_mock_chunk_delay_seconds()

_active_agent_tasks: dict[tuple[str, str], Any] = {}


def _build_thread_id(user_id: str, chat_id: str) -> str:
    """Build deterministic UUID thread id from user+chat identity."""
    raw = f"{user_id}:{chat_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


async def _send_mock_stream_to_user(user_id: str, sid: str, chat_id: str, user_msg: str) -> str:
    short_answer = f"OK: mesajın alındı. Kısa mock yanıt: {user_msg[:120]}"
    chunks = ["OK: mesajın alındı. ", f"Kısa mock yanıt: {user_msg[:120]}"]

    for idx, chunk in enumerate(chunks):
        await Messenger.broadcast_message(
            chat_id=chat_id,
            socket_event=SocketEventName.CHAT_AGENT_PROGRESS,
            message=MessageFactory.agent_text(chunk, is_chunk=True),
            session_id=sid,
            user_id=user_id,
        )
        if idx < len(chunks) - 1 and MOCK_CHUNK_DELAY_SECONDS > 0:
            await asyncio.sleep(MOCK_CHUNK_DELAY_SECONDS)

    await Messenger.broadcast_message(
        chat_id=chat_id,
        socket_event=SocketEventName.CHAT_AGENT_DONE,
        message=MessageFactory.final_done(),
        session_id=sid,
        user_id=user_id,
    )
    return short_answer


async def run_real_agent_task(sid: str, user_id: str, chat_id: str, messages: list) -> None:
    """Gerçek agent streaming'i background task olarak çalıştırır ve sonucu kaydeder."""
    try:
        thread_id = _build_thread_id(user_id, chat_id)
        final_text, final_messages = await stream_agent_reply(
            sid,
            messages,
            user_id=user_id,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        from ..services.session_service import save_session
        await save_session(chat_id, final_messages)
        if final_text.strip():
            bot_entry = await add_message(
                chat_id=chat_id,
                sender="assistant",
                content=final_text,
                metadata={"session_id": sid, "user_id": user_id},
            )
            logger.debug(lambda: f"[{sid}] Assistant stored (user={user_id}, chat={chat_id}, id={bot_entry['id']})"
            )
    except Exception as exc:
        await Messenger.send_direct_message(
            sid=sid,
            socket_event=SocketEventName.CHAT_ERROR,
            message=MessageFactory.agent_error(f"Agent çalışma hatası: {exc}"),
            chat_id=chat_id,
            user_id=user_id,
            session_id=sid,
        )
    finally:
        _active_agent_tasks.pop((sid, chat_id), None)


async def run_mock_agent_task(sid: str, user_id: str, chat_id: str, user_msg: str) -> None:
    """Mock task olarak gelen mesajı stream formatında gönderir."""
    try:
        await _send_mock_stream_to_user(user_id, sid, chat_id, user_msg)
    finally:
        _active_agent_tasks.pop((sid, chat_id), None)


async def start_agent_background_task(
    sio,
    ws_enable_real_agent: bool,
    sid: str,
    user_id: str,
    chat_id: str,
    user_msg: str,
) -> None:
    """WS_ENABLE_REAL_AGENT durumuna göre doğru background task'i başlatır."""
    cancel_agent_task_for_session(sid, chat_id)

    if ws_enable_real_agent:
        from ..services.session_service import get_messages_for_agent

        messages = await get_messages_for_agent(chat_id, str(user_msg))
        task = sio.start_background_task(run_real_agent_task, sid, user_id, chat_id, messages)
        _active_agent_tasks[(sid, chat_id)] = task
    else:
        task = sio.start_background_task(run_mock_agent_task, sid, user_id, chat_id, str(user_msg))
        _active_agent_tasks[(sid, chat_id)] = task


def cancel_agent_task_for_session(sid: str, chat_id: str | None = None) -> None:
    if chat_id is not None:
        keys = [(sid, chat_id)]
    else:
        keys = [key for key in _active_agent_tasks if key[0] == sid]

    for key in keys:
        task = _active_agent_tasks.pop(key, None)
        if task is None:
            continue

        cancel_fn = getattr(task, "cancel", None)
        if callable(cancel_fn):
            try:
                cancel_fn()
            except Exception as exc:
                logger.warning(lambda: f"Could not cancel agent task for sid={sid}, chat_id={key[1]}: {exc}")
