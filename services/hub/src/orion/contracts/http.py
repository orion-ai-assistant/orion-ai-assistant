from typing import Any, Literal

from pydantic import BaseModel, Field


class JobInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    images: list[str] | None = Field(default=None, description="List of base64 encoded images or image URLs")
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    chat_id: str | None = Field(default=None, max_length=120)
    input: JobInput
    stream_mode: Literal["once", "continuous"] = "once"


class JobCreateResponse(BaseModel):
    chat_id: str
    status: Literal["queued", "failed"]
    created_at: str


class JobStatusResponse(BaseModel):
    chat_id: str
    status: Literal["queued", "processing", "completed", "failed", "stopped"]
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None


class JobStopResponse(BaseModel):
    chat_id: str
    status: Literal["stopping"]
    updated_at: str
