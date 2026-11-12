"""Hub-scoped delegation and retry-safe account credentials on a pinned Router."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from uuid import UUID, uuid5

import aiohttp

from orion.kernel.local_security import SecurityStore, atomic_write, identity_lock, security_dir, tls_identity
from orion.kernel.pinned_router import router_session
from services.shared.router_connection import read_connection, write_connection

logger = logging.getLogger(__name__)
_pending = {}


class RouterAccountError(RuntimeError):
    pass


class EnrollmentRequired(RouterAccountError):
    """The Router has not admitted this Hub yet; only the Hub owner can finish it."""
    def __init__(self, message='Router bağlantısı Hub sahibinin onayını bekliyor. '
                               'Hub sahibi, Hub panelindeki Router bağlantısı sayfasından bağlantıyı tamamlamalı.'):
        super().__init__(message)


def _material():
    directory = security_dir()
    with identity_lock(directory):
        path = directory / 'router-access-seed'
        if not path.exists():
            atomic_write(path, os.urandom(32))
        seed = path.read_bytes()
        if len(seed) != 32:
            raise RouterAccountError('Router erişim kaydı okunamadı.')
    identity = tls_identity()
    return seed, identity


def _credential(router_id, kind, subject=''):
    seed, identity = _material()
    message = json.dumps([identity['id'], router_id, kind, subject], separators=(',', ':')).encode()
    value = base64.urlsafe_b64encode(hmac.new(seed, message, hashlib.sha256).digest()).decode().rstrip('=')
    return ('hub-orion-' if kind == 'hub' else 'sk-orion-') + value


def _state_path(router_id, subject=None):
    router_id = str(UUID(router_id))
    suffix = 'hub' if subject is None else str(UUID(subject))
    return security_dir() / f'router-access-{router_id}-{suffix}.json'


def _state(router_id, subject=None):
    path = _state_path(router_id, subject)
    return json.loads(path.read_text()) if path.exists() else None


async def selected_router():
    selected = await asyncio.to_thread(read_connection)
    if not selected:
        from orion.kernel.router_bootstrap import bootstrap_local_router
        await bootstrap_local_router()
        trusted = await asyncio.to_thread(lambda: SecurityStore().routers())
        if len(trusted) == 1:
            selected = {**trusted[0], 'mode': 'manual'}
            await asyncio.to_thread(write_connection, selected)
    if not selected or not selected.get('id'):
        raise RouterAccountError('Bu Hub’ın model bağlantısı henüz kurulmamış. Orion Panel’den Router bağlayın.')
    # Use the trusted current address (discovery may have updated the IP).
    trusted = await asyncio.to_thread(lambda: SecurityStore().routers())
    record = next((r for r in trusted if r['id'] == selected['id']), None)
    if not record:
        raise RouterAccountError('Router kimliği henüz doğrulanmamış. Orion Panel’den bağlantıyı kontrol edin.')
    return record


async def request_router(record, method, path, *, body=None, enrollment=None, account_key=None):
    headers = {'x-orion-hub-key': await asyncio.to_thread(_credential, record['id'], 'hub'), **(enrollment or {})}
    if account_key:
        headers['Authorization'] = 'Bearer ' + account_key
    try:
        async with router_session(expected_router=record, account_route=getattr(account_key, 'route', False), timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.request(method, record['url'].rstrip('/') + path,
                                       headers=headers, json=body, allow_redirects=False) as response:
                if response.status == 401 and 'x-orion-enrollment-code' in headers:
                    raise RouterAccountError('Bağlantı kodu geçersiz veya süresi dolmuş. Router panelinden yeni kod oluşturun.')
                if response.status == 401 and enrollment is not None:
                    raise EnrollmentRequired()
                if response.status == 401 and not account_key:
                    # The Router no longer knows this Hub (removed by its administrator): forget the old
                    # admission so the owner is asked for a new connection code.
                    await asyncio.to_thread(_state_path(record['id']).unlink, missing_ok=True)
                    forget_account_keys()
                    raise EnrollmentRequired('Router bu Hub’ı kaldırmış. Hub sahibi, Hub panelinden yeni bir bağlantı kodu ile yeniden bağlamalı.')
                if response.status >= 400:
                    messages = {401: 'Router bağlantı yetkisi geçersiz. Orion Panel’den yeniden bağlayın.',
                                402: 'Router kullanım limitiniz dolmuş.', 403: 'Router erişiminiz kapatılmış.',
                                404: 'Router güncel değil veya bu sağlayıcı desteklenmiyor.',
                                409: 'Router hesap kaydı uyuşmuyor. Router yöneticisi kontrol etmeli.'}
                    raise RouterAccountError(messages.get(response.status, 'Router işlemi tamamlanamadı.'))
                return await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise RouterAccountError('Orion Router’a güvenli bağlantı kurulamadı.') from error


def local_enrollment_token(record):
    """The same-PC Router's enrollment token, read from its registered installation; never from the network."""
    from orion.kernel.router_bootstrap import read_local_router_candidate
    try:
        candidate = read_local_router_candidate()
        if candidate is None:
            return None
        identity_path = Path(candidate['identity_path'])
        if str(UUID(identity_path.read_text(encoding='ascii').strip())) != record['id']:
            return None
        token = (identity_path.parent / 'hub-enrollment-token').read_text(encoding='ascii').strip()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        # No readable same-PC Router: the owner is asked for a connection code instead.
        logger.info('Same-PC Router enrollment unavailable: %s', error)
        return None
    return token if 32 <= len(token) <= 128 else None


