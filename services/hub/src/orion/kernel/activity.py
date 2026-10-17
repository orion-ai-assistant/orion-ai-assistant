"""Cross-process record of user work that an explicit "update now" must never interrupt.

The Hub runs several API processes plus the Worker, so nothing here is process-local: short
Redis leases (they expire on their own if a process dies) mark voice, file-transfer and
remote-client work; chat turns and tool runs are read from the existing chat state.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
import uuid

from fastapi import HTTPException

from orion.contracts.constants import CHAT_STATE_KEY_PREFIX

PREFIX = 'orion:activity:'
REMOTE_CHAT = PREFIX + 'remote-chat:'
DRAIN_KEY = 'orion:update:draining'
LEASE_TTL = 45
DRAIN_TTL = 120


@asynccontextmanager
async def lease(redis, kind, *, remote=False, ttl=LEASE_TTL):
    """Mark `kind` ('voice' | 'transfer') work as running while the block executes."""
    key = f'{PREFIX}{kind}:{"remote" if remote else "local"}:{uuid.uuid4().hex}'
    renewer = None
    try:
        await redis.set(key, '1', ex=ttl)

        async def renew():
            while True:
                await asyncio.sleep(ttl / 3)
                await redis.expire(key, ttl)
        renewer = asyncio.create_task(renew())
    except Exception:
        pass  # Tracking is advisory; it must never fail the user's request.
    try:
        yield
    finally:
        if renewer is not None:
            renewer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await renewer
        with suppress(Exception):
            await redis.delete(key)


async def mark_remote_chat(redis, chat_id):
    """A chat turn started by a phone/remote session: counts as remote work while it runs."""
    with suppress(Exception):
        await redis.set(REMOTE_CHAT + chat_id, '1', ex=1800)


async def _keys(redis, pattern):
    found, cursor = [], 0
    while True:
        cursor, batch = await redis.scan(cursor, match=pattern, count=200)
        found.extend(batch)
        if not cursor:
            return found


async def snapshot(redis):
    """Counts of running work. `busy` is true when anything must be waited for."""
    counts = {'chat': 0, 'voice': 0, 'transfer': 0, 'remote': 0}
    for key in await _keys(redis, CHAT_STATE_KEY_PREFIX + '*'):
        if await redis.hget(key, 'status') in {'queued', 'processing'}:
            counts['chat'] += 1
            if await redis.exists(REMOTE_CHAT + key.removeprefix(CHAT_STATE_KEY_PREFIX)):
                counts['remote'] += 1
    for key in await _keys(redis, PREFIX + '*'):
        parts = key.removeprefix(PREFIX).split(':')
        if parts[0] in {'voice', 'transfer'} and len(parts) == 3:
            counts[parts[0]] += 1
            counts['remote'] += parts[1] == 'remote'
    return {'counts': counts, 'busy': any(counts.values())}


async def set_draining(redis, on):
    if on:
        await redis.set(DRAIN_KEY, '1', ex=DRAIN_TTL)
    else:
        await redis.delete(DRAIN_KEY)


async def draining(redis):
    try:
        return bool(await redis.exists(DRAIN_KEY))
    except Exception:
        return False


async def require_not_draining(redis):
    if await draining(redis):
        raise HTTPException(503, 'Güncelleme uygulanıyor; birkaç dakika sonra yeniden deneyin.',
                            headers={'Retry-After': '30'})
