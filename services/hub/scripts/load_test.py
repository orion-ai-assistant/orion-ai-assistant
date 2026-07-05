import asyncio
import contextlib
import json
from services.shared.environment import get_env
import statistics
import time

import aiohttp

BASE_URL = get_env("ORION_BASE_URL", "http://localhost:8909")
USER_COUNT = get_env("ORION_USER_COUNT", "8", cast=int)
MESSAGE_COUNT = get_env("ORION_MESSAGE_COUNT", "1", cast=int)
JOB_COMPLETE_TIMEOUT_SECONDS = get_env("ORION_JOB_COMPLETE_TIMEOUT_SECONDS", "60", cast=int)
POST_TIMEOUT_SECONDS = get_env("ORION_POST_TIMEOUT_SECONDS", "20", cast=int)

latencies_ms: list[float] = []
post_latencies_ms: list[float] = []
accepted_latencies_ms: list[float] = []
first_token_latencies_ms: list[float] = []


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=20)[18]


async def sse_listener(
    session: aiohttp.ClientSession,
    user_id: str,
    chat_id_ref: dict[str, str | None],
    done_future_ref: dict[str, asyncio.Future[None] | None],
    started_at_ref: dict[str, float | None],
    accepted_at_ref: dict[str, float | None],
    accepted_seen_ref: dict[str, bool],
    first_token_seen_ref: dict[str, bool],
    connected_event: asyncio.Event,
):
    url = f"{BASE_URL}/api/v1/chat/stream?user_id={user_id}"
    async with session.get(url, timeout=None) as response:
        response.raise_for_status()
        connected_event.set()
        buffer: list[str] = []

        async for raw in response.content:
            line = raw.decode("utf-8", errors="ignore").strip()

            if not line:
                if not buffer:
                    continue

                payload = "\n".join(buffer)
                buffer.clear()
                try:
                    message = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = message.get("type")
                event_chat_id = message.get("chat_id")

                if not event_chat_id:
                    continue

                expected_chat_id = chat_id_ref["value"]
                if expected_chat_id is not None and event_chat_id != expected_chat_id:
                    continue

                # The first accepted event can carry the new chat id before POST body is processed.
                if expected_chat_id is None and event_type == "accepted":
                    chat_id_ref["value"] = event_chat_id

                started_at = started_at_ref["value"]
                accepted_at = accepted_at_ref["value"]
                done_future = done_future_ref["value"]

                if event_type == "accepted" and started_at is not None and not accepted_seen_ref["value"]:
                    accepted_seen_ref["value"] = True
                    accepted_at_ref["value"] = time.perf_counter()
                    latency = (accepted_at_ref["value"] - started_at) * 1000
                    accepted_latencies_ms.append(latency)

                if event_type == "token" and not first_token_seen_ref["value"]:
                    first_token_seen_ref["value"] = True
                    if accepted_at is not None:
                        latency = (time.perf_counter() - accepted_at) * 1000
                    elif started_at is not None:
                        latency = (time.perf_counter() - started_at) * 1000
                    else:
                        latency = 0.0
                    first_token_latencies_ms.append(latency)

                if event_type == "done" and done_future is not None and not done_future.done() and started_at is not None:
                    done_future.set_result(None)
                    latency = (time.perf_counter() - started_at) * 1000
                    latencies_ms.append(latency)
                elif event_type == "error" and done_future is not None and not done_future.done():
                    done_future.set_exception(RuntimeError("Worker returned error event"))
                continue

            if line.startswith("data:"):
                buffer.append(line[5:].strip())


