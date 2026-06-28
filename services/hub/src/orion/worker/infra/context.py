from redis.asyncio import Redis

from orion.contracts.events import StreamEvent
from orion.contracts.http import JobCreateRequest
from orion.contracts.queue import JobQueueRecord


class JobContext:
    def __init__(self, record: JobQueueRecord, request: JobCreateRequest, redis: Redis):
        self.record = record
        self.request = request
        self.redis = redis

    @classmethod
    def from_stream_fields(cls, fields: dict[str, str], redis: Redis) -> "JobContext":
        record = JobQueueRecord.model_validate(fields)
        request = record.request_payload()
        return cls(record=record, request=request, redis=redis)

    @property
    def chat_id(self) -> str:
        return self.record.chat_id

    @property
    def user_id(self) -> str:
        return self.record.user_id

    @property
    def channel(self) -> str:
        return self.record.channel

    @property
    def prompt(self) -> str:
        return self.request.input.text

    @property
    def stream_mode(self) -> str:
        return self.request.stream_mode

    @property
    def images(self) -> list[str] | None:
        return self.request.input.images

    async def _publish(self, event: StreamEvent) -> None:
        await self.redis.publish(self.channel, event.model_dump_json())

    async def emit_thinking(self, token: str) -> None:
        event = StreamEvent.thinking(self.chat_id, token)
        await self._publish(event)

    async def emit_token(self, token: str) -> None:
        event = StreamEvent.token(self.chat_id, token)
        await self._publish(event)

    async def emit_done(self, status: str) -> None:
        event = StreamEvent.done(self.chat_id, status)
        await self._publish(event)

    async def emit_error(self, message: str) -> None:
        event = StreamEvent.error(self.chat_id, message)
        await self._publish(event)
