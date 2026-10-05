"""Loopback-only launcher, chat client and mounted owner panel."""
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import FileResponse, JSONResponse, RedirectResponse

from orion.api.access_boundary import is_local
from orion.api.local_peer import PeerClient


class OwnerShortcut:
    def __init__(self, app, panel_app=None, directory=None):
        self.app = app
        self.panel_app = panel_app
        self.peers = PeerClient(directory)

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        if not is_local(scope):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            try:
                hostname = urlsplit('http://' + dict(scope['headers']).get(b'host', b'').decode('latin1')).hostname
            except ValueError:
                hostname = None
            removed = hostname and (hostname.endswith('.local') or hostname in {'orionpanel', 'orionpanel.localhost'})
            return await JSONResponse({'detail': 'Yerel giriş için localhost kullanın.'},
                                      status_code=404 if removed else 403)(scope, receive, send)
        path = scope['path']
        if scope['type'] == 'http' and scope['method'] in {'GET', 'HEAD'} and path in {'/manage.', '/manage./'}:
            return await RedirectResponse('/manage', status_code=307)(scope, receive, send)
        if scope['type'] == 'http' and path.rstrip('/') == '/orionrouter' and scope['method'] in {'GET', 'HEAD'}:
            from services.shared.environment import get_env
            port = int(get_env('ORION_ROUTER_LOCAL_PORT', default='20128', required=False))
            return await RedirectResponse(f'http://127.0.0.1:{port}/dashboard', status_code=307)(scope, receive, send)
        from orion.api.access_boundary import management_api, USER_SETTINGS, OWNER_COOKIE
        headers = dict(scope.get('headers', []))
        account_credentials = headers.get(b'authorization', b'').lower().startswith(b'bearer ')
        owner_credentials = b'x-admin-key' in headers or (not account_credentials and OWNER_COOKIE in headers.get(b'cookie', b'').decode('latin1'))
        account_browser = path == '/api/v1/browser' or path.startswith('/api/v1/browser/')
        private_management = management_api(path) and not account_browser and (path.rstrip('/') not in USER_SETTINGS or owner_credentials)
        if self.panel_app and (path == '/manage' or path.startswith(('/manage/', '/router', '/admin', '/owner-assets/')) or private_management):
            return await self.panel_app(scope, receive, send)
        if path == '/panel' or path.startswith('/panel/'):
            mounted = dict(scope, root_path=scope.get('root_path', '') + '/panel')
            if path == '/panel':
                mounted['path'] += '/'
                mounted['raw_path'] = b'/panel/'
            return await self.panel_app(mounted, receive, send)
        if path.startswith('/api/v1/peers/'):
            return await self.peers(scope, receive, send)
        if scope['type'] == 'http' and path == '/' and scope['method'] in {'GET', 'HEAD'}:
            page = Path(__file__).with_name('owner_ui') / 'local.html'
            return await FileResponse(page, headers={'Cache-Control': 'no-store',
                                                     'Referrer-Policy': 'no-referrer'})(scope, receive, send)
        return await self.app(scope, receive, send)
