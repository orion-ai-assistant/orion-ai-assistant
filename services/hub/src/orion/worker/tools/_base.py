from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    chat_id: str
    turn_id: str
    cancelled: Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any, ToolContext], Awaitable[Any]]
    timeout_seconds: float = 30

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.id, "description": self.description,
            "parameters": self.input_model.model_json_schema(),
        }}


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    description: str
    tools: tuple[Tool, ...]
