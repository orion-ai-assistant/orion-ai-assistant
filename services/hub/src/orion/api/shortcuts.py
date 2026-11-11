"""Loopback-only local entry (8910): chat pages open the Flutter app, the owner panel is mounted at /panel."""
from urllib.parse import urlsplit

from starlette.responses import JSONResponse, RedirectResponse

from orion.api.access_boundary import is_local

# Every browser entry to the chat, old bookmarks included, opens the Flutter app.
FLUTTER_PATHS = {'/', '/hub', '/hub/', '/dashboard', '/dashboard/'}


class OwnerShortcut:
    def __init__(self, app, panel_app=None):
        self.app = app
        self.panel_app = panel_app

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
        if scope['type'] == 'http' and scope['method'] in {'GET', 'HEAD'} and (path in FLUTTER_PATHS or path.startswith('/dashboard/')):
            from services.shared.environment import get_env
            from services.shared.flutter_launcher import flutter_url
            target = flutter_url(get_env) + '/'
            query = scope.get('query_string', b'').decode('latin1')
            if query:
                target += '?' + query
            return await RedirectResponse(target, status_code=307, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if scope['type'] == 'http' and scope['method'] in {'GET', 'HEAD'} and path in {'/manage.', '/manage./'}:
            return await RedirectResponse('/manage', status_code=307)(scope, receive, send)
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
        return await self.app(scope, receive, send)
