"""HTTPS-only device boundary and owner pairing/device/Router management."""
import asyncio
import json
import os
import time
import base64
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from io import BytesIO
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse, HTMLResponse, StreamingResponse

from orion.api.owner_routes import require_owner
from orion.api.auth_routes import get_current_user
from orion.kernel.local_security import SecurityStore, tls_identity, local_address

mobile_router = APIRouter()
pairing_router = APIRouter()
owner_router = APIRouter(prefix='/api/v1/owner/secure', tags=['local security'])

PAIR_REQUEST_CHANNEL = 'orion:pairing:requests'
PAIR_CODE_TAKE = """
local record = redis.call('GET', KEYS[1])
if record then redis.call('DEL', KEYS[1]) end
return record
"""
PAIR_REQUEST_LIMIT = """
local peer = redis.call('INCR', KEYS[1])
if peer == 1 then redis.call('EXPIRE', KEYS[1], 120) end
local total = redis.call('INCR', KEYS[2])
if total == 1 then redis.call('EXPIRE', KEYS[2], 120) end
if peer > 60 or total > 200 then return 0 end
return 1
"""


class PairRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default='unknown', min_length=1, max_length=40)

    @field_validator('device_name', 'platform')
    @classmethod
    def clean_display_name(cls, value):
        import unicodedata
        cleaned = ''.join(c for c in value if not unicodedata.category(c).startswith('C')).strip()
        if not cleaned:
            raise ValueError('A visible device name is required')
        return cleaned


@pairing_router.post('/api/v1/pair/request')
async def request_pairing(payload: PairRequest, request: Request, response: Response):
    """An unpaired phone can notify the PC; this grants no authority or secrets."""
    from hashlib import sha256
    from orion.api.access_boundary import FORWARDED
    if (not getattr(request.app.state, 'mobile', False) or request.scope['scheme'] != 'https'
            or any(header in request.headers for header in FORWARDED)):
        raise HTTPException(403, 'Pairing notification requires direct mobile HTTPS')
    peer = request.client.host if request.client else 'unknown'
    peer_key = sha256(peer.encode()).hexdigest()
    redis = request.app.state.redis
    try:
        allowed = await redis.eval(PAIR_REQUEST_LIMIT, 2,
                                   'orion:pairing:limit:peer:' + peer_key,
                                   'orion:pairing:limit:global')
        if await redis.exists('orion:pairing:cooldown:' + peer_key):
            raise HTTPException(429, 'Biraz bekleyip yeniden dene.', headers={'Retry-After': '10'})
        if not allowed:
            raise HTTPException(429, 'Çok fazla eşleştirme isteği.', headers={'Retry-After': '120'})
        notice = {'request_id': str(uuid4()), 'device_name': payload.device_name,
                  'platform': payload.platform, 'expires_at': int(time.time()) + 120,
                  'hub_id': (await asyncio.to_thread(tls_identity))['id']}
        await redis.set('orion:pairing:request:' + notice['request_id'], json.dumps({**notice, 'peer_key': peer_key}), ex=120)
        await redis.publish(PAIR_REQUEST_CHANNEL, json.dumps(notice))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Eşleştirme bildirimi şu anda gönderilemiyor.') from error
    response.headers['Cache-Control'] = 'no-store'
    return {'ok': True, 'request_id': notice['request_id'], 'expires_at': notice['expires_at']}




