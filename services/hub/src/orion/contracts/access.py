from typing import Literal

from pydantic import BaseModel, Field


ChatAccessState = Literal["ok", "not_found", "forbidden"]


class ChatAccessResult(BaseModel):
    state: ChatAccessState
    meta: dict[str, str] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.state == "ok"
