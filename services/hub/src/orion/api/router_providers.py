"""Account-only provider management; Router connection remains in Orion Panel."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, Path
from pydantic import BaseModel, Field
from uuid import UUID
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from orion.api.auth_routes import get_current_user
from orion.kernel.router_accounts import (RouterAccountError, selected_router, ensure_account,
                                          request_router)
from orion.kernel.local_security import SecurityStore
from orion.kernel.router_accounts import _state

class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                return JSONResponse(status_code=422, content={'detail': [
                    {k:e[k] for k in ('loc','msg','type') if k in e} for e in error.errors()]})
        return safe_handler


router = APIRouter(prefix='/api/v1/router', tags=['Provider settings'], route_class=CredentialRoute)


async def account(user_id, router_id=None):
    if user_id == 'global':
        raise HTTPException(403, 'Kişisel sağlayıcı eklemek için kullanıcı hesabıyla giriş yapın.')
    record = await selected_router()
    if router_id is not None and record['id'] != str(router_id):
        records = await asyncio.to_thread(lambda: SecurityStore().routers())
        record = next((r for r in records if r['id'] == str(router_id)), None)
        if not record or not await asyncio.to_thread(_state, record['id']):
            raise HTTPException(409, 'Hub’ın Router bağlantısı değişti. Ayarları yenileyin.')
    key = await ensure_account(user_id, record)
    return record, key


@router.get('/providers')
async def status(user_id: str = Depends(get_current_user)):
    record = None
    try:
        record, key = await account(user_id)
        result = await request_router(record, 'GET', '/api/v1/hubs/providers', account_key=key)
        current = {**result, 'connected': True, 'router_name': record['name'], 'router_id': record['id']}
    except RouterAccountError as error:
        message = str(error)
        if 'şifre' in message or 'Orion Panel' in message:
            message = 'Bu Hub’ın Router erişimi henüz kurulmamış. Hub yöneticisinin Orion Panel’den bağlantıyı tamamlaması gerekiyor.'
        current = {'connected': False, 'message': message, 'providers': [], 'configured': []}
        if record:
            current.update(router_id=record['id'], router_name=record['name'])
    routers = [current] if record else []
    records = await asyncio.to_thread(lambda: SecurityStore().routers())
    for other in records:
        if (record and other['id'] == record['id']) or not await asyncio.to_thread(_state, other['id']):
            continue
        try:
            other_key = await ensure_account(user_id, other)
            data = await request_router(other, 'GET', '/api/v1/hubs/providers', account_key=other_key)
            routers.append({**data, 'connected': True, 'router_id': other['id'], 'router_name': other['name']})
        except RouterAccountError as error:
            routers.append({'connected': False, 'router_id': other['id'], 'router_name': other['name'], 'message': str(error)})
    primary = next((r for r in routers if r['connected']), current)
    return {**primary, 'managed': True, 'routers': routers,
            'active_keys': sum(r.get('active_keys', 0) for r in routers),
            'active_routers': sum(bool(r.get('active_keys')) for r in routers)}


class Credential(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)
    router_id: UUID


@router.put('/providers/{provider}')
async def save(payload: Credential, request: Request,
               provider: str = Path(pattern=r'^[a-z][a-z0-9_-]{0,63}$'),
               user_id: str = Depends(get_current_user)):
    from orion.api.login_limits import limit_attempts
    await limit_attempts(request, 'provider-save', user_id, 20)
    try:
        record, key = await account(user_id, payload.router_id)
        if record['id'] != str(payload.router_id):
            raise HTTPException(409, 'Hub’ın Router bağlantısı değişti. Ayarları yenileyin.')
        result = await request_router(record, 'PUT', '/api/v1/hubs/providers/' + provider,
                                      body={'api_key': payload.api_key}, account_key=key)
        return result
    except RouterAccountError as error:
        raise HTTPException(503, str(error)) from error


@router.delete('/providers/{provider}')
async def remove(router_id: UUID, provider: str = Path(pattern=r'^[a-z][a-z0-9_-]{0,63}$'), user_id: str = Depends(get_current_user)):
    try:
        record, key = await account(user_id, router_id)
        if record['id'] != str(router_id):
            raise HTTPException(409, 'Hub’ın Router bağlantısı değişti. Ayarları yenileyin.')
        return await request_router(record, 'DELETE', '/api/v1/hubs/providers/' + provider, account_key=key)
    except RouterAccountError as error:
        raise HTTPException(503, str(error)) from error
