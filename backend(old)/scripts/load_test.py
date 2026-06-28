#!/usr/bin/env python3
"""
Load test: spawn N Async Socket.IO clients that send a single message simultaneously.
Collect first-progress (first token) and done timings per client and print summary.

Usage:
  python scripts/load_test.py --url http://localhost:8000 --concurrency 20 --chat-id loadtest-chat --token testtoken

Log-to-file usage (added):
  # Windows PowerShell (satır devamı için backtick ` kullanın):
  python scripts/load_test.py `
    --url http://localhost:8000 `
    --concurrency 50 `
    --spawn-delay-ms 70 `
    --token testtoken `
    --separate-chats `
    --log-file load_test_50_70ms.txt

  # 2 ms ve 70 ms aralıklı 50 kullanıcı testi örneği (PowerShell, tek log dosyası):
  $delays = 2,70
  foreach ($d in $delays) {
    Write-Host "=== delay=$d ms ==="
    python scripts/load_test.py `
      --url http://localhost:8000 `
      --concurrency 50 `
      --spawn-delay-ms $d `
      --token testtoken `
      --separate-chats `
      --log-file load_test_50_both_delays.txt
    ""
  }

  Bu şekilde terminal çıktısı hem ekrana basılır hem verilen dosyaya eklenir.

"""
import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime

import socketio

log_lines: list[str] = []

def log(msg: str) -> None:
    print(msg)
    log_lines.append(str(msg))


async def run_client(url, user_id, token, chat_id, message_text, timeout=30):
    sio = socketio.AsyncClient()
    first_progress = None
    done = None
    send_time = None

    async def _on_progress(data):
        nonlocal first_progress
        if first_progress is None:
            first_progress = time.perf_counter()

    async def _on_done(data):
        nonlocal done
        done = time.perf_counter()

    async def _on_response(data):
        # Current API emits `chat:message:received` as the final response event.
        nonlocal first_progress, done
        if first_progress is None:
            first_progress = time.perf_counter()
        if done is None:
            done = time.perf_counter()

    async def _on_agent_response(data):
        nonlocal first_progress, done
        is_chunk = False
        is_done = False
        try:
            if isinstance(data, dict):
                is_chunk = bool(data.get("is_chunk", False))
                is_done = bool(data.get("is_done", False))
        except Exception:
            pass

        if is_chunk and first_progress is None:
            first_progress = time.perf_counter()
        if is_done and done is None:
            done = time.perf_counter()

    async def _on_connect():
        pass

    sio.on("chat:agent:progress")(_on_progress)
    sio.on("chat:agent:done")(_on_done)
    sio.on("chat:agent:response")(_on_agent_response)
    sio.on("chat:message:received")(_on_response)
    sio.on("chat:error")(lambda data: None)

    connect_url = f"{url}?user_id={user_id}&token={token}"
    try:
        await sio.connect(connect_url, transports=["websocket"]) 
    except Exception as exc:
        return {"user": user_id, "error": f"connect_failed: {exc}"}

    payload = {"message": {"text": message_text}, "chatId": chat_id}

    send_time = time.perf_counter()
    try:
        await sio.emit("chat:message", payload)
    except Exception as exc:
        await sio.disconnect()
        return {"user": user_id, "error": f"emit_failed: {exc}"}

    # wait until first_progress and done or timeout
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if done is not None:
            break
        await asyncio.sleep(0.05)

    await sio.disconnect()

    result = {"user": user_id}
    if first_progress is not None:
        result["first_progress_ms"] = (first_progress - send_time) * 1000.0
    else:
        result["first_progress_ms"] = None
    if done is not None:
        result["done_ms"] = (done - send_time) * 1000.0
    else:
        result["done_ms"] = None
    return result


async def run_load_test(url, concurrency, chat_id, token, same_chat=True, spawn_delay_ms=0, timeout=30):
    tasks = []
    for i in range(concurrency):
        user_id = f"loadtest_user_{i+1}"
        msg = f"Load test message from {user_id} at {time.time()}"
        target_chat = chat_id if same_chat else f"{chat_id}-{i+1}"
        tasks.append(asyncio.create_task(run_client(url, user_id, token, target_chat, msg, timeout=timeout)))
        if spawn_delay_ms > 0:
            await asyncio.sleep(spawn_delay_ms / 1000.0)

    return await asyncio.gather(*tasks)


def summarize(results):
    firsts = [r["first_progress_ms"] for r in results if r.get("first_progress_ms") is not None]
    dones = [r["done_ms"] for r in results if r.get("done_ms") is not None]
    errors = [r for r in results if r.get("error")]

    def stats(arr):
        if not arr:
            return None
        return {
            "count": len(arr),
            "min": min(arr),
            "max": max(arr),
            "mean": statistics.mean(arr),
            "median": statistics.median(arr),
            "p95": statistics.quantiles(arr, n=100)[94] if len(arr) >= 20 else None,
        }

    total = len(results)
    ok = total - len(errors)
    success_rate = (ok / total * 100.0) if total else 0.0

    return {
        "total": total,
        "ok": ok,
        "errors": len(errors),
        "success_rate_percent": round(success_rate, 2),
        "first_progress": stats(firsts),
        "done": stats(dones),
        "raw": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--concurrency", type=int, default=20, help="Number of concurrent users")
    parser.add_argument("--chat-id", default="loadtest-chat", help="Base chat id (will be suffixed when separate chats are used)")
    parser.add_argument("--token", default="testtoken", help="Authentication token")
    parser.add_argument("--same-chat", action="store_true", default=False, help="Send all messages to same chat id")
    parser.add_argument("--separate-chats", action="store_true", default=False, help="Send each user to a separate chat id")
    parser.add_argument("--spawn-delay-ms", type=int, default=0, help="Delay between client spawns in milliseconds")
    parser.add_argument("--timeout-seconds", type=int, default=30, help="Per-client wait timeout for done event")
    parser.add_argument("--log-file", default=None, help="Optional file path to store terminal output after test")
    args = parser.parse_args()

    if args.same_chat:
        same_chat = True
    elif args.separate_chats:
        same_chat = False
    else:
        # varsayılan: farklı odalar
        same_chat = False

    log(
        f"Starting load test: {args.concurrency} clients -> {args.url} "
        f"(chat={args.chat_id}, same_chat={same_chat}, spawn_delay_ms={args.spawn_delay_ms})"
    )
    results = asyncio.run(
        run_load_test(
            args.url,
            args.concurrency,
            args.chat_id,
            args.token,
            same_chat=same_chat,
            spawn_delay_ms=args.spawn_delay_ms,
            timeout=args.timeout_seconds,
        )
    )
    summary = summarize(results)

    log("\nSummary:")
    summary_json = json.dumps(summary, indent=2)
    log(summary_json)

    if args.log_file:
        with open(args.log_file, "a", encoding="utf-8") as f:
            f.write("=== Load Test Log: " + datetime.now().isoformat() + " ===\n")
            for line in log_lines:
                f.write(line + "\n")
            f.write("\n")

    return summary


if __name__ == "__main__":
    main()
