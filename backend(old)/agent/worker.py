from __future__ import annotations

import asyncio
import json
import signal
import time
from typing import Any

import redis.asyncio as aioredis
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from common.agent_queue_contract import AGENT_QUEUE_KEY, cancel_version_key, events_channel
from common.env_helper import get_env
from log import Logger

from .facade import stream_request
from .stream_parser import StreamParser, serialize_message

logger = Logger(__file__)

DEFAULT_MAX_CONCURRENT = 500
MAX_CONCURRENT_TASKS = int(get_env("MAX_CONCURRENT_TASKS", required=False) or str(DEFAULT_MAX_CONCURRENT))
HEARTBEAT_SECONDS = float(get_env("AGENT_HEARTBEAT_SECONDS", required=False) or "8")
HEARTBEAT_TEXT = (get_env("AGENT_HEARTBEAT_TEXT", required=False) or "Arastiriliyor...").strip()
CANCEL_CHECK_INTERVAL_SECONDS = float(get_env("AGENT_CANCEL_CHECK_INTERVAL_SECONDS", required=False) or "0.05")
BLPOP_TIMEOUT_SECONDS = int(get_env("AGENT_QUEUE_BLPOP_TIMEOUT_SECONDS", required=False) or "1")
REDIS_URL = get_env("REDIS_URL", required=False)


def _get_redis() -> aioredis.Redis:
    url = REDIS_URL or "redis://localhost:6379"
    return aioredis.from_url(url, decode_responses=True)


def _to_langchain_message(msg: dict[str, Any]):
    role = str(msg.get("role") or "user").lower()
    content = str(msg.get("content") or "")
    if role == "system":
        return SystemMessage(content=content)
    if role in {"assistant", "ai", "model"}:
        return AIMessage(content=content)
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=str(msg.get("tool_call_id") or "tool_call"))
    return HumanMessage(content=content)


async def _publish_event(r: aioredis.Redis, chat_id: str, event: dict[str, Any]) -> None:
    await r.publish(events_channel(chat_id), json.dumps(event, ensure_ascii=False))


async def _is_canceled(r: aioredis.Redis, chat_id: str, task_cancel_version: int) -> bool:
    current = await r.get(cancel_version_key(chat_id))
    if current is None:
        return False
    try:
        return int(current) > task_cancel_version
    except ValueError:
        return False


