"""Account sessions: one rule for every entry point (this PC's window, LAN/public browser, paired phone).

A session lasts as long as it is used. It ends after IDLE without any request, on logout, when the
account's password changes, when the account is deactivated or its device is revoked. The token is a
signed pointer to a durable session row (orion_sessions), so Hub restarts and updates keep it and the
client never has to renew it.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException

from orion.api.security import ALGORITHM, JWT_SECRET_KEY
from orion.kernel import registry

IDLE = timedelta(days=14)
# Bounds the idle precision and the writes: at most one last_seen update per session per interval.
TOUCH_EVERY = timedelta(minutes=5)
# RFC 6750: the one 401 after which the client shows the login screen. Other 401s (an owner
# password prompt, a wrong admin key) leave the account session alone.
ENDED_HEADERS = {'WWW-Authenticate': 'Bearer error="invalid_token"'}


def ended(detail='Oturum sona erdi.') -> HTTPException:
    return HTTPException(401, detail, headers=ENDED_HEADERS)


def unavailable(error: Exception) -> HTTPException:
    # Not knowing is not an ended session: the client retries instead of logging out.
    return HTTPException(503, 'Oturum şu an doğrulanamıyor; Hub veritabanı hazır değil.')


def credential(password_hash: str) -> str:
    """Changes with the password, whatever path changes it."""
    return hashlib.sha256(password_hash.encode('utf-8')).hexdigest()


def decode(token: str, *, verify_exp=True) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM], options={'verify_exp': verify_exp})
    except jwt.PyJWTError as error:
        raise ended() from error


async def issue(user: dict, device_id: str | None = None) -> str:
    session_id = secrets.token_urlsafe(24)
    try:
        await registry.create_session(session_id, user['id'], device_id, credential(user['password_hash']),
                                      forget_after=IDLE * 2)
    except RuntimeError as error:
        raise unavailable(error) from error
    claims = {'sub': user['id'], 'sid': session_id, 'iat': datetime.now(timezone.utc)}
    if device_id:
        claims['device_id'] = device_id
    return jwt.encode(claims, JWT_SECRET_KEY, algorithm=ALGORITHM)


async def check(claims: dict) -> None:
    """The session behind a decoded token is still open; using it keeps it open."""
    try:
        record = await registry.get_session(claims['sid'])
    except RuntimeError as error:
        raise unavailable(error) from error
    now = datetime.now(timezone.utc)
    if (not record or record['revoked_at'] is not None or record['user_id'] != claims.get('sub')
            or record['device_id'] != claims.get('device_id') or not record['is_active']
            or now - record['last_seen_at'] > IDLE
            or record['credential'] != credential(record['password_hash'])):
        raise ended()
    if now - record['last_seen_at'] > TOUCH_EVERY:
        try:
            await registry.touch_session(claims['sid'])
        except RuntimeError:
            # The request is authenticated; the next one records the use.
            logging.getLogger(__name__).warning('Session use could not be recorded', exc_info=True)


async def revoke(token: str) -> None:
    """Logout: an expired or foreign token has nothing left to end."""
    try:
        claims = decode(token, verify_exp=False)
    except HTTPException:
        return
    if isinstance(claims.get('sid'), str):
        try:
            await registry.revoke_sessions(session_id=claims['sid'])
        except RuntimeError as error:
            raise unavailable(error) from error


async def revoke_device(device_id: str) -> None:
    # The device boundary already refuses a revoked device; this also closes its account sessions.
    try:
        await registry.revoke_sessions(device_id=device_id)
    except RuntimeError:
        logging.getLogger(__name__).warning('Sessions of a revoked device stay closed by its device check only', exc_info=True)


_SECRET_QUERY = re.compile(r'([?&](?:token|admin_key|device_token)=)[^&\s"]+')


class RedactQuerySecrets(logging.Filter):
    """WebSocket and SSE clients pass the account token in the query; access logs never keep it."""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(_SECRET_QUERY.sub(r'\1***', value) if isinstance(value, str) else value
                                for value in record.args)
        elif isinstance(record.msg, str):
            record.msg = _SECRET_QUERY.sub(r'\1***', record.msg)
        return True


def redact_server_logs() -> None:
    for name in ('uvicorn.access', 'uvicorn.error'):
        logger = logging.getLogger(name)
        if not any(isinstance(existing, RedactQuerySecrets) for existing in logger.filters):
            logger.addFilter(RedactQuerySecrets())
