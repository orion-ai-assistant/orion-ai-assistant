import asyncio
import logging
import socket

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from orion.contracts.constants import CONSUMER_GROUP, SETTINGS_DEFAULT_USER, STREAM_NAME
from orion.kernel.environment import get_redis_url
from orion.kernel.config import get_runtime_settings
from orion.worker.services.job_processor import process_message
from orion.worker.services.router import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def ensure_consumer_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(name=STREAM_NAME, groupname=CONSUMER_GROUP, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def run() -> None:
    redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    settings = await get_runtime_settings(redis, SETTINGS_DEFAULT_USER)
    consumer_name = socket.gethostname()
    semaphore = asyncio.Semaphore(settings.worker_max_concurrency)
    in_flight: set[asyncio.Task] = set()

    await ensure_consumer_group(redis)

    async def run_with_limit(stream_id: str, fields: dict[str, str]) -> None:
        async with semaphore:
            try:
                await process_message(redis, stream_id, fields, consumer_name)
            finally:
                await redis.xack(STREAM_NAME, CONSUMER_GROUP, stream_id)

    try:
        while True:
            messages = await redis.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=consumer_name,
                streams={STREAM_NAME: ">"},
                count=settings.worker_max_concurrency,
                block=2000,
            )

            if not messages:
                continue

            for _, entries in messages:
                for stream_id, fields in entries:
                    task = asyncio.create_task(run_with_limit(stream_id, fields))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)

            if len(in_flight) >= settings.worker_max_concurrency:
                done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    if exception := finished.exception():
                        logging.exception("Worker task failed: %s", exception)
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        await close_session()
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run())
