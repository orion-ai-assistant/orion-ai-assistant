from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from api.app.core.message_protocol import MessageKind
from api.app.core.stream_constants import MessageStatus, PayloadEventName, SenderType, StreamEventType


class OutgoingSocketMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    sender: SenderType
    content: str
    kind: MessageKind
    status: MessageStatus = MessageStatus.OK
    is_chunk: bool = False
    is_done: bool = False
    payload_event: PayloadEventName | StreamEventType | str | None = None
    metadata: dict[str, Any] | None = None
    is_thinking: bool = False
    is_tool: bool = False
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_output: str | None = None
    message_id: str | None = None
    client_message_id: str | None = None
    reason: str | None = None
    room: str | None = None
    chat_created: bool | None = None