async def start_user(user_index: int):
    user_id = f"user_{user_index}"
    timeout = aiohttp.ClientTimeout(total=POST_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        chat_id_ref: dict[str, str | None] = {"value": None}
        done_future_ref: dict[str, asyncio.Future[None] | None] = {"value": None}
        started_at_ref: dict[str, float | None] = {"value": None}
        accepted_at_ref: dict[str, float | None] = {"value": None}
        accepted_seen_ref: dict[str, bool] = {"value": False}
        first_token_seen_ref: dict[str, bool] = {"value": False}
        connected_event = asyncio.Event()

        listener = asyncio.create_task(
            sse_listener(
                session,
                user_id,
                chat_id_ref,
                done_future_ref,
                started_at_ref,
                accepted_at_ref,
                accepted_seen_ref,
                first_token_seen_ref,
                connected_event,
            )
        )

        try:
            await asyncio.wait_for(connected_event.wait(), timeout=5)

            for message_index in range(MESSAGE_COUNT):
                payload = {
                    "user_id": user_id,
                    "input": {
                        "text": f"load test message {message_index} from {user_id}",
                        "metadata": {"case": "load_test"},
                    },
                }
                if chat_id_ref["value"]:
                    payload["chat_id"] = chat_id_ref["value"]

                started_at_ref["value"] = time.perf_counter()
                accepted_at_ref["value"] = None
                accepted_seen_ref["value"] = False
                first_token_seen_ref["value"] = False
                done_future_ref["value"] = asyncio.get_running_loop().create_future()

                t0 = time.perf_counter()
                async with session.post(f"{BASE_URL}/api/v1/chats/messages", json=payload) as response:
                    response.raise_for_status()
                    body = await response.json()
                post_latencies_ms.append((time.perf_counter() - t0) * 1000)

                if chat_id_ref["value"] is None:
                    chat_id_ref["value"] = body["chat_id"]

                try:
                    await asyncio.wait_for(
                        done_future_ref["value"],
                        timeout=JOB_COMPLETE_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if done_future_ref["value"] and not done_future_ref["value"].done():
                        done_future_ref["value"].set_exception(
                            TimeoutError(
                                f"Chat {chat_id_ref['value']} did not complete within {JOB_COMPLETE_TIMEOUT_SECONDS}s"
                            )
                        )
        finally:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener


async def main():
    print(f"Starting HTTP+SSE load test with {USER_COUNT} users x {MESSAGE_COUNT} messages...")
    tasks = [asyncio.create_task(start_user(i)) for i in range(USER_COUNT)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        print(f"\n{len(errors)} user tasks failed:")
        for error in errors[:10]:
            print(type(error).__name__, error)
        if len(errors) > 10:
            print("...")

    if post_latencies_ms:
        print("\nPOST latency:")
        print(f"  Average: {statistics.mean(post_latencies_ms):.2f} ms")
        print(f"  Min    : {min(post_latencies_ms):.2f} ms")
        print(f"  Max    : {max(post_latencies_ms):.2f} ms")
        print(f"  P95    : {p95(post_latencies_ms):.2f} ms")

    if accepted_latencies_ms:
        print("\nAccepted event latency:")
        print(f"  Samples: {len(accepted_latencies_ms)}")
        print(f"  Average: {statistics.mean(accepted_latencies_ms):.2f} ms")
        print(f"  Min    : {min(accepted_latencies_ms):.2f} ms")
        print(f"  Max    : {max(accepted_latencies_ms):.2f} ms")
        print(f"  P95    : {p95(accepted_latencies_ms):.2f} ms")

    if first_token_latencies_ms:
        print("\nFirst token latency:")
        print(f"  Samples: {len(first_token_latencies_ms)}")
        print(f"  Average: {statistics.mean(first_token_latencies_ms):.2f} ms")
        print(f"  Min    : {min(first_token_latencies_ms):.2f} ms")
        print(f"  Max    : {max(first_token_latencies_ms):.2f} ms")
        print(f"  P95    : {p95(first_token_latencies_ms):.2f} ms")

    if latencies_ms:
        print("\nChat completion latency (SSE done event):")
        print(f"  Samples: {len(latencies_ms)}")
        print(f"  Average: {statistics.mean(latencies_ms):.2f} ms")
        print(f"  Min    : {min(latencies_ms):.2f} ms")
        print(f"  Max    : {max(latencies_ms):.2f} ms")
        print(f"  P95    : {p95(latencies_ms):.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())
