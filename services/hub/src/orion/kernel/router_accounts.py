"""Hub-scoped delegation and retry-safe account credentials on a pinned Router."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import quote
from uuid import UUID, uuid5

import aiohttp

from orion.kernel.local_security import SecurityStore, atomic_write, identity_lock, security_dir, tls_identity
from orion.kernel.pinned_router import router_session
from services.shared.router_connection import read_connection, write_connection

logger = logging.getLogger(__name__)
_pending = {}


class RouterAccountError(RuntimeError):
    pass


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


async def request_router(record, method, path, *, body=None, admin_password=None, account_key=None):
    headers = {'x-orion-hub-key': await asyncio.to_thread(_credential, record['id'], 'hub')}
    if admin_password is not None:
        headers['x-admin-key'] = quote(admin_password, safe='')
    if account_key:
        headers['Authorization'] = 'Bearer ' + account_key
    try:
        async with router_session(expected_router=record, account_route=getattr(account_key, 'route', False), timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.request(method, record['url'].rstrip('/') + path,
                                       headers=headers, json=body, allow_redirects=False) as response:
                if response.status == 401 and admin_password is not None:
                    raise RouterAccountError('Orion Router şifresini girin. Varsayılan şifre değiştirilmiş veya girilen şifre yanlış.')
                if response.status >= 400:
                    messages = {401: 'Router bağlantı yetkisi geçersiz. Orion Panel’den yeniden bağlayın.',
                                402: 'Router kullanım limitiniz dolmuş.', 403: 'Router erişiminiz kapatılmış.',
                                404: 'Router güncel değil veya bu sağlayıcı desteklenmiyor.',
                                409: 'Router hesap kaydı uyuşmuyor. Router yöneticisi kontrol etmeli.'}
                    raise RouterAccountError(messages.get(response.status, 'Router işlemi tamamlanamadı.'))
                return await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise RouterAccountError('Orion Router’a güvenli bağlantı kurulamadı.') from error


async def connect_router(record, password=None):
    identity = await asyncio.to_thread(tls_identity)
    token = await asyncio.to_thread(_credential, record['id'], 'hub')
    await request_router(record, 'POST', '/api/v1/hubs/enroll',
                         body={'id': identity['id'], 'name': identity['name'], 'token': token},
                         admin_password=password if password is not None else
                         (os.getenv('ROUTER_ADMIN_KEY') or os.getenv('ROUTER_API_KEY') or 'orion'))
    await asyncio.to_thread(atomic_write, _state_path(record['id']), b'{"connected":true}')


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


async def catalog_credentials(admin_key):
    record = await asyncio.to_thread(read_connection)
    if record and record.get('id') and await asyncio.to_thread(_state, record['id']):
        return '/api/v1/hubs/catalog/', {'x-orion-hub-key': await asyncio.to_thread(_credential, record['id'], 'hub')}
    return '/dashboard/api/', {'x-admin-key': quote(admin_key, safe='')}


async def account_settings(settings, user_id):
    from .router_access import current_route, account_key
    current_route.set(None)
    if user_id == 'global':
        return settings
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
