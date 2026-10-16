"""Authenticated account-only Router override controls. No raw secret reads."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool

from orion.api.auth_routes import get_current_user
from orion.api.router_providers import CredentialRoute
from orion.kernel import router_accounts as accounts
from orion.kernel.router_access import manual_state, manual_path, save_manual
from orion.kernel.config import mask_api_key

router = APIRouter(prefix='/api/v1/router/access', route_class=CredentialRoute)


async def identity(user, router_id=None):
    if user == 'global':
        raise HTTPException(403, 'Kullanıcı hesabıyla giriş yapın.')
    record = await accounts.selected_router()
    if router_id is not None and record['id'] != str(router_id):
        raise HTTPException(409, 'Seçilen Router değişti; ekranı yenileyin.')
    subject, _ = await accounts.account_identity(user)
    return record, subject


@router.get('')
async def status(user: str = Depends(get_current_user)):
    record, subject = None, None
    try:
        record, subject = await identity(user)
        await accounts.provision_if_available(user, record)
        state = await asyncio.to_thread(manual_state, record['id'], subject)
        return {'router_id': record['id'], 'router_name': record['name'],
                'managed_registered': bool(await asyncio.to_thread(accounts._state, record['id'], subject)),
                'manual_saved': bool(state), 'manual_enabled': bool(state and state.get('enabled')),
                'manual_hint': mask_api_key(state.get('key')) if state else ''}
    except accounts.RouterAccountError as error:
        if record and subject:
            return {'router_id': record['id'], 'router_name': record['name'], 'message': str(error),
                    'managed_registered': bool(await asyncio.to_thread(accounts._state, record['id'], subject)),
                    'manual_saved': manual_path(record['id'], subject).exists(), 'manual_enabled': False,
                    'manual_hint': 'Okunamadı; değiştirin veya kaldırın.'}
        return {'router_id': None, 'message': str(error), 'managed_registered': False,
                'manual_saved': False, 'manual_enabled': False}


class ManualKey(BaseModel):
    router_id: UUID
    key: str | None = Field(default=None, pattern=r'^sk-orion-[A-Za-z0-9_-]{16,512}$')
    enabled: StrictBool = True
    model_config = {'extra': 'forbid'}


@router.put('')
async def save(payload: ManualKey, user: str = Depends(get_current_user)):
    try:
        record, subject = await identity(user, payload.router_id)
        await asyncio.to_thread(save_manual, record['id'], subject, key=payload.key, enabled=payload.enabled)
        return {'saved': True}
    except (accounts.RouterAccountError, ValueError) as error:
        raise HTTPException(409, str(error)) from error


@router.delete('')
async def remove(router_id: UUID, user: str = Depends(get_current_user)):
    try:
        record, subject = await identity(user, router_id)
        await asyncio.to_thread(save_manual, record['id'], subject, remove=True)
        return {'removed': True}
    except accounts.RouterAccountError as error:
        raise HTTPException(409, str(error)) from error
