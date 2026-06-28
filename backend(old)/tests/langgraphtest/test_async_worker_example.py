import asyncio
import json

import pytest

from langgraphtest.async_worker_example import (
    run_local_simulation,
    worker_loop,
)


class FakeRedis:
    def __init__(self) -> None:
        self.inbound = asyncio.Queue()
        self.outbound: list[tuple[str, str]] = []

    async def brpop(self, queue: str, timeout: int | None = None):
        return await self.inbound.get()

    async def lpush(self, queue: str, value: str) -> None:
        self.outbound.append((queue, value))


@pytest.mark.asyncio
async def test_worker_loop_creates_concurrent_tasks_and_writes_outbound(monkeypatch):
    monkeypatch.setenv("INBOUND_QUEUE", "inbound_messages")
    monkeypatch.setenv("OUTBOUND_QUEUE", "outbound_messages")

    fake_redis = FakeRedis()
    payloads = [
        json.dumps({"user_id": "user1", "text": "hello"}),
        json.dumps({"user_id": "user2", "text": "merhaba"}),
    ]

    await fake_redis.inbound.put(("inbound_messages", payloads[0]))
    await fake_redis.inbound.put(("inbound_messages", payloads[1]))

    processed = await worker_loop(r=fake_redis, stop_after=2, max_concurrent_tasks=1)

    assert processed == 2
    assert len(fake_redis.outbound) == 2
    assert {queue for queue, _ in fake_redis.outbound} == {"outbound_messages"}
    assert any("Merhaba user1!" in json.loads(value)["text"] for _, value in fake_redis.outbound)
    assert any("Merhaba user2!" in json.loads(value)["text"] for _, value in fake_redis.outbound)


@pytest.mark.asyncio
async def test_run_local_simulation_returns_metrics():
    results = await run_local_simulation(total_clients=2, max_concurrent_tasks=1)

    assert len(results) == 2
    assert all(result["first_token_ms"] > 0 for result in results)
    assert all(result["total_ms"] > 0 for result in results)
    assert all("Merhaba user" in result["final_text"] for result in results)
