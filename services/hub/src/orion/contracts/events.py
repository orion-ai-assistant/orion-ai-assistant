import base64
from typing import Any, Literal

from pydantic import BaseModel, Field


class StreamEvent(BaseModel):
    type: Literal["accepted", "token", "thinking", "done", "error", "user_message", "chat_rename", "chat_delete", "audio"]
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
    def done(cls, chat_id: str, status: str, metrics: dict[str, Any] | None = None) -> "StreamEvent":
        data: dict[str, Any] = {"status": status}
        if metrics:
            data.update(metrics)
        return cls(type="done", chat_id=chat_id, data=data)

    @classmethod
    def error(
        cls,
        chat_id: str,
        message: str,
        metrics: dict[str, Any] | None = None,
    ) -> "StreamEvent":
        data: dict[str, Any] = {"message": message, "status": "failed"}
        if metrics:
            data.update(metrics)
        return cls(type="error", chat_id=chat_id, data=data)
        
    @classmethod
    def user_message(cls, chat_id: str, text: str) -> "StreamEvent":
        return cls(type="user_message", chat_id=chat_id, data={"text": text})

    @classmethod
    def chat_rename(cls, chat_id: str, name: str) -> "StreamEvent":
        return cls(type="chat_rename", chat_id=chat_id, data={"name": name})

    @classmethod
    def chat_delete(cls, chat_id: str) -> "StreamEvent":
        return cls(type="chat_delete", chat_id=chat_id, data={})

    @classmethod
    def audio(
        cls,
        chat_id: str,
        audio_data: str | bytes,
        format: str = "wav",
        sample_rate: int | None = None,
        text: str | None = None,
    ) -> "StreamEvent":
        if isinstance(audio_data, bytes):
            encoded = base64.b64encode(audio_data).decode("utf-8")
        else:
            encoded = audio_data

        payload: dict[str, Any] = {
            "audio": encoded,
            "format": format,
        }
        if sample_rate is not None:
            payload["sample_rate"] = sample_rate
        if text is not None:
            payload["text"] = text

        return cls(type="audio", chat_id=chat_id, data=payload)

