"""Local owner authentication and invitation/user management."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from services.shared.environment import get_env
from orion.api.access_boundary import OWNER_COOKIE
from orion.api.login_limits import limit_attempts
from orion.api.security import valid_admin_key
from orion.api import invitations
from orion.kernel.registry import set_user_active

router = APIRouter(prefix='/api/v1/owner', tags=['local owner'])
SESSION_SECONDS = 15 * 60


def fingerprint():
    return hashlib.sha256((get_env('ADMIN_API_KEY', required=False) or '').encode()).hexdigest()


async def authenticate_owner_session(request: Request):
    token = request.cookies.get(OWNER_COOKIE)
    if not token or len(token) > 128:
        return
    try:
        raw = await request.app.state.redis.get('auth:owner:' + hashlib.sha256(token.encode()).hexdigest())
        record = json.loads(raw) if raw else {}
    except Exception:
        # Owner credentials fail closed; normal bearer authentication still works.
        return
    if record.get('fingerprint') == fingerprint() and record.get('scope') in {'people', 'admin'}:
        request.state.owner_scope = record['scope']


def require_owner(request: Request, scope=None):
    if not getattr(request.state, 'local_owner_access', False):
        raise HTTPException(403, 'Yönetim yalnızca bu PC üzerinden açılabilir.')
    owner_scope = getattr(request.state, 'owner_scope', None)
    if owner_scope not in ({scope} if scope else {'people', 'admin'}):
        raise HTTPException(401, 'Yönetici şifrenizi girin.')


class OwnerLogin(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    scope: Literal['people', 'admin']


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
                                          json.dumps({'scope': payload.scope, 'fingerprint': fingerprint()}), ex=SESSION_SECONDS)
    except Exception as exc:
        raise HTTPException(503, 'Yönetim oturumu açılamıyor.') from exc
    response.set_cookie(OWNER_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
    response.headers['Cache-Control'] = 'no-store'
    return {'scope': payload.scope, 'expires_in': SESSION_SECONDS}


@router.post('/logout')
async def logout(request: Request, response: Response):
    token = request.cookies.get(OWNER_COOKIE)
    if token:
        try:
            await request.app.state.redis.delete('auth:owner:' + hashlib.sha256(token.encode()).hexdigest())
        except Exception as exc:
            raise HTTPException(503, 'Oturum kapatılamıyor.') from exc
    response.delete_cookie(OWNER_COOKIE)
    return {'ok': True}


@router.get('/people')
async def people(request: Request):
    require_owner(request)
    return {'users': await invitations.list_people(), 'invitations': await invitations.list_invitations(),
            'public_url': os.getenv('ORION_PUBLIC_URL', '')}


class InviteRequest(BaseModel):
    public_url: str = Field(max_length=500)
    label: str = Field(default='', max_length=80)
    expires_minutes: int = Field(default=1440, ge=5, le=10080)

    @field_validator('public_url')
    @classmethod
    def valid_public_url(cls, value):
        uri = urlsplit(value.strip())
        if (uri.scheme not in {'http', 'https'} or not uri.hostname or uri.username or uri.password
                or uri.query or uri.fragment or uri.path not in {'', '/'}):
            raise ValueError('Sunucu adresi http:// veya https:// ile başlamalı ve yalnızca adres/port içermeli.')
        if uri.hostname in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Telefonun erişebildiği PC adresini yazın; localhost telefondaki cihazı ifade eder.')
        return value.strip().rstrip('/')


@router.post('/invitations', status_code=201)
async def invite(payload: InviteRequest, request: Request, response: Response):
    require_owner(request)
    import qrcode
    import qrcode.image.svg
    identifier, token = str(uuid4()), secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=payload.expires_minutes)
    url = payload.public_url + '/dashboard/#invite=' + token
    qr = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=4)
    image = BytesIO()
    qr.save(image)
    await invitations.create_invitation(identifier, token, payload.label, expires)
    response.headers['Cache-Control'] = 'no-store'
    return {'id': identifier, 'url': url, 'qr_svg': image.getvalue().decode(), 'expires_at': expires}


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
