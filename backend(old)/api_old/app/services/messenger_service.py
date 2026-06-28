from __future__ import annotations

from enum import Enum
from typing import Any

from api.app.core.message_schemas import OutgoingSocketMessage
from api.app.shared_state import manager
from api.app.core.stream_constants import (
    SocketEventName,
)


def _enum_value(value: str | Enum | None) -> str | None:
    if isinstance(value, Enum):
        return str(value.value)
    return value


class Messenger:
    @staticmethod
    def _wire_payload(
        message: OutgoingSocketMessage,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        payload = message.model_dump(exclude_none=True)
        payload["session_id"] = session_id
        payload["chat_id"] = chat_id
        payload["user_id"] = user_id

        payload_event = payload.pop("payload_event", None)
        if payload_event is not None:
            payload["event"] = _enum_value(payload_event)

        payload.pop("kind", None)
        return payload

    @classmethod
    async def send_direct_message(
        cls,
        sid: str,
        socket_event: SocketEventName | str,
        message: OutgoingSocketMessage,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> None:
        payload = cls._wire_payload(message, session_id=session_id, chat_id=chat_id, user_id=user_id)
        await manager.send_to(sid, payload, event=_enum_value(socket_event) or SocketEventName.CONNECTED.value)

    @classmethod
    async def broadcast_message(
        cls,
        chat_id: str,
        socket_event: SocketEventName | str,
        message: OutgoingSocketMessage,
        session_id: str,
        user_id: str,
        skip_sid: str | None = None,
    ) -> None:
        payload = cls._wire_payload(message, session_id=session_id, chat_id=chat_id, user_id=user_id)
        await manager.broadcast_to_chat(
            chat_id,
            payload,
            event=_enum_value(socket_event) or SocketEventName.CHAT_MESSAGE_RECEIVED.value,
            skip_sid=skip_sid,
        )
