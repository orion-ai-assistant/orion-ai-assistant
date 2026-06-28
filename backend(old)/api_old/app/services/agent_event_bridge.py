from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import redis.asyncio as aioredis

from api.app.core.stream_constants import SocketEventName
from api.app.services.agent_queue_service import get_events_pattern
from api.app.services.message_factory import MessageFactory
from api.app.services.messenger_service import Messenger
from api.app.services.session_service import save_session
from api.app.shared_state import manager
from api.app.store.redis_store import add_message
from config.settings.api_server import REDIS_URL
from log import Logger

logger = Logger(__file__)


class AgentEventBridge:
    def __init__(self) -> None:
        self._redis = aioredis.from_url(REDIS_URL or "redis://localhost:6379", decode_responses=True)
        self._task: asyncio.Task | None = None
        self._worker_tasks: list[asyncio.Task] = []
        self._sequence_state: dict[str, int] = {}
        self._worker_count = max(1, int(os.getenv("AGENT_EVENT_BRIDGE_WORKERS", "8")))
        self._queue_size = max(100, int(os.getenv("AGENT_EVENT_BRIDGE_QUEUE_SIZE", "2000")))
        self._queues: list[asyncio.Queue[dict[str, Any]]] = [
            asyncio.Queue(maxsize=self._queue_size) for _ in range(self._worker_count)
        ]
        self._dropped_events = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if not self._worker_tasks:
            self._worker_tasks = [
                asyncio.create_task(self._worker_loop(i), name=f"agent_event_bridge_worker_{i}")
                for i in range(self._worker_count)
            ]
        self._task = asyncio.create_task(self._run())
        logger.info(
            lambda: (
                f"agent_event_bridge started workers={self._worker_count} "
                f"queue_size={self._queue_size}"
            )
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

        for worker in self._worker_tasks:
            worker.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

    def _should_process(self, session_id: str) -> bool:
        return bool(session_id and manager.get_user_id(session_id))

    def _is_duplicate(self, request_id: str, seq: int | None) -> bool:
        if not request_id or seq is None:
            return False
        last_seq = self._sequence_state.get(request_id)
        if last_seq is not None and seq <= last_seq:
            return True
        self._sequence_state[request_id] = seq
        return False

    async def _emit(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        request_id = str(event.get("request_id") or "")
        seq_raw = event.get("sequence")
        seq = seq_raw if isinstance(seq_raw, int) else None
        session_id = str(event.get("session_id") or "")
        user_id = str(event.get("user_id") or "")
        chat_id = str(event.get("chat_id") or "")

        if not (session_id and user_id and chat_id):
            return
        if not self._should_process(session_id):
            return
        if self._is_duplicate(request_id, seq):
            return

        if event_type == "token":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_PROGRESS,
                message=MessageFactory.agent_text(str(event.get("content") or ""), is_chunk=True),
                session_id=session_id,
                user_id=user_id,
            )
            return

        if event_type == "status":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_PROGRESS,
                message=MessageFactory.agent_status(str(event.get("content") or "")),
                session_id=session_id,
                user_id=user_id,
            )
            return

        if event_type == "tool_call":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_PROGRESS,
                message=MessageFactory.tool_call(str(event.get("name") or "tool"), event.get("args") or {}),
                session_id=session_id,
                user_id=user_id,
            )
            return

        if event_type == "tool_result":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_PROGRESS,
                message=MessageFactory.tool_result(str(event.get("name") or "tool"), str(event.get("output") or "")),
                session_id=session_id,
                user_id=user_id,
            )
            return

        if event_type == "canceled":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_CANCELED,
                message=MessageFactory.system_message("Agent akisi iptal edildi"),
                session_id=session_id,
                user_id=user_id,
            )
            if request_id:
                self._sequence_state.pop(request_id, None)
            return

        if event_type == "error":
            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_ERROR,
                message=MessageFactory.agent_error(str(event.get("error") or "Agent hata uretti")),
                session_id=session_id,
                user_id=user_id,
            )
            if request_id:
                self._sequence_state.pop(request_id, None)
            return

        if event_type == "done":
            final_text = str(event.get("final_text") or "")
            final_messages = event.get("final_messages")
            if isinstance(final_messages, list):
                await save_session(chat_id, final_messages)

            if final_text.strip():
                await add_message(
                    chat_id=chat_id,
                    sender="assistant",
                    content=final_text,
                    metadata={"session_id": session_id, "user_id": user_id},
                )

            await Messenger.broadcast_message(
                chat_id=chat_id,
                socket_event=SocketEventName.CHAT_AGENT_DONE,
                message=MessageFactory.final_done(),
                session_id=session_id,
                user_id=user_id,
            )
            if request_id:
                self._sequence_state.pop(request_id, None)

    def _pick_queue(self, event: dict[str, Any]) -> asyncio.Queue[dict[str, Any]]:
        request_id = str(event.get("request_id") or "")
        session_id = str(event.get("session_id") or "")
        chat_id = str(event.get("chat_id") or "")
        key = request_id or f"{session_id}:{chat_id}"
        index = hash(key) % self._worker_count
        return self._queues[index]

    def _enqueue_event(self, event: dict[str, Any]) -> None:
        queue = self._pick_queue(event)
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        # Keep backpressure local by dropping the oldest event in the same shard queue.
        try:
            _ = queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            pass

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_events += 1
            if self._dropped_events % 100 == 1:
                logger.warning(
                    lambda: (
                        "agent_event_bridge dropping events due to saturated queues; "
                        f"dropped_count={self._dropped_events}"
                    )
                )

    async def _worker_loop(self, worker_idx: int) -> None:
        queue = self._queues[worker_idx]
        while True:
            event = await queue.get()
            try:
                await self._emit(event)
            except Exception as exc:
                logger.error(lambda: f"agent_event_bridge worker emit failed: {exc}")
            finally:
                queue.task_done()

    async def _run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(get_events_pattern())

        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if not message:
                    continue

                raw = message.get("data")
                if not isinstance(raw, str):
                    continue

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(lambda: f"agent_event_bridge invalid payload: {raw}")
                    continue

                self._enqueue_event(event)
        finally:
            await pubsub.punsubscribe(get_events_pattern())
            await pubsub.close()


_bridge = AgentEventBridge()


async def start_agent_event_bridge() -> None:
    await _bridge.start()


async def stop_agent_event_bridge() -> None:
    await _bridge.stop()
