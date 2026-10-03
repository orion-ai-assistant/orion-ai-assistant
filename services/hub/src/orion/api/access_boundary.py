"""Private owner routes and a separate public entry point; HTTP and WS alike."""
import ipaddress
import os
from urllib.parse import urlsplit, parse_qs

from starlette.responses import JSONResponse

OWNER_COOKIE = 'orion_owner'
FORWARDED = {'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'cf-connecting-ip', 'x-real-ip'}
USER_SETTINGS = {'/api/v1/admin/settings', '/api/v1/admin/settings/schema'}
DESKTOP_MANAGEMENT = ('/api/v1/browser', '/api/v1/google-ai/session')


def management_api(path: str) -> bool:
    return (path.startswith(('/api/v1/owner/', '/api/v1/admin/'))
            or any(path == prefix or path.startswith(prefix + '/') for prefix in DESKTOP_MANAGEMENT)
            or (path.startswith('/api/v1/auth/users/') and path.rstrip('/').endswith('/approval')))


def private_path(path: str) -> bool:
    return (path == '/' or path == '/index.html' or path == '/dashboard' or path.startswith(('/dashboard/', '/manage', '/admin', '/owner-assets', '/api/v1/owner/'))
            or (path.startswith('/api/v1/admin/') and path.rstrip('/') not in USER_SETTINGS)
            or (path.startswith('/api/v1/auth/users/') and path.rstrip('/').endswith('/approval')))


def is_local(scope) -> bool:
    headers = dict(scope.get('headers', []))
    try:
        address = ipaddress.ip_address(scope['client'][0])
        peer = address.is_loopback
        # Compose publishes this listener on host 127.0.0.1 only; Docker NAT changes the peer.
        if os.getenv('ORION_PRIVATE_DOCKER_LISTENER') == 'true' and os.path.exists('/.dockerenv'):
            peer = peer or address.is_private
        host = urlsplit('http://' + headers.get(b'host', b'').decode('latin1')).hostname
        return peer and host in {'localhost', '127.0.0.1', '::1'} and not any(name.encode() in headers for name in FORWARDED)
    except (KeyError, TypeError, ValueError):
        return False


def public_path(path: str) -> bool:
    if path == '/' or path == '/dashboard' or path.startswith('/dashboard/'):
        return True
    if private_path(path):
        return False
    return (path in {'/health', '/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/auth/me', '/api/v1/auth/invitations/preview'}
            or path.rstrip('/') in USER_SETTINGS
            or path == '/dashboard' or path.startswith('/dashboard/')
            or any(path == prefix or path.startswith(prefix + '/') for prefix in
                   ('/api/v1/chats', '/api/v1/chat', '/api/v1/tools', '/api/v1/models', '/api/v1/audio')))


class AccessBoundary:
    def __init__(self, app, public=False):
        self.app, self.public = app, public

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        path = scope['path']
        headers = dict(scope.get('headers', []))
        local = is_local(scope)
        user_api_on_private = not self.public and path.startswith('/api/v1/') and not management_api(path)
        # The public listener never exposes owner routes, even through loopback tunnels.
        denied = (user_api_on_private or (self.public and not public_path(path))
                  or (not self.public and path not in {'/health', '/ready'} and not local))
        # Administrator credentials must never turn a public chat request into global access.
        has_key = b'x-admin-key' in headers or 'admin_key' in parse_qs(scope.get('query_string', b'').decode('latin1'), keep_blank_values=True)
        if has_key and (self.public or not local):
            denied = True
        if denied:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            detail = 'Bu bölüm yalnızca bu PC üzerinden kullanılabilir.'
            if user_api_on_private:
                from services.shared.environment import get_env
                port = get_env('ORION_PUBLIC_PORT', default='8910', required=False)
                detail = f'Bu port yalnızca yönetim içindir. Hub bağlantısını genel kullanım portuna ({port}) değiştirin.'
            return await JSONResponse({'detail': detail}, status_code=403)(scope, receive, send)
        scope.setdefault('state', {})['local_owner_access'] = local and not self.public
        if local and not self.public and scope['type'] == 'http':
            from starlette.requests import Request
            from orion.api.owner_routes import authenticate_owner_session
            request = Request(scope)
            if has_key:
                from orion.api.security import valid_admin_key
                from orion.api.login_limits import limit_attempts
                from fastapi import HTTPException
                provided = request.headers.get('X-Admin-Key') or request.query_params.get('admin_key')
                if not valid_admin_key(provided):
                    try:
                        await limit_attempts(request, 'owner', request.client.host, 5)
                    except HTTPException as error:
                        return await JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)(scope, receive, send)
            # Protect owner login and every owner API from cross-origin browser requests.
            if management_api(path) or private_path(path) or has_key or OWNER_COOKIE in request.cookies:
                origin = request.headers.get('origin')
                expected = str(request.base_url).rstrip('/')
                if (origin and origin != expected) or request.headers.get('sec-fetch-site') == 'cross-site':
                    return await JSONResponse({'detail': 'Geçersiz istek kaynağı.'}, status_code=403)(scope, receive, send)
            await authenticate_owner_session(request)
            # Shared settings paths are user-facing on the public listener, but
            # require administrator authority on the management listener.
            if path.rstrip('/') in USER_SETTINGS or any(path == prefix or path.startswith(prefix + '/') for prefix in DESKTOP_MANAGEMENT):
                from orion.api.security import valid_admin_key
                provided = request.headers.get('X-Admin-Key') or request.query_params.get('admin_key')
                if getattr(request.state, 'owner_scope', None) != 'admin' and not valid_admin_key(provided):
                    return await JSONResponse({'detail': 'Bu port yalnızca yönetim içindir; yönetici girişi gerekli.'}, status_code=401)(scope, receive, send)
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                response_headers = list(message.get('headers', []))
                response_headers.append((b'referrer-policy', b'no-referrer'))
                if private_path(path) or path.startswith('/api/v1/auth/'):
                    response_headers = [(key, value) for key, value in response_headers if key.lower() != b'cache-control']
                    response_headers.append((b'cache-control', b'no-store'))
                message = {**message, 'headers': response_headers}
            await send(message)
        await self.app(scope, receive, secure_send)