async def _process_task(r: aioredis.Redis, task: dict[str, Any]) -> None:
    session_id = task.get("session_id")
    user_id = task.get("user_id")
    chat_id = task.get("chat_id")
    messages = task.get("messages")
    task_id = str(task.get("task_id") or "")
    request_id = str(task.get("request_id") or "")
    cancel_version = int(task.get("cancel_version") or 0)

    if not (session_id and user_id and chat_id and isinstance(messages, list) and request_id):
        logger.warning(lambda: f"agent.worker invalid task payload skipped: {task}")
        return

    if await _is_canceled(r, str(chat_id), cancel_version):
        await _publish_event(
            r,
            str(chat_id),
            {
                "type": "canceled",
                "request_id": request_id,
                "task_id": task_id,
                "session_id": session_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "sequence": 1,
            },
        )
        return

    parser = StreamParser()
    sequence = 0
    last_values_state: dict[str, Any] = {}
    last_cancel_check_at = 0.0

    def next_sequence() -> int:
        nonlocal sequence
        sequence += 1
        return sequence

    lc_messages = [_to_langchain_message(message) for message in messages]

    try:
        iterator = stream_request(
            messages=lc_messages,
            request_id=request_id,
            thread_id=f"{user_id}:{chat_id}",
        ).__aiter__()
        while True:
            now = time.monotonic()
            if now - last_cancel_check_at >= CANCEL_CHECK_INTERVAL_SECONDS:
                last_cancel_check_at = now
                if await _is_canceled(r, str(chat_id), cancel_version):
                    await _publish_event(
                        r,
                        str(chat_id),
                        {
                            "type": "canceled",
                            "request_id": request_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "user_id": user_id,
                            "chat_id": chat_id,
                            "sequence": next_sequence(),
                        },
                    )
                    return

            try:
                stream_type, data = await asyncio.wait_for(iterator.__anext__(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await _publish_event(
                    r,
                    str(chat_id),
                    {
                        "type": "status",
                        "content": HEARTBEAT_TEXT,
                        "request_id": request_id,
                        "task_id": task_id,
                        "session_id": session_id,
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "sequence": next_sequence(),
                    },
                )
                continue
            except StopAsyncIteration:
                break

            if stream_type == "values":
                if isinstance(data, dict):
                    last_values_state = data

                for event in parser.extract_tool_events_from_values(data):
                    if event["event"] == "tool_call":
                        await _publish_event(
                            r,
                            str(chat_id),
                            {
                                "type": "tool_call",
                                "name": str(event.get("name", "tool")),
                                "args": event.get("args", {}),
                                "request_id": request_id,
                                "task_id": task_id,
                                "session_id": session_id,
                                "user_id": user_id,
                                "chat_id": chat_id,
                                "sequence": next_sequence(),
                            },
                        )
                    elif event["event"] == "tool_result":
                        await _publish_event(
                            r,
                            str(chat_id),
                            {
                                "type": "tool_result",
                                "name": str(event.get("name", "tool")),
                                "output": str(event.get("output", "")),
                                "request_id": request_id,
                                "task_id": task_id,
                                "session_id": session_id,
                                "user_id": user_id,
                                "chat_id": chat_id,
                                "sequence": next_sequence(),
                            },
                        )
                continue

            for event in parser.parse_payload(data):
                if event["event"] == "text":
                    await _publish_event(
                        r,
                        str(chat_id),
                        {
                            "type": "token",
                            "content": str(event.get("content", "")),
                            "request_id": request_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "user_id": user_id,
                            "chat_id": chat_id,
                            "sequence": next_sequence(),
                        },
                    )
                elif event["event"] == "think":
                    await _publish_event(
                        r,
                        str(chat_id),
                        {
                            "type": "status",
                            "content": str(event.get("content", "")),
                            "request_id": request_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "user_id": user_id,
                            "chat_id": chat_id,
                            "sequence": next_sequence(),
                        },
                    )

        final_text, _final_thought = parser.get_final_texts()
        final_messages_source = None
        if isinstance(last_values_state, dict):
            maybe_messages = last_values_state.get("messages")
            if isinstance(maybe_messages, list):
                final_messages_source = maybe_messages

        if final_messages_source is None:
            final_messages = [dict(message) for message in messages]
        else:
            final_messages = [serialize_message(message) for message in final_messages_source]

        if final_text and (not final_messages or final_messages[-1].get("content") != final_text):
            final_messages.append({"role": "assistant", "content": final_text})

        await _publish_event(
            r,
            str(chat_id),
            {
                "type": "done",
                "final_text": final_text,
                "final_messages": final_messages,
                "request_id": request_id,
                "task_id": task_id,
                "session_id": session_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "sequence": next_sequence(),
            },
        )
    except Exception as exc:
        logger.error(lambda: f"agent.worker task failed request_id={request_id} chat_id={chat_id}: {exc}")
        await _publish_event(
            r,
            str(chat_id),
            {
                "type": "error",
                "error": str(exc),
                "request_id": request_id,
                "task_id": task_id,
                "session_id": session_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "sequence": next_sequence(),
            },
        )


async def worker_loop() -> None:
    r = _get_redis()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS) if MAX_CONCURRENT_TASKS > 0 else None
    pending: set[asyncio.Task] = set()

    if semaphore is None:
        logger.info("agent.worker unlimited concurrency mode enabled")
    else:
        logger.info(lambda: f"agent.worker concurrency limit set to {MAX_CONCURRENT_TASKS}")

    async def run_task(task_data: dict[str, Any]) -> None:
        if semaphore is None:
            await _process_task(r, task_data)
            return
        async with semaphore:
            await _process_task(r, task_data)

    while True:
        done = {task for task in pending if task.done()}
        pending -= done

        item = await r.blpop(AGENT_QUEUE_KEY, timeout=BLPOP_TIMEOUT_SECONDS)
        if item is None:
            continue

        _, raw_payload = item
        try:
            task_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning(lambda: f"agent.worker invalid queue payload: {raw_payload}")
            continue

        task = asyncio.create_task(run_task(task_payload))
        pending.add(task)
        task.add_done_callback(lambda done_task: pending.discard(done_task))


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, loop.stop)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(worker_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("agent.worker exiting")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
