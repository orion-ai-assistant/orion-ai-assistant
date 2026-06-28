from __future__ import annotations

import redis.asyncio as aioredis

from config.settings.api_server import REDIS_URL

_redis_pool: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool

    url = REDIS_URL or "redis://localhost:6379"
    _redis_pool = aioredis.from_url(
        url,
        decode_responses=True,
        max_connections=500,
    )
    return _redis_pool