async def connect_router(record, code=None):
    """Admit this Hub with a dashboard connection code, or the same-PC Router's local token."""
    if code:
        enrollment = {'x-orion-enrollment-code': code}
    else:
        token = await asyncio.to_thread(local_enrollment_token, record)
        if not token:
            raise EnrollmentRequired()
        enrollment = {'x-orion-local-enrollment': token}
    identity = await asyncio.to_thread(tls_identity)
    token = await asyncio.to_thread(_credential, record['id'], 'hub')
    await request_router(record, 'POST', '/api/v1/hubs/enroll',
                         body={'id': identity['id'], 'name': identity['name'], 'token': token}, enrollment=enrollment)
    await asyncio.to_thread(atomic_write, _state_path(record['id']), b'{"connected":true}')
    forget_account_keys()


async def ensure_connection(record):
    if not await asyncio.to_thread(_state, record['id']):
        await connect_router(record)


async def account_identity(user_id):
    from orion.kernel.registry import get_user_by_username
    user = await get_user_by_username(user_id)
    if not user:
        raise RouterAccountError('Kullanıcı hesabı bulunamadı.')
    identity = await asyncio.to_thread(tls_identity)
    # The existing account ID is the username. Include its creation timestamp
    # so deleting/recreating a name does not reuse the previous account's key.
    subject = str(uuid5(UUID(identity['id']), json.dumps([user['id'], str(user['created_at'])])))
    return subject, user['id']


async def ensure_account(user_id, record=None, *, force=False):
    record = record or await selected_router()
    subject, name = await account_identity(user_id)
    key = await asyncio.to_thread(_credential, record['id'], 'account', subject)
    state = await asyncio.to_thread(_state, record['id'], subject)
    if force or not state:
        await ensure_connection(record)
        state = await request_router(record, 'POST', '/api/v1/hubs/accounts',
                                     body={'subject_id': subject, 'name': name, 'key': key})
        await asyncio.to_thread(atomic_write, _state_path(record['id'], subject), json.dumps(state).encode())
    from .router_access import account_key
    return await account_key(user_id, record, subject, key)


async def provision_if_available(user_id, record=None):
    # Avoid repeatedly waiting for an unavailable Router during UI polling.
    marker = (str(security_dir()), user_id, record['id'] if record else None)
    if time.monotonic() < _pending.get(marker, 0):
        return
    try:
        await ensure_account(user_id, record)
        _pending.pop(marker, None)
    except RouterAccountError:
        # Account creation is durable even if Router is offline. First model
        # use/settings access retries; a shared admin key is never substituted.
        logger.info('Router account provisioning pending')
        _pending[marker] = time.monotonic() + 15


async def catalog_credentials():
    """Only an admitted Hub reads the shared catalog; never with the Router administrator password."""
    record = await selected_router()
    await ensure_connection(record)
    return '/api/v1/hubs/catalog/', {'x-orion-hub-key': await asyncio.to_thread(_credential, record['id'], 'hub')}


# The account's Router key, as account_settings binds it, for ACCOUNT_KEY_SECONDS. Binding it reads the
# selected Router, the account's identity, its encrypted key and its provisioning state from disk (about
# 20 ms), and most requests (chat list, history, send, settings) ask for it. A Router that changed in the
# meantime is still caught before anything is sent (router_access.validate_target); failures are never kept.
ACCOUNT_KEY_SECONDS = 30.0
_bound_keys: dict[str, tuple[float, object]] = {}


def forget_account_keys(user_id: str | None = None) -> None:
    """After a change of Router or of an account's key: the next request binds again."""
    if user_id is None:
        _bound_keys.clear()
    else:
        _bound_keys.pop(user_id, None)


async def account_settings(settings, user_id):
    from .router_access import current_route, account_key
    current_route.set(None)
    if user_id == 'global':
        return settings
    cached = _bound_keys.get(user_id)
    if cached and time.monotonic() < cached[0]:
        bound = cached[1]
        current_route.set(bound.route)
        return settings.model_copy(update={'router_api_key': str(bound)})
    try:
        record = await selected_router()
    except RouterAccountError:
        record = await asyncio.to_thread(read_connection)
        if not record or not record.get('id'):
            # No selected Router is not permission to inherit shared admin access.
            return settings.model_copy(update={'router_api_key': 'sk-orion-unavailable'})
        # A missing/revoked selected Router must never restore shared admin access.
    subject, _ = await account_identity(user_id)
    key = await asyncio.to_thread(_credential, record['id'], 'account', subject)
    await provision_if_available(user_id, record)
    bound = await account_key(user_id, record, subject, key)
    _bound_keys[user_id] = (time.monotonic() + ACCOUNT_KEY_SECONDS, bound)
    current_route.set(bound.route)
    return settings.model_copy(update={'router_api_key': str(bound)})


async def reconcile_accounts():
    """The durable user DB minus Router receipts is the pending account queue."""
    from .registry import list_active_users
    record = await selected_router()
    await ensure_connection(record)
    remote = await request_router(record, 'GET', '/api/v1/hubs/accounts')
    known = {row['subject_id'] for row in remote['accounts']}
    for user in await list_active_users():
        subject, _ = await account_identity(user['id'])
        if subject not in known or not await asyncio.to_thread(_state, record['id'], subject):
            await ensure_account(user['id'], record, force=True)


async def provisioning_loop():
    while True:
        try:
            await reconcile_accounts()
        except Exception:
            logger.info('Router account reconciliation pending')
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        await asyncio.sleep(15)
