import asyncio
import logging
import json
import socket

from redis.asyncio import Redis
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError as RedisConnectionError,
    ResponseError,
    TimeoutError as RedisTimeoutError,
)

from orion.contracts.constants import CONSUMER_GROUP, SETTINGS_DEFAULT_USER, STREAM_NAME
from services.shared.environment import get_redis_url
from orion.kernel.config import get_runtime_settings
from orion.kernel.job_safety import (
    SingleWorkerLock, acquire_owner, COMPARE_DELETE, OWNER_KEY,
    job_key, mark_started, fail_job, INTERRUPTED,
)
from orion.worker.services.job_processor import process_message
from orion.worker.services.router import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

READ_BLOCK_MS = 2000
REDIS_SOCKET_TIMEOUT = 5.0  # Leave headroom above the blocking stream read.
READ_RETRY_INITIAL = 0.5
READ_RETRY_MAX = 5.0


async def ensure_consumer_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(name=STREAM_NAME, groupname=CONSUMER_GROUP, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


ACK_TERMINAL = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return redis.error_reply('Worker ownership lost') end
if redis.call('HGET', KEYS[2], 'phase') ~= 'terminal' then return 0 end
return redis.call('XACK', KEYS[3], ARGV[2], ARGV[3])
"""


async def execute_job(redis, stream_id, fields, consumer_name, owner):
    fields = dict(fields)
    fields['turn_id'] = fields.get('turn_id') or fields['created_at']
    journal = await redis.hgetall(job_key(fields['turn_id']))
    if journal.get('phase') != 'terminal':
        if journal.get('phase') == 'queued' and await mark_started(redis, fields, owner):
            # The marker is confirmed in Redis BEFORE any Router/tool work.
            await process_message(redis, stream_id, fields, consumer_name)
        journal = await redis.hgetall(job_key(fields['turn_id']))
        if journal.get('phase') != 'terminal':
            # Includes legacy jobs, interrupted jobs and superseded turns.
            await fail_job(redis, fields, INTERRUPTED)
    await redis.eval(ACK_TERMINAL, 3, OWNER_KEY, job_key(fields['turn_id']), STREAM_NAME,
                     owner, CONSUMER_GROUP, stream_id)


async def execute_serialized(redis, stream_id, fields, consumer_name, owner, active_turns):
    # Serialize duplicate stream entries locally; the durable marker remains
    # the authority after any process crash or ambiguous Redis write response.
    turn = fields.get('turn_id') or fields['created_at']
    while turn in active_turns:
        await active_turns[turn].wait()
    finished = asyncio.Event()
    active_turns[turn] = finished
    try:
        await execute_job(redis, stream_id, fields, consumer_name, owner)
    finally:
        active_turns.pop(turn)
        finished.set()


async def recover_pending(redis, consumer_name, owner):
    """Only run after exclusive ownership and confirmed local predecessor death."""
    start = '-'
    hostname = socket.gethostname()
    while True:
        if await redis.get(OWNER_KEY) != owner:
            raise RuntimeError('Worker ownership lost before pending recovery')
        entries = await redis.xpending_range(STREAM_NAME, CONSUMER_GROUP, start, '+', 100)
        if not entries:
            return
        for entry in entries:
            previous = entry['consumer']
            # Never infer death for an unknown/remote legacy consumer.
            if previous != hostname and not previous.startswith(hostname + ':'):
                logging.warning('Leaving unverified consumer %s pending', previous)
                continue
            claimed = await redis.xclaim(STREAM_NAME, CONSUMER_GROUP, consumer_name, 0, [entry['message_id']])
            for stream_id, fields in claimed:
                await execute_job(redis, stream_id, fields, consumer_name, owner)
        milliseconds, sequence = entries[-1]['message_id'].split('-')
        # Redis 5 has no exclusive XPENDING range syntax.
        start = f'{milliseconds}-{int(sequence) + 1}'


async def run() -> None:
    with SingleWorkerLock():
        await run_owned()


async def run_owned() -> None:
    from orion.worker.tools._registry import get_registry
    get_registry()
    redis = Redis.from_url(
        get_redis_url(), decode_responses=True, protocol=2,
        socket_timeout=REDIS_SOCKET_TIMEOUT,
    )
    in_flight: set[asyncio.Task] = set()
    active_turns = {}
    owner = None

    async def run_with_limit(stream_id: str, fields: dict[str, str]) -> None:
        async with semaphore:
            await execute_serialized(redis, stream_id, fields, consumer_name, owner, active_turns)

    task_failed = asyncio.Event()

    def on_finished(task):
        in_flight.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            logging.error('Worker job failed; preserving pending entry', exc_info=(type(error), error, error.__traceback__))
            task_failed.set()

    try:
        owner = await acquire_owner(redis)
        consumer_name = socket.gethostname() + ':' + json.loads(owner)['token']
        settings = await get_runtime_settings(redis, SETTINGS_DEFAULT_USER)
        semaphore = asyncio.Semaphore(settings.worker_max_concurrency)
        await ensure_consumer_group(redis)
        await recover_pending(redis, consumer_name, owner)
        retry_delay = READ_RETRY_INITIAL
        while True:
            if task_failed.is_set():
                raise RuntimeError('Worker job failed before safe ACK; restarting for recovery')
            try:
                messages = await redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=consumer_name,
                    streams={STREAM_NAME: ">"},
                    count=settings.worker_max_concurrency,
                    block=READ_BLOCK_MS,
                )
            except (AuthenticationError, AuthorizationError):
                raise
            except (RedisTimeoutError, RedisConnectionError, TimeoutError, OSError) as exc:
                logging.warning(
                    "Worker Redis read failed (%s): %s; retrying in %.1fs",
                    type(exc).__name__, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, READ_RETRY_MAX)
                continue
            retry_delay = READ_RETRY_INITIAL

            if not messages:
                continue

            for _, entries in messages:
                for stream_id, fields in entries:
                    task = asyncio.create_task(run_with_limit(stream_id, fields))
                    in_flight.add(task)
                    task.add_done_callback(on_finished)

            if len(in_flight) >= settings.worker_max_concurrency:
                done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    if exception := finished.exception():
                        raise exception
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        await close_session()
        if owner:
            try:
                await redis.eval(COMPARE_DELETE, 1, OWNER_KEY, owner)
            except (RedisTimeoutError, RedisConnectionError, OSError):
                logging.warning('Could not release Worker ownership; next startup verifies process death')
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run())
