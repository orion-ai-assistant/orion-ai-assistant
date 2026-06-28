from typing import Any, Literal

from pydantic import BaseModel, Field


class StreamEvent(BaseModel):
    type: Literal["accepted", "token", "thinking", "done", "error", "user_message", "chat_rename", "chat_delete"]
    chat_id: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def accepted(cls, chat_id: str, status: str = "queued") -> "StreamEvent":
        return cls(type="accepted", chat_id=chat_id, data={"status": status})

    @classmethod
    def thinking(cls, chat_id: str, token: str) -> "StreamEvent":
        return cls(type="thinking", chat_id=chat_id, data={"token": token})

    @classmethod
    def token(cls, chat_id: str, token: str) -> "StreamEvent":
        return cls(type="token", chat_id=chat_id, data={"token": token})

    @classmethod
    def done(cls, chat_id: str, status: str) -> "StreamEvent":
        return cls(type="done", chat_id=chat_id, data={"status": status})

    @classmethod
    def error(cls, chat_id: str, message: str) -> "StreamEvent":
        return cls(type="error", chat_id=chat_id, data={"message": message})
        
    @classmethod
    def user_message(cls, chat_id: str, text: str) -> "StreamEvent":
        return cls(type="user_message", chat_id=chat_id, data={"text": text})

    @classmethod
    def chat_rename(cls, chat_id: str, name: str) -> "StreamEvent":
        return cls(type="chat_rename", chat_id=chat_id, data={"name": name})

    @classmethod
    def chat_delete(cls, chat_id: str) -> "StreamEvent":
        return cls(type="chat_delete", chat_id=chat_id, data={})
