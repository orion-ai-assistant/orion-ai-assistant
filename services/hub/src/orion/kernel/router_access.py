"""Per-account, per-Router override and request-local routing snapshot."""
from contextvars import ContextVar
from dataclasses import dataclass
import asyncio
import base64
import json
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from orion.kernel.local_security import atomic_write, identity_lock, security_dir

current_route = ContextVar('orion_account_router', default=None)


@dataclass(frozen=True, repr=False)
class AccountRoute:
    record: dict
    user: str
    subject: str
    managed_key: str
    manual_key: str | None = None


class AccountKey(str):
    def __new__(cls, value, route):
        result = super().__new__(cls, value)
        result.route = route
        return result


def manual_path(router_id, subject):
    from .router_accounts import _state_path
    return _state_path(router_id, subject).with_suffix('.manual')


def _cipher():
    from .router_accounts import _material
    seed, _ = _material()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'orion-router-manual-credential-v1').derive(seed)
    return Fernet(base64.urlsafe_b64encode(key))


def manual_state(router_id, subject):
    path = manual_path(router_id, subject)
    if not path.exists():
        return None
    try:
        state = json.loads(_cipher().decrypt(path.read_bytes()))
        if (not isinstance(state, dict) or not isinstance(state.get('key'), str)
                or not state['key'].startswith('sk-orion-') or type(state.get('enabled')) is not bool):
            raise ValueError('Invalid manual credential state')
        return state
    except Exception as error:
        from .router_accounts import RouterAccountError
        raise RouterAccountError('Manuel Router anahtarı okunamadı; kaldırın veya yeniden kaydedin.') from error


def save_manual(router_id, subject, *, key=None, enabled=True, remove=False):
    path = manual_path(router_id, subject)
    with identity_lock(security_dir()):
        if remove:
            path.unlink(missing_ok=True)
            return
        # Resolve encryption material outside this lock (identity locks are not reentrant).
        previous = path.read_bytes() if path.exists() else None
    cipher = _cipher()
    state = json.loads(cipher.decrypt(previous)) if previous and key is None else {}
    if key is not None:
        state['key'] = key
    if not state.get('key'):
        raise ValueError('Önce manuel bir sanal anahtar kaydedin.')
    state['enabled'] = enabled
    with identity_lock(security_dir()):
        current = path.read_bytes() if path.exists() else None
        if current != previous:
            raise ValueError('Router anahtar ayarı değişti; yenileyip tekrar deneyin.')
        atomic_write(path, cipher.encrypt(json.dumps(state).encode()))


async def account_key(user, record, subject, managed):
    state = await asyncio.to_thread(manual_state, record['id'], subject)
    manual = state['key'] if state and state.get('enabled') else None
    route = AccountRoute(dict(record), user, subject, managed, manual)
    return AccountKey(manual or managed, route)


def validate_target(route, url):
    """Reject selection/trust changes before transmitting any credential."""
    from .local_security import SecurityStore
    from .router_accounts import RouterAccountError
    target = urlsplit(str(url))
    record = next((r for r in SecurityStore().routers() if r['id'] == route.record['id']), None)
    if record:
        expected = urlsplit(record['url'])
        if (target.scheme in {'https', 'wss'} and target.hostname == expected.hostname
                and (target.port or 443) == (expected.port or 443) and record['fp'] == route.record['fp']):
            return
    raise RouterAccountError('Router bağlantısı değişti; isteği yeniden gönderin. Eski anahtar başka Router’a gönderilmedi.')
