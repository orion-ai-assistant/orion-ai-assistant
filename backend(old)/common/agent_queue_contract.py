from __future__ import annotations

from typing import Final


AGENT_QUEUE_KEY: Final[str] = "agent:queue"
AGENT_EVENTS_PREFIX: Final[str] = "agent:events"
AGENT_CANCEL_VERSION_PREFIX: Final[str] = "agent:cancel:version"


def events_channel(chat_id: str) -> str:
    return f"{AGENT_EVENTS_PREFIX}:{chat_id}"


def cancel_version_key(chat_id: str) -> str:
    return f"{AGENT_CANCEL_VERSION_PREFIX}:{chat_id}"
