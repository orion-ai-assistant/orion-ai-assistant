"""Shared, atomic login cooldowns for accounts and the local owner."""
import hashlib
import logging

from fastapi import HTTPException, Request

WINDOW_SECONDS = 30
SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


async def limit_attempts(request: Request, scope: str, value: str, threshold: int) -> None:
    key = 'auth:limit:v2:' + scope + ':' + hashlib.sha256(value.encode()).hexdigest()
    try:
        count, remaining = await request.app.state.redis.eval(SCRIPT, 1, key, WINDOW_SECONDS)
    except Exception as exc:
        logging.getLogger(__name__).exception('Login limiter unavailable')
        raise HTTPException(503, 'Giriş geçici olarak kullanılamıyor.') from exc
    if int(count) > threshold:
        wait = max(1, int(remaining))
        raise HTTPException(429, 'Çok fazla giriş denemesi.', headers={'Retry-After': str(wait)})


async def limit_login(request: Request, username: str) -> None:
    await limit_attempts(request, 'account', username, 5)
    await limit_attempts(request, 'ip', request.client.host if request.client else 'unknown', 10)
