from typing import Literal

from pydantic import BaseModel

from orion.contracts.http import JobCreateRequest


class JobQueueRecord(BaseModel):
    user_id: str
    chat_id: str
    channel: str
    created_at: str
    stream_mode: Literal["once", "continuous"] = "once"
    payload: str

    def request_payload(self) -> JobCreateRequest:
        return JobCreateRequest.model_validate_json(self.payload)
