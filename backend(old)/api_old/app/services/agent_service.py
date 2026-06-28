"""Legacy agent service shim.

HTTP transport removed. Queue -> worker -> Redis Pub/Sub is the active path.
"""
from __future__ import annotations

from log import Logger

logger = Logger(__file__)

async def stream_agent_reply(
    session_id: str,
    messages: list,
    user_id: str | None = None,
    chat_id: str = "",
    thread_id: str | None = None,
) -> tuple[str, list]:
    logger.warning(
        "stream_agent_reply called on deprecated HTTP transport path "
        f"(session_id={session_id}, chat_id={chat_id}, user_id={user_id}, thread_id={thread_id})"
    )
    raise RuntimeError("HTTP agent transport was removed. Use queue + worker + Redis Pub/Sub path.")