@pairing_router.post('/api/v1/pair/requests/{request_id}/reject')
async def reject_pairing_request(request_id: str, request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.access_boundary import is_desktop_peer
    if not is_desktop_peer(request.scope):
        raise HTTPException(403, 'Only this PC can dismiss a request')
    redis = request.app.state.redis
    raw = await redis.get('orion:pairing:request:' + request_id)
    if not raw:
        return {'ok': True}
    notice = json.loads(raw)
    # Multiple open PC clients count the same dismissal only once.
    if await redis.set('orion:pairing:rejected:' + request_id, '1', ex=120, nx=True):
        key = notice['peer_key']
        await redis.eval("""
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 120) end
if count >= 3 then
    redis.call('SET', KEYS[2], '1', 'EX', 10)
    redis.call('DEL', KEYS[1])
end
return 1
""", 2, 'orion:pairing:reject-count:' + key, 'orion:pairing:cooldown:' + key)
    return {'ok': True}


@pairing_router.get('/api/v1/pair/capabilities')
async def pairing_capabilities(request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.access_boundary import is_desktop_peer
    local = is_desktop_peer(request.scope)
    return JSONResponse({'can_manage_local': local, 'can_create_pairing': local or request.scope.get('scheme') == 'https',
                         'management_url': f'http://127.0.0.1:{int(os.getenv("ORION_LOCAL_PORT", "8910"))}/panel' if local else None},
                        headers={'Cache-Control': 'no-store'})


@pairing_router.get('/api/v1/pair/requests/stream')
async def pairing_request_stream(request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.access_boundary import is_desktop_peer
    if not is_desktop_peer(request.scope):
        raise HTTPException(403, 'Pairing notifications are available only on this PC')
    pubsub = request.app.state.redis.pubsub()

    async def events():
        try:
            await pubsub.subscribe(PAIR_REQUEST_CHANNEL)
            yield ': connected\n\n'
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message:
                    raw = message['data']
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    notice = json.loads(raw)
                    if notice.get('expires_at', 0) > time.time():
                        event = notice.get('event', 'pairing_request')
                        if event in {'pairing_request', 'pairing_completed'}:
                            yield 'event: ' + event + '\ndata: ' + json.dumps(notice, separators=(',', ':')) + '\n\n'
                else:
                    yield ': heartbeat\n\n'
        finally:
            await pubsub.unsubscribe(PAIR_REQUEST_CHANNEL)
            await pubsub.aclose()

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@owner_router.get('/password-info')
async def password_info(request: Request):
    if not getattr(request.state, 'local_owner_access', False):
        raise HTTPException(403, 'Local management required')
    from orion.api.security import valid_admin_key
    default = 'orion' if await asyncio.to_thread(valid_admin_key, 'orion') else None
    return JSONResponse({'default_password': default}, headers={'Cache-Control': 'no-store'})


class ChangeOwnerPassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=4, max_length=72)


@owner_router.post('/password')
async def change_owner_password(payload: ChangeOwnerPassword, request: Request):
    require_owner(request, 'admin')
    from orion.api.security import valid_admin_key, hash_password
    from orion.kernel.local_security import atomic_write, security_dir, identity_lock
    from orion.api.login_limits import limit_attempts
    await limit_attempts(request, 'owner-password', request.client.host, 5)
    if not valid_admin_key(payload.current_password):
        raise HTTPException(401, 'Mevcut yönetici şifresi yanlış.')
    if len(payload.new_password.encode('utf-8')) > 72:
        raise HTTPException(422, 'Şifre en fazla 72 bayt olabilir.')
    hashed = await asyncio.to_thread(hash_password, payload.new_password)
    directory = security_dir()
    with identity_lock(directory):
        atomic_write(directory / 'owner-password.hash', hashed.encode('ascii'))
    return JSONResponse({'ok': True, 'reauthenticate': True}, headers={'Cache-Control': 'no-store'})


@owner_router.get('/addresses')
async def connection_addresses(request: Request):
    require_owner(request)
    from orion.api.owner_routes import invitation_address
    local_port = int(os.getenv('ORION_LOCAL_PORT', '8910'))
    local_url = 'http://localhost/hub' if request.url.port in {None, 80} else f'http://localhost:{local_port}/hub'
    return {'local_browser_url': local_url,
            'lan_browser_url': invitation_address() + '/'}


class Claim(BaseModel):
    code: str = Field(min_length=8, max_length=8, pattern=r'^[A-Z2-9]{8}$')
    device_name: str = Field(min_length=1, max_length=120)
    platform: str | None = Field(default=None, max_length=40)
    device_id: str | None = Field(default=None, max_length=128)


class LocalClaim(BaseModel):
    local_token: str = Field(min_length=32, max_length=128)
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(min_length=1, max_length=40)


@mobile_router.post('/api/v1/pair/local')
async def local_claim(payload: LocalClaim, request: Request, response: Response):
    import ipaddress
    from orion.kernel.local_installation import local_token_path
    from orion.api.access_boundary import FORWARDED
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
        saved = local_token_path().read_text(encoding='ascii').strip()
    except (OSError, ValueError, AttributeError):
        raise HTTPException(403, 'Local installation proof unavailable') from None
    if (not getattr(request.app.state, 'mobile', False) or request.scope.get('scheme') != 'https'
            or not local or request.headers.get('origin')
            or any(header in request.headers for header in FORWARDED)
            or not secrets.compare_digest(saved, payload.local_token)):
        raise HTTPException(403, 'Local installation proof required')
    store = SecurityStore()
    code, _ = store.create_pairing()
    try:
        device, token = store.claim(code, payload.device_name, payload.platform, request.client.host)
    except PermissionError as error:
        raise HTTPException(429, str(error), headers={'Retry-After': '120'}) from error
    hub = tls_identity()
    response.headers['Cache-Control'] = 'no-store'
    return {'device_id':device, 'device_token':token, 'hub_id':hub['id'], 'hub_name':hub['name'], 'name':hub['name'], 'fp':hub['fp']}


@mobile_router.get('/api/v1/tls/identity')
async def identity():
    return {key: value for key, value in tls_identity().items() if key in {'v', 'id', 'name', 'fp'}}


@mobile_router.post('/api/v1/pair/claim')
async def claim(payload: Claim, request: Request):
    if not getattr(request.app.state, 'mobile', False) or request.scope['scheme'] != 'https':
        raise HTTPException(403, 'Pairing requires the mobile HTTPS listener')
    try:
        device_id, token = await asyncio.to_thread(SecurityStore().claim, payload.code, payload.device_name, payload.platform, request.client.host)
    except PermissionError as error:
        raise HTTPException(429, str(error), headers={'Retry-After': '120'}) from error
    except ValueError as error:
        raise HTTPException(403, 'pair_code_invalid') from error
    hub = tls_identity()
    # Completion is advisory: successful durable pairing must not fail on a Redis outage.
    try:
        from hashlib import sha256
        # Windows installations can run Redis 5, which has no GETDEL. Lua
        # preserves the atomic consume on every supported Redis version.
        record = await request.app.state.redis.eval(PAIR_CODE_TAKE, 1, 'orion:pairing:code:' + sha256(payload.code.encode()).hexdigest())
        if isinstance(record, bytes):
            record = record.decode('ascii')
        metadata = json.loads(record) if record and record.startswith('{') else {'pairing_id': record}
        pairing_id = metadata.get('pairing_id')
        request_id = metadata.get('request_id')
        if pairing_id:
            if request_id:
                await request.app.state.redis.set('orion:pairing:request-completed:' + request_id, '1', ex=120)
            await request.app.state.redis.set('orion:pairing:completed:' + pairing_id, '1', ex=120)
            await request.app.state.redis.publish(PAIR_REQUEST_CHANNEL, json.dumps({
                'event': 'pairing_completed', 'pairing_id': pairing_id, 'request_id': request_id,
                'hub_id': hub['id'], 'expires_at': int(time.time()) + 120}))
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Pairing completion notification failed')
    return {'device_id': device_id, 'device_token': token, 'hub_id': hub['id'], 'hub_name': hub['name'], 'name': hub['name'], 'fp': hub['fp']}


@mobile_router.delete('/api/v1/devices/self')
async def revoke_self(request: Request):
    # The device boundary supplies this identity; callers cannot choose another.
    device_id = getattr(request.state, 'device_id', None)
    if not device_id:
        raise HTTPException(401, 'Paired device required')
    SecurityStore().revoke(device_id)
    return {'ok': True}


class CreatePairing(BaseModel):
    request_id: str | None = Field(default=None, max_length=36)


@owner_router.post('/pairing')
async def create_pairing(request: Request, payload: CreatePairing | None = None):
    require_owner(request)
    return await build_pairing(request, payload.request_id if payload else None)


class RemotePairing(BaseModel):
    password: str = Field(min_length=1, max_length=512)


@pairing_router.post('/api/v1/pair/create')
async def create_remote_pairing(payload: RemotePairing, request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.access_boundary import FORWARDED
    from orion.api.login_limits import limit_attempts
    from orion.api.security import valid_admin_key
    if request.scope.get('scheme') != 'https' or any(header in request.headers for header in FORWARDED):
        raise HTTPException(403, 'Uzaktan QR oluşturmak için doğrudan HTTPS bağlantısı gerekli.')
    await limit_attempts(request, 'owner-pair-account', current_user, 5)
    await limit_attempts(request, 'owner', request.client.host if request.client else 'unknown', 5)
    await limit_attempts(request, 'owner-pair-global', 'all', 20)
    if not await asyncio.to_thread(valid_admin_key, payload.password):
        raise HTTPException(403, 'Yönetici şifresi yanlış.')
    return JSONResponse(await build_pairing(request), headers={'Cache-Control': 'no-store'})


async def build_pairing(request: Request, request_id: str | None = None):
    if request_id and not await request.app.state.redis.exists('orion:pairing:request:' + request_id):
        raise HTTPException(410, 'Eşleştirme isteğinin süresi doldu.')
    hub = tls_identity()
    host = local_address()
    host = '[' + host + ']' if ':' in host else host
    from orion.api import invitations
    invitation = secrets.token_urlsafe(32)
    await invitations.create_invitation(str(uuid4()), invitation, 'Telefon bağlantısı · isteğe bağlı yeni hesap',
                                        datetime.now(timezone.utc) + timedelta(minutes=15))
    code, expires = SecurityStore().create_pairing()
    pairing_id = str(uuid4())
    from hashlib import sha256
    await request.app.state.redis.set('orion:pairing:code:' + sha256(code.encode()).hexdigest(), json.dumps({'pairing_id': pairing_id, 'request_id': request_id}), ex=120)
    payload = {'v': 1, 'id': hub['id'], 'url': f'https://{host}:{int(os.getenv("ORION_MOBILE_PORT", "8443"))}',
               'fp': hub['fp'], 'code': code, 'name': hub['name']}
    if invitation:
        payload['invitation_token'] = invitation
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    app_link = 'orion://pair?data=' + encoded
    from orion.api.owner_routes import invitation_address
    link = invitation_address() + '/connect#data=' + encoded
    import qrcode
    import qrcode.image.svg
    output = BytesIO()
    qrcode.make(app_link, image_factory=qrcode.image.svg.SvgPathImage).save(output)
    png = BytesIO()
    qrcode.make(app_link).save(png, format='PNG')
    return {'qr': payload, 'link': link, 'app_link': app_link, 'request_id': request_id, 'qr_svg': output.getvalue().decode(),
            'qr_png': base64.b64encode(png.getvalue()).decode('ascii'), 'pairing_id': pairing_id, 'expires_at': expires, 'expires_in': 120}


@owner_router.get('/pairing/events')
async def owner_pairing_events(pairing_id: str, request: Request, request_id: str | None = None):
    require_owner(request)
    try:
        from uuid import UUID
        UUID(pairing_id)
        if request_id:
            UUID(request_id)
    except ValueError:
        raise HTTPException(422, 'Invalid pairing id') from None
    if request_id:
        raw = await request.app.state.redis.get('orion:pairing:request:' + request_id)
        if not raw:
            raise HTTPException(410, 'Eşleştirme isteğinin süresi doldu.')
    pubsub = request.app.state.redis.pubsub()
    async def events():
        try:
            await pubsub.subscribe(PAIR_REQUEST_CHANNEL)
            # Subscribe first, then check the durable short-lived result: a fast
            # camera can finish before this browser's EventSource is ready.
            if (await request.app.state.redis.exists('orion:pairing:completed:' + pairing_id)
                    or (request_id and await request.app.state.redis.exists('orion:pairing:request-completed:' + request_id))):
                yield 'event: pairing_completed\ndata: {"ok":true}\n\n'
                return
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message:
                    notice = json.loads(message['data'])
                    if notice.get('event') == 'pairing_completed' and (notice.get('pairing_id') == pairing_id or (request_id and notice.get('request_id') == request_id)):
                        yield 'event: pairing_completed\ndata: {"ok":true}\n\n'
                        return
                else:
                    yield ': heartbeat\n\n'
        finally:
            await pubsub.unsubscribe(PAIR_REQUEST_CHANNEL)
            await pubsub.aclose()
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'})


@owner_router.get('/devices')
async def devices(request: Request):
    require_owner(request)
    return {'devices': SecurityStore().devices()}


@owner_router.delete('/devices/{device_id}')
async def revoke(device_id: str, request: Request):
    require_owner(request)
    if not SecurityStore().revoke(device_id):
        raise HTTPException(404, 'Device not found')
    return {'ok': True}


class RouterTrust(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    fp: str = Field(pattern=r'^[0-9a-f]{64}$')
    url: str = Field(min_length=1, max_length=500)


@owner_router.get('/routers')
async def routers(request: Request):
    require_owner(request, 'admin')
    return {'routers': SecurityStore().routers()}


@owner_router.post('/routers')
async def trust_router(payload: RouterTrust, request: Request):
    require_owner(request, 'admin')
    from orion.kernel.pinned_router import verify_router
    try:
        identity = await verify_router(payload.url, payload.id, payload.fp)
        SecurityStore().trust_router(identity, payload.url.rstrip('/'))
    except Exception as error:
        raise HTTPException(400, 'Router identity could not be verified: ' + str(error)) from error
    return {'ok': True, 'router': identity}


@owner_router.delete('/routers/{router_id}')
async def forget_router(router_id: str, request: Request):
    require_owner(request, 'admin')
    SecurityStore().remove_router(router_id)
    return {'ok': True}


class MobileBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        async def deny(status, detail, code=None):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'detail': detail, **({'code': code} if code else {})}, status_code=status)(scope, receive, send)
        if scope.get('scheme') not in {'https', 'wss'}:
            return await deny(403, 'Mobile authentication requires TLS')
        path = scope['path']
        if path == '/' and scope['type'] == 'http' and scope.get('method') in {'GET', 'HEAD'}:
            from html import escape
            from urllib.parse import urlsplit
            host = urlsplit('https://' + dict(scope.get('headers', [])).get(b'host', b'').decode('latin1')).hostname
            try:
                import ipaddress
                address = ipaddress.ip_address(host)
                if address.is_unspecified or address.is_multicast:
                    raise ValueError('Invalid browser address')
            except (ValueError, TypeError):
                host = local_address()
            host = '[' + host + ']' if ':' in host else host
            url = escape(f'https://{host}:{int(os.getenv("ORION_WEB_PORT", "8911"))}/', quote=True)
            return await HTMLResponse('<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Orion Hub</title><body><h1>Orion Hub</h1><p>Bu adres telefon uygulamasının güvenli bağlantısı içindir. Orion uygulamasında PC ekranındaki QR kodunu okut veya bağlantı linkini aç. Sonraki açılışlarda Hub hatırlanır.</p><p>Tarayıcıdan kullanmak için <a href="'+url+'">Hub giriş ekranını aç</a>.</p></body></html>', headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})(scope, receive, send)
        from orion.api.access_boundary import public_path
        if path not in {'/api/v1/pair/claim', '/api/v1/pair/local', '/api/v1/tls/identity', '/api/v1/devices/self'} and not public_path(path):
            return await deny(403, 'Endpoint not available on mobile listener')
        if path in {'/api/v1/pair/claim', '/api/v1/pair/local', '/api/v1/pair/request', '/api/v1/tls/identity', '/health'}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        # Device credentials never confer account or administrator authority.
        query = parse_qs(scope.get('query_string', b'').decode('latin1'), keep_blank_values=True)
        if b'x-admin-key' in headers or 'admin_key' in query:
            return await deny(403, 'Administrator credentials are private')
        raw = headers.get(b'x-device-token', b'').decode('latin1')
        def authenticate_device():
            # Schema/file initialization may wait on SQLite locks. Constructing
            # SecurityStore before to_thread would still block every HTTP/SSE
            # request on the event loop. Reuse it for revocation reads as well.
            store = SecurityStore()
            return store, store.authenticate(raw)
        store, device = await asyncio.to_thread(authenticate_device)
        if not device:
            return await deny(401, 'Device is not paired or has been revoked', 'device_pairing_required')
        scope.setdefault('state', {})['device_id'] = device
        async def watch_revoke():
            while True:
                await asyncio.sleep(0.25)
                if not await asyncio.to_thread(store.active, device):
                    return
        serving = asyncio.create_task(self.app(scope, receive, send))
        watching = asyncio.create_task(watch_revoke())
        revoked = False
        try:
            done, _ = await asyncio.wait({serving, watching}, return_when=asyncio.FIRST_COMPLETED)
            if watching in done and not serving.done():
                # A failed DB read is not a revocation. Propagate it rather than
                # silently swallowing it as a normal stream cancellation.
                await watching
                revoked = True
                serving.cancel()
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008, 'reason': 'Device revoked'})
                # Cancelling the ASGI stream terminates SSE and other long bodies.
            await serving
        except asyncio.CancelledError:
            if not revoked:
                raise
        finally:
            serving.cancel(); watching.cancel()
            await asyncio.gather(serving, watching, return_exceptions=True)


class LegacyDeviceBoundary:
    """Reject injected device credentials and device-bound JWT on legacy apps."""
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        query = parse_qs(scope.get('query_string', b'').decode('latin1'))
        token = headers.get(b'authorization', b'').decode('latin1').removeprefix('Bearer ') or query.get('token', [''])[0]
        injected = b'x-device-token' in headers or 'device_token' in query or token.startswith('orion_device_')
        if token and not injected:
            import jwt
            from orion.api.security import JWT_SECRET_KEY, ALGORITHM
            try:
                injected = bool(jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM]).get('device_id'))
            except jwt.PyJWTError:
                pass
        if injected:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'Device credentials require mobile HTTPS'}, status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)
