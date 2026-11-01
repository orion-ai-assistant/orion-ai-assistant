"""Owner-confirmed Router UUID and SPKI trust, with HTTPS LAN discovery."""
import asyncio
from contextlib import suppress
import logging
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from orion.api.hub_directory import HubDirectory
from orion.api.owner_routes import require_owner
from orion.api.router_providers import CredentialRoute
from orion.kernel.local_security import SecurityStore
from orion.kernel.pinned_router import verify_router, resolve_trusted_routers
from services.shared.router_connection import read_connection, write_connection, validate_url

router = APIRouter(prefix='/api/v1/owner/router', route_class=CredentialRoute)
logger = logging.getLogger(__name__)
SERVICE_TYPE = '_orion-router-tls._tcp.local.'


class RouterAssociation:
    def __init__(self, redis):
        self.redis = redis
        self.directory = HubDirectory(SERVICE_TYPE, '/v1')
        self.lock = asyncio.Lock()
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.run(), name='router-association')

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def refresh(self):
        async with self.lock:
            # A corrupt selection is not an absent selection. Read before any
            # bootstrap can write state, and let the owner repair it explicitly.
            previous = await asyncio.to_thread(read_connection)
            from orion.kernel.router_bootstrap import bootstrap_local_router
            await bootstrap_local_router()
            await resolve_trusted_routers()
            trusted = SecurityStore().routers()
            if previous:
                selected = next((entry for entry in trusted if entry['id'] == previous.get('id')), None)
                if selected and previous.get('fp') and previous['fp'] != selected['fp']:
                    raise RuntimeError('Kayıtlı Router parmak izi güven kaydıyla eşleşmiyor.')
                return selected
            if len(trusted) == 1:
                selected = {**trusted[0], 'mode': 'manual', 'last_seen': time.time()}
                await asyncio.to_thread(write_connection, selected)
                return trusted[0]
            return None

    async def current(self):
        """The saved selection as trusted now, without discovery or bootstrap; for frequent checks."""
        previous = await asyncio.to_thread(read_connection)
        if not previous:
            return None
        return next((entry for entry in SecurityStore().routers() if entry['id'] == previous.get('id')), None)

    async def run(self):
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.warning('Router discovery refresh failed; retaining confirmed trust')
            await asyncio.sleep(30)


def association(request):
    require_owner(request, 'admin')
    return request.app.state.router_association


@router.get('')
async def status(request: Request, light: bool = False):
    manager = association(request)
    try:
        selected = await (manager.current() if light else manager.refresh())
    except RuntimeError as error:
        raise HTTPException(503, 'Router bağlantı kaydı geçersiz; panelden kontrol edin.') from error
    return {'selected': selected, 'identity_verified': bool(selected), **await access_state(selected)}


async def access_state(selected):
    """Whether the Router admits this Hub; a same-PC Router admits it without asking anything."""
    from orion.kernel.router_accounts import ensure_connection, request_router, RouterAccountError, EnrollmentRequired
    if not selected:
        return {'access_ready': False, 'needs_code': False, 'message': 'Bu Hub’ın model bağlantısı henüz kurulmamış.'}
    try:
        await ensure_connection(selected)
        await request_router(selected, 'GET', '/api/v1/hubs/status')
    except EnrollmentRequired:
        return {'access_ready': False, 'needs_code': True,
                'message': 'Router bu Hub’ı henüz tanımıyor. Router panelinden bir bağlantı kodu oluşturup girin.'}
    except RouterAccountError as error:
        return {'access_ready': False, 'needs_code': False, 'message': str(error)}
    return {'access_ready': True, 'needs_code': False, 'message': 'Router bağlı. Kullanıcı erişimleri otomatik oluşturulur.'}


