"""Private owner routes and a separate public entry point; HTTP and WS alike."""
import ipaddress
import os
from urllib.parse import urlsplit, parse_qs

from starlette.responses import JSONResponse

OWNER_COOKIE = 'orion_owner'


def owner_cookie_name(scope):
    name = OWNER_COOKIE + ('_local' if scope.get('scheme') == 'http' else '')
    return name + '_panel' if scope.get('root_path') else name
FORWARDED = {'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'cf-connecting-ip', 'x-real-ip'}
USER_SETTINGS = {'/api/v1/admin/settings', '/api/v1/admin/settings/schema'}
DESKTOP_MANAGEMENT = ('/api/v1/browser', '/api/v1/google-ai/session')


def management_api(path: str) -> bool:
    return (path.startswith(('/api/v1/owner/', '/api/v1/admin/'))
            or any(path == prefix or path.startswith(prefix + '/') for prefix in DESKTOP_MANAGEMENT)
            or (path.startswith('/api/v1/auth/users/') and path.rstrip('/').endswith('/approval')))


def private_path(path: str) -> bool:
    return (path == '/' or path == '/index.html' or path == '/dashboard' or path.startswith(('/dashboard/', '/manage', '/admin', '/router', '/owner-assets', '/api/v1/owner/'))
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
    if path in {'/', '/hub', '/hub/', '/favicon.ico', '/dashboard', '/connect'} or path.startswith(('/dashboard/', '/css/', '/js/')):
        return True
    if private_path(path):
        return False
    return (path in {'/health', '/api/v1/pair/request', '/api/v1/pair/create', '/api/v1/pair/capabilities', '/api/v1/pair/requests/stream', '/api/v1/discovery/self', '/api/v1/discovery/hubs', '/api/v1/discovery/connection', '/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/auth/me', '/api/v1/auth/invitations/preview'}
            or path.startswith('/api/v1/pair/requests/')
            or path.rstrip('/') in USER_SETTINGS
            or path == '/dashboard' or path.startswith('/dashboard/')
            or any(path == prefix or path.startswith(prefix + '/') for prefix in
                   ('/api/v1/chats', '/api/v1/chat', '/api/v1/tools', '/api/v1/models', '/api/v1/audio')))


def is_desktop_peer(scope) -> bool:
    """Only this computer's actual socket peer, including its LAN self-connection."""
    headers = dict(scope.get('headers', []))
    if any(name.encode() in headers for name in FORWARDED):
        return False
    try:
        peer = ipaddress.ip_address(scope['client'][0])
        host = urlsplit('http://' + headers.get(b'host', b'').decode('latin1')).hostname
        from orion.api.discovery import lan_addresses
        addresses = lan_addresses()
        allowed_hosts = {'localhost', '127.0.0.1', '::1', *addresses}
        if host and host.startswith('orion-'):
            from orion.api.hub_directory import local_identity
            from orion.api.discovery import canonical_hostname
            allowed_hosts.add(canonical_hostname(local_identity()['id']).rstrip('.'))
        if peer.is_loopback:
            return host in allowed_hosts
        server = ipaddress.ip_address(scope['server'][0]) if scope.get('server') else None
        return host in allowed_hosts and (
            server is not None and peer == server and str(peer) in addresses)
    except (KeyError, TypeError, ValueError):
        return False


LOCAL_OWNER_WEB_PATHS = {'/api/v1/owner/login', '/api/v1/owner/secure/pairing',
                         '/api/v1/owner/secure/password-info', '/api/v1/owner/secure/password'}


def local_owner_web_origin(scope, path):
    if path not in LOCAL_OWNER_WEB_PATHS or not is_local(scope) or scope.get('scheme') != 'http':
        return None
    headers = dict(scope.get('headers', []))
    origin = headers.get(b'origin', b'').decode('latin1')
    try:
        parsed = urlsplit(origin)
        host = urlsplit('http://' + headers.get(b'host', b'').decode('latin1')).hostname
        # Same loopback hostname preserves Strict HttpOnly cookies across local ports.
        if (parsed.scheme == 'http' and parsed.hostname == host and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
                and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                and headers.get(b'sec-fetch-site') != b'cross-site'):
            parsed.port  # Reject malformed ports.
            return origin
    except ValueError:
        pass
    return None


class AccessBoundary:
    def __init__(self, app, public=False):
        self.app, self.public = app, public

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        path = scope['path']
        root = scope.get('root_path', '').rstrip('/')
        if root and (path == root or path.startswith(root + '/')):
            path = path[len(root):] or '/'
        headers = dict(scope.get('headers', []))
        local = is_local(scope)
        web_origin = local_owner_web_origin(scope, path) if not self.public else None
        if web_origin and scope.get('method') == 'OPTIONS':
            if headers.get(b'access-control-request-method', b'').decode().upper() not in {'GET', 'POST'}:
                return await JSONResponse({'detail': 'Method not allowed'}, status_code=403)(scope, receive, send)
            return await JSONResponse({}, headers={'Access-Control-Allow-Origin': web_origin,
                'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Methods': 'GET, POST',
                'Access-Control-Allow-Headers': 'Content-Type', 'Vary': 'Origin'})(scope, receive, send)
        user_api_on_private = not self.public and path.startswith('/api/v1/') and not management_api(path)
        # The public listener never exposes owner routes, even through loopback tunnels.
        browser_path = path == '/api/v1/browser' or path.startswith('/api/v1/browser/')
        desktop_access = browser_path and is_desktop_peer(scope)
        denied = (user_api_on_private or (self.public and not public_path(path) and not desktop_access)
                  or (not self.public and path not in {'/health', '/ready'} and not local))
        # Administrator credentials must never turn a public chat request into global access.
        has_key = b'x-admin-key' in headers or 'admin_key' in parse_qs(scope.get('query_string', b'').decode('latin1'), keep_blank_values=True)
        if has_key and (self.public or not local):
            denied = True
        if denied:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            if self.public and local and not has_key and not private_path(path) and not management_api(path):
                detail = 'Bu adres bulunamadı.'
                if path.strip('/').isdigit():
                    detail += ' Port numarası için / yerine : kullanın; örneğin http://localhost:8910/manage.'
                return await JSONResponse({'detail': detail}, status_code=404)(scope, receive, send)
            detail = 'Bu bölüm yalnızca bu PC üzerinden kullanılabilir.'
            if user_api_on_private:
                from services.shared.environment import get_env
                port = get_env('ORION_PUBLIC_PORT', default='8910', required=False)
                detail = f'Bu port yalnızca yönetim içindir. Hub bağlantısını genel kullanım portuna ({port}) değiştirin.'
            return await JSONResponse({'detail': detail}, status_code=403)(scope, receive, send)
        if self.public and desktop_access:
            from starlette.requests import Request
            request = Request(scope)
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.url.netloc}'
            if (origin and origin != expected) or request.headers.get('sec-fetch-site') == 'cross-site':
                return await JSONResponse({'detail': 'Geçersiz istek kaynağı.'}, status_code=403)(scope, receive, send)
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
            if management_api(path) or private_path(path) or has_key or owner_cookie_name(scope) in request.cookies:
                origin = request.headers.get('origin')
                expected = f'{request.url.scheme}://{request.url.netloc}'
                if ((origin and origin != expected) and not web_origin) or request.headers.get('sec-fetch-site') == 'cross-site':
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
                if web_origin:
                    response_headers = [(key, value) for key, value in response_headers
                                        if key.lower() not in {b'access-control-allow-origin', b'access-control-allow-credentials'}]
                    response_headers.extend([(b'access-control-allow-origin', web_origin.encode('latin1')),
                                             (b'access-control-allow-credentials', b'true'), (b'vary', b'Origin')])
                if private_path(path) or path.startswith('/api/v1/auth/'):
                    response_headers = [(key, value) for key, value in response_headers if key.lower() != b'cache-control']
                    response_headers.append((b'cache-control', b'no-store'))
                message = {**message, 'headers': response_headers}
            await send(message)
        await self.app(scope, receive, secure_send)
