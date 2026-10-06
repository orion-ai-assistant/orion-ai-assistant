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
from orion.kernel.local_security import SecurityStore
from orion.kernel.pinned_router import verify_router, resolve_trusted_routers
from services.shared.router_connection import read_connection, write_connection, validate_url

router = APIRouter(prefix='/api/v1/owner/router')
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
            from orion.kernel.router_bootstrap import bootstrap_local_router
            await bootstrap_local_router()
            await resolve_trusted_routers()
            trusted = SecurityStore().routers()
            try:
                previous = await asyncio.to_thread(read_connection)
            except RuntimeError:
                previous = None
            if previous:
                return next((entry for entry in trusted if entry['id'] == previous.get('id')), None)
            if len(trusted) == 1:
                selected = {**trusted[0], 'mode': 'manual', 'last_seen': time.time()}
                await asyncio.to_thread(write_connection, selected)
                return trusted[0]
            return None

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
async def status(request: Request):
    return {'selected': await association(request).refresh(), 'identity_verified': True}


@router.get('/discover')
async def discover(request: Request):
    manager = association(request)
    from orion.kernel.router_bootstrap import local_router_identity
    records = await manager.directory.list()
    try:
        local = await local_router_identity()
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

    @model_validator(mode='after')
    def one_source(self):
        if (self.id is None) == (self.manual_url is None):
            raise ValueError('Bir Router seç veya HTTPS adresi yaz.')
        if self.manual_url:
            self.manual_url = validate_url(self.manual_url)
            if not self.manual_id:
                raise ValueError('Router ekranındaki UUID gerekli.')
        return self


@router.post('')
async def select(payload: Selection, request: Request):
    manager = association(request)
    async with manager.lock:
        if payload.id:
            from orion.kernel.router_bootstrap import local_router_identity
            try:
                local = await local_router_identity()
            except Exception:
                local = None
            records = await manager.directory.list()
            if local and str(payload.id) == local['id']:
                records = [local]
            matches = [entry for entry in records if entry['id'] == str(payload.id)]
            if len(matches) != 1 or matches[0].get('conflict'):
                raise HTTPException(409, 'Seçilen Router bulunamadı veya kimliği çakışıyor.')
            record = matches[0]
        else:
            record = {'id': str(payload.manual_id), 'name': 'Orion Router', 'url': payload.manual_url}
        base = validate_url(record['url'])
        known = next((entry for entry in SecurityStore().routers() if entry['id'] == record['id']), None)
        fingerprint = payload.fp or record.get('fp') or (known['fp'] if known else None)
        if fingerprint is None:
            raise HTTPException(400, 'Yeni Router için kendi ekranındaki SPKI parmak izini girin.')
        try:
            identity = await verify_router(base, record['id'], fingerprint)
        except Exception as error:
            raise HTTPException(409, 'Router UUID veya parmak izi doğrulanamadı.') from error
        selected = {'mode': 'discovery' if payload.id else 'manual', 'id': record['id'],
                    'name': identity.get('name') or record['name'], 'url': base, 'fp': fingerprint,
                    'last_seen': time.time()}
        await asyncio.to_thread(SecurityStore().trust_router, identity, base)
        await asyncio.to_thread(write_connection, selected)
    return {'selected': selected, 'identity_verified': True}
