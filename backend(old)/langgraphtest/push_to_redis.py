import argparse
import asyncio
import json
import os
import sys
from typing import Sequence

import redis.asyncio as redis


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def get_inbound_queue() -> str:
    return os.getenv("INBOUND_QUEUE", "inbound_messages")


async def push_message(user_id: str, text: str) -> None:
    r = redis.from_url(get_redis_url(), decode_responses=True)
    payload = json.dumps({"user_id": user_id, "text": text}, ensure_ascii=False)
    await r.lpush(get_inbound_queue(), payload)
    print(f"✅ Mesaj Redis inbound_messages kuyruğuna gönderildi: user_id={user_id}")


async def push_bulk_messages(
    user_prefix: str,
    text_template: str,
    count: int,
    parallel: int,
) -> None:
    semaphore = asyncio.Semaphore(parallel)

    async def push_one(i: int) -> None:
        async with semaphore:
            user_id = f"{user_prefix}{i}"
            text = text_template.replace("{i}", str(i))
            await push_message(user_id, text)

    tasks = [asyncio.create_task(push_one(i + 1)) for i in range(count)]
    await asyncio.gather(*tasks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redis inbound_messages kuyruğuna test mesajı gönderir")
    parser.add_argument("user_id", type=str, help="Tek kullanıcı için user_id veya toplu test için kullanıcı adı prefixi")
    parser.add_argument(
        "text",
        type=str,
        nargs="+",
        help="Mesaj metni. Toplu testte {i} kullandığınızda her kullanıcı için numara yerleştirilir",
    )
    parser.add_argument("--count", type=int, default=1, help="Kaç mesaj gönderilsin")
    parser.add_argument(
        "--parallel",
        type=int,
        default=10,
        help="Aynı anda kaç push işlemi paralel çalışsın",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    text = " ".join(args.text)

    if args.count == 1:
        asyncio.run(push_message(args.user_id, text))
        sys.exit(0)

    asyncio.run(push_bulk_messages(args.user_id, text, args.count, args.parallel))
