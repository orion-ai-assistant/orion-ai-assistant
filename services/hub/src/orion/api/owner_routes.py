"""Local owner authentication and user management. New accounts join through the app's pairing QR."""
import hashlib
import json
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from orion.api.access_boundary import OWNER_COOKIE, owner_cookie_name
from orion.api.login_limits import limit_attempts
from orion.api.security import valid_admin_key
from orion.api import invitations
from orion.kernel.registry import set_user_active

router = APIRouter(prefix='/api/v1/owner', tags=['local owner'])
# Idle limit of the elevated owner session: every authenticated request starts it again.
SESSION_SECONDS = 15 * 60


def fingerprint():
    from orion.api.security import admin_credential_fingerprint
    return admin_credential_fingerprint()


async def authenticate_owner_session(request: Request):
    token = request.headers.get('X-Orion-Owner-Session') or request.cookies.get(owner_cookie_name(request.scope))
    if not token or len(token) > 128:
        return
    key = 'auth:owner:' + hashlib.sha256(token.encode()).hexdigest()
    try:
        raw = await request.app.state.redis.get(key)
        record = json.loads(raw) if raw else {}
    except Exception:
        # Owner credentials fail closed; normal bearer authentication still works.
        return
    if record.get('tab_session') and not request.headers.get('X-Orion-Owner-Session'):
        return
    if record.get('fingerprint') == fingerprint() and record.get('scope') in {'people', 'admin'}:
        request.state.owner_scope = record['scope']
        if request.headers.get('X-Orion-Background-Refresh') == '1':
            return  # A page refreshing itself is not the owner's activity; the idle limit keeps running.
        try:
            await request.app.state.redis.expire(key, SESSION_SECONDS)
        except Exception:
            pass  # Valid for this request; it ends on its previous deadline.


def require_owner(request: Request, scope=None):
    if not getattr(request.state, 'local_owner_access', False):
        raise HTTPException(403, 'Yönetim yalnızca bu PC üzerinden açılabilir.')
    owner_scope = getattr(request.state, 'owner_scope', None)
    if owner_scope not in ({scope} if scope else {'people', 'admin'}):
        raise HTTPException(401, 'Yönetici şifrenizi girin.')


class OwnerLogin(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    scope: Literal['people', 'admin']
    tab_session: bool = False


@router.post('/login')
async def login(payload: OwnerLogin, request: Request, response: Response):
    if not getattr(request.state, 'local_owner_access', False):
        raise HTTPException(403, 'Yerel yönetim erişimi gerekli.')
    await limit_attempts(request, 'owner', request.client.host, 5)
    if not valid_admin_key(payload.password):
        raise HTTPException(401, 'Yönetici şifresi yanlış.')
    token = secrets.token_urlsafe(32)
    try:
        await request.app.state.redis.set('auth:owner:' + hashlib.sha256(token.encode()).hexdigest(),
                                          json.dumps({'scope': payload.scope, 'fingerprint': fingerprint(), 'tab_session': payload.tab_session}), ex=SESSION_SECONDS)
    except Exception as exc:
        raise HTTPException(503, 'Yönetim oturumu açılamıyor.') from exc
    if not payload.tab_session:
        # No max_age: the browser keeps it while the server-side session stays in use.
        response.set_cookie(owner_cookie_name(request.scope), token, httponly=True, samesite='strict', secure=request.scope['scheme'] == 'https', path=request.scope.get('root_path') or '/')
    response.headers['Cache-Control'] = 'no-store'
    result = {'scope': payload.scope, 'expires_in': SESSION_SECONDS}
    if payload.tab_session:
        result['token'] = token
    return result


@router.get('/session')
async def session(request: Request):
    require_owner(request)
    return {'scope': request.state.owner_scope}


@router.post('/logout')
async def logout(request: Request, response: Response):
    token = request.headers.get('X-Orion-Owner-Session') or request.cookies.get(owner_cookie_name(request.scope))
    if token:
        try:
            await request.app.state.redis.delete('auth:owner:' + hashlib.sha256(token.encode()).hexdigest())
        except Exception as exc:
            raise HTTPException(503, 'Oturum kapatılamıyor.') from exc
    response.delete_cookie(owner_cookie_name(request.scope), path=request.scope.get('root_path') or '/')
    return {'ok': True}


@router.get('/people')
async def people(request: Request):
    require_owner(request)
    return {'users': await invitations.list_people(), 'invitations': await invitations.list_invitations()}


@router.delete('/invitations/{identifier}')
async def revoke(identifier: str, request: Request):
    require_owner(request)
    await invitations.revoke_invitation(identifier)
    return {'ok': True}


class ActiveRequest(BaseModel):
    is_active: bool


@router.put('/people/{username}')
async def activate(username: str, payload: ActiveRequest, request: Request):
    require_owner(request)
    if username == 'global':
        raise HTTPException(404, 'Kullanıcı bulunamadı.')
    try:
        updated = await set_user_active(username, payload.is_active)
    except RuntimeError as exc:
        raise HTTPException(503, 'Kullanıcı veritabanına ulaşılamıyor.') from exc
    if not updated:
        raise HTTPException(404, 'Kullanıcı bulunamadı.')
    return {'username': username, 'is_active': payload.is_active}