@router.get('/discover')
async def discover(request: Request):
    manager = association(request)
    from orion.kernel.router_bootstrap import local_router_identity
    records = await manager.directory.list()
    try:
        local = await local_router_identity()
        if local:
            # Installer files can outlive the process using this port. Only show
            # automatic local verification after checking the live UUID and pin.
            # This probe never enrolls trust or changes the selected Router.
            identity = await asyncio.wait_for(
                verify_router(local['url'], local['id'], local['fp']), timeout=2)
            local = {**local, 'name': identity.get('name') or local['name']}
    except Exception:
        local = None
    if local:
        records = [entry for entry in records if entry['id'] != local['id']]
        records.insert(0, {**{key: value for key, value in local.items() if key != 'fp'}, 'same_pc': True})
    return {'routers': records}


class Selection(BaseModel):
    id: UUID | None = None
    manual_url: str | None = None
    manual_id: UUID | None = None
    fp: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    # A dashboard connection code proves the Router's identity and admits this Hub in one step.
    code: str | None = Field(default=None, pattern=r'^[A-Za-z0-9 -]{12,24}$')

    @model_validator(mode='after')
    def one_source(self):
        if (self.id is None) == (self.manual_url is None):
            raise ValueError('Bir Router seç veya HTTPS adresi yaz.')
        if self.manual_url:
            self.manual_url = validate_url(self.manual_url)
        return self


async def _listed_router(manager, router_id):
    from orion.kernel.router_bootstrap import local_router_identity
    try:
        local = await local_router_identity()
    except Exception:
        local = None
    records = await manager.directory.list()
    if local and router_id == local['id']:
        records = [local]
    known = [entry for entry in SecurityStore().routers() if entry['id'] == router_id]
    matches = [entry for entry in records if entry['id'] == router_id] or known
    if len(matches) != 1 or matches[0].get('conflict'):
        raise HTTPException(409, 'Seçilen Router bulunamadı veya kimliği çakışıyor.')
    return matches[0]


NEEDS_CODE = 'Bu Router’ı ilk kez bağlıyorsun. Router panelinden bir bağlantı kodu oluşturup gir.'


@router.post('')
async def select(payload: Selection, request: Request):
    manager = association(request)
    if payload.code:
        from orion.api.login_limits import limit_attempts
        await limit_attempts(request, 'router-code', request.client.host, 10)
    async with manager.lock:
        if payload.id:
            record = await _listed_router(manager, str(payload.id))
        else:
            record = {'id': str(payload.manual_id) if payload.manual_id else None, 'name': 'Orion Router', 'url': payload.manual_url}
        base = validate_url(record['url'])
        if payload.code:
            from orion.kernel.pinned_router import prove_router
            try:
                identity = await prove_router(base, payload.code)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            except Exception as error:
                raise HTTPException(409, 'Router’a ulaşılamadı. Adresi ve Router’ın açık olduğunu kontrol edin.') from error
            if record['id'] and identity['id'] != record['id']:
                raise HTTPException(409, 'Bağlantı kodu seçilen Router’a ait değil.')
            fingerprint = identity['fp']
        else:
            known = next((entry for entry in SecurityStore().routers() if entry['id'] == record['id']), None)
            fingerprint = payload.fp or record.get('fp') or (known['fp'] if known else None)
            if fingerprint is None or not record['id']:
                raise HTTPException(400, NEEDS_CODE)
            try:
                identity = await verify_router(base, record['id'], fingerprint)
            except Exception as error:
                raise HTTPException(409, 'Router kimliği doğrulanamadı.') from error
        selected = {'mode': 'discovery' if payload.id else 'manual', 'id': identity['id'],
                    'name': identity.get('name') or record['name'], 'url': base, 'fp': fingerprint,
                    'last_seen': time.time()}
        await asyncio.to_thread(SecurityStore().trust_router, identity, base)
        await asyncio.to_thread(write_connection, selected)
        from orion.kernel.router_models import clear_catalog_cache
        clear_catalog_cache()
    if payload.code:
        from orion.kernel.router_accounts import selected_router, connect_router, RouterAccountError
        try:
            await connect_router(await selected_router(), payload.code)
        except RouterAccountError as error:
            raise HTTPException(400, str(error)) from error
    # A same-PC Router admits the Hub without a code; otherwise the owner is asked for one next.
    return {'selected': selected, 'identity_verified': True, **await access_state(selected)}
