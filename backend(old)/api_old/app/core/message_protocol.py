from __future__ import annotations

from enum import Enum

from api.app.core.stream_constants import SocketEventName


class IncomingEventType(str, Enum):
    MESSAGE = "chat:message"
    JOIN = "chat:join"
    CANCEL = "chat:agent:cancel"


class OutgoingSocketEventType(str, Enum):
    CONNECTED = SocketEventName.CONNECTED.value
    CHAT_MESSAGE_RECEIVED = SocketEventName.CHAT_MESSAGE_RECEIVED.value
    CHAT_AGENT_PROGRESS = SocketEventName.CHAT_AGENT_PROGRESS.value
    CHAT_AGENT_DONE = SocketEventName.CHAT_AGENT_DONE.value
    CHAT_AGENT_RESPONSE = SocketEventName.CHAT_AGENT_RESPONSE.value
    CHAT_AGENT_ERROR = SocketEventName.CHAT_AGENT_ERROR.value
    CHAT_ERROR = SocketEventName.CHAT_ERROR.value
    CHAT_JOINED = SocketEventName.CHAT_JOINED.value
    CHAT_CREATED = SocketEventName.CHAT_CREATED.value


class MessageKind(str, Enum):
    SYSTEM = "system"
    USER_ECHO = "user_echo"
    STATUS = "status"
    THINKING = "thinking"
    STREAM = "stream"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL = "final"
    ERROR = "error"


class ParseErrorCode(str, Enum):
    INVALID_JSON = "invalid_json"
    INVALID_PAYLOAD = "invalid_payload"
    MISSING_PAYLOAD = "missing_payload"
    MISSING_CHAT_ID = "missing_chat_id"
    EMPTY_MESSAGE = "empty_message"