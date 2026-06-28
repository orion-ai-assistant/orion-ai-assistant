from enum import Enum

class StreamEventType(str, Enum):
    TEXT = "text"
    THINK = "think"
    STATUS = "status"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL = "final"

class AgentRole(str, Enum):
    ASSISTANT = "assistant"
    AI = "ai"
    MODEL = "model"
    AIMESSAGE = "aimessage"
    AIMESSAGECHUNK = "aimessagechunk"

class SenderType(str, Enum):
    BOT_THINKING = "Bot (Thinking)"
    BOT_STREAM = "Bot (Stream)"
    BOT_TOOL = "Bot (Tool)"
    BOT = "Bot"
    SYSTEM = "System"
    ERROR = "Error"
    USER = "User"
    USER_ECHO = "User (Echo)"

class SocketEventName(str, Enum):
    CHAT_MESSAGE_RECEIVED = "chat:message:received"
    CHAT_AGENT_PROGRESS = "chat:agent:progress"
    CHAT_AGENT_DONE = "chat:agent:done"
    CHAT_AGENT_RESPONSE = "chat:agent:response"
    CHAT_AGENT_ERROR = "chat:agent:error"
    CHAT_AGENT_CANCELED = "chat:agent:canceled"
    CHAT_ERROR = "chat:error"
    CHAT_JOINED = "chat:joined"
    CHAT_CREATED = "chat:created"
    CONNECTED = "connected"


class PayloadEventName(str, Enum):
    CONNECTED = "connected"
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_ECHO = "message_echo"
    INVALID_FORMAT = "invalid_format"
    EMPTY_MESSAGE = "empty_message"
    CHAT_JOINED = "chat_joined"
    CHAT_CREATED = "chat_created"
class MessageStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
