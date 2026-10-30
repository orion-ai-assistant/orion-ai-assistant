from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from orion.contracts.tools import ToolSelection


class Attachment(BaseModel):
    id: str | None = None
    name: str = "Dosya"
    mime_type: str
    data: str | None = None
    media_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    media_path: str | None = None
    size: int | None = Field(default=None, ge=0)
    isText: bool = False

    @model_validator(mode="after")
    def validate_media(self):
        if self.media_id:
            if self.isText or self.data is not None:
                raise ValueError('Media references cannot contain inline data or text')
            if not self.mime_type.startswith(('image/', 'audio/', 'video/')):
                raise ValueError('Unsupported media reference MIME type')
            return self
        if self.data is None:
            raise ValueError('Attachment data or media_id is required')
        if not self.isText:
            if self.data.startswith(('http://', 'https://')) and self.mime_type.startswith('image/'):
                return self
            header, separator, _ = self.data.partition(",")
            if not separator or not header.startswith("data:") or not header.endswith(";base64"):
                raise ValueError("Media attachments must contain a base64 data URL")
            actual_mime = header[5:].split(";", 1)[0].lower()
            if actual_mime != self.mime_type.lower():
                raise ValueError("Attachment MIME type does not match its data URL")
            if not actual_mime.startswith(("image/", "audio/", "video/")):
                raise ValueError("Unsupported attachment MIME type")
            self.mime_type = actual_mime
        return self


class JobInput(BaseModel):
    text: str = Field(min_length=1)
    attachments: list[Attachment] | None = None
    images: list[str] | None = Field(default=None, description="List of base64 encoded images or image URLs")
    audio: bool | None = Field(default=None, description="Whether to generate audio/TTS for response")
    speech_mode: Literal["whole", "adaptive"] = "whole"
    voice: str | None = Field(default=None, description="Voice ID or name for TTS")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_attachments(cls, value):
        if not isinstance(value, dict) or value.get("attachments") is not None:
            return value
        metadata = value.get("metadata") or {}
        if not metadata.get("attachments"):
            return value
        value = dict(value)
        value["attachments"] = [
            {**item, "mime_type": item.get("mime_type") or (
                "text/plain" if item.get("isText") else item["data"].split(";", 1)[0][5:]
            )}
            for item in metadata["attachments"]
        ]
        if "display_text" in metadata:
            value["text"] = metadata["display_text"] or "Ekleri incele."
        return value

    def display_attachments(self) -> list[dict[str, Any]]:
        if self.attachments is not None:
            return [item.model_dump(exclude_none=True) for item in self.attachments]
        return [{"data": image, "name": "Dosya"} for image in self.images or []]



class JobCreateRequest(BaseModel):
    origin_session_id: str | None = Field(default=None, min_length=1, max_length=120)
    tool_selection: ToolSelection | None = None
    user_id: str = Field(min_length=1, max_length=120)
    chat_id: str | None = Field(default=None, max_length=120)
    # "Asistanla konuş": every device of the user shares one conversation.
    assistant: bool = False
    input: JobInput
    stream_mode: Literal["once", "continuous"] = "once"


class JobCreateResponse(BaseModel):
    chat_id: str
    status: Literal["queued", "failed"]
    created_at: str
    turn_id: str | None = None
    generation_id: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class JobStatusResponse(BaseModel):
    chat_id: str
    turn_id: str | None = None
    retry_text: str | None = None
    status: Literal["queued", "processing", "completed", "failed", "stopped"]
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None


class JobStopResponse(BaseModel):
    chat_id: str
    turn_id: str | None = None
    status: Literal["stopping"]
    updated_at: str
