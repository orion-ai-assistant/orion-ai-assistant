"""Account-only provider management; Router connection remains in Orion Panel."""
from fastapi import APIRouter, Depends, HTTPException, Request, Path
from pydantic import BaseModel, Field
from uuid import UUID

from orion.api.auth_routes import get_current_user
from orion.kernel.router_accounts import (RouterAccountError, selected_router, ensure_account,
                                          request_router)

router = APIRouter(prefix='/api/v1/router', tags=['Provider settings'])


async def account(user_id):
    if user_id == 'global':
        raise HTTPException(403, 'Kişisel sağlayıcı eklemek için kullanıcı hesabıyla giriş yapın.')
    record = await selected_router()
    key = await ensure_account(user_id, record)
    return record, key


@router.get('/providers')
async def status(user_id: str = Depends(get_current_user)):
    try:
        record, key = await account(user_id)
        result = await request_router(record, 'GET', '/api/v1/hubs/providers', account_key=key)
        return {**result, 'managed': True, 'connected': True, 'router_name': record['name'], 'router_id': record['id']}
    except RouterAccountError as error:
        message = str(error)
        if 'şifre' in message or 'Orion Panel' in message:
            message = 'Bu Hub’ın Router erişimi henüz kurulmamış. Hub yöneticisinin Orion Panel’den bağlantıyı tamamlaması gerekiyor.'
        return {'managed': True, 'connected': False, 'message': message, 'providers': [], 'configured': []}


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
        record, key = await account(user_id)
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
        record, key = await account(user_id)
        if record['id'] != str(router_id):
            raise HTTPException(409, 'Hub’ın Router bağlantısı değişti. Ayarları yenileyin.')
        return await request_router(record, 'DELETE', '/api/v1/hubs/providers/' + provider, account_key=key)
    except RouterAccountError as error:
        raise HTTPException(503, str(error)) from error
