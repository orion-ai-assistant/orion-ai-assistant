"""One API-owned Chrome connection shared by local Hub processes.

The private loopback RPC is authenticated with an ephemeral capability. It is
not mounted on the public Hub API and never enables CORS or accepts URL targets.
"""
import asyncio
import json
import os
import secrets
from contextlib import suppress

import aiohttp
from aiohttp import web

from .cdp import CDPError, CDPConnectionLost, CDPTimeout


def descriptor_path():
    from .manager import browser_root
    return browser_root() / 'bridge.json'


class BrowserBridge:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.connections = {}
        self.lock = asyncio.Lock()
        self.runner = None

    async def start(self):
        from .manager import file_lock
        self.lease = file_lock(descriptor_path().with_suffix('.lock'))
        self.lease.__enter__()
        try:
            app = web.Application(client_max_size=16 * 1024 * 1024)
            app.router.add_post('/rpc', self.handle)
            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            site = web.TCPSite(self.runner, '127.0.0.1', 0)
            await site.start()
            port = self.runner.addresses[0][1]
            path = descriptor_path()
            temporary = path.with_suffix('.tmp')
            with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
                                  0o600), 'w', encoding='utf-8') as output:
                json.dump({'port': port, 'token': self.token}, output)
            temporary.replace(path)
        except BaseException:
            if self.runner:
                await self.runner.cleanup()
            self.lease.__exit__(None, None, None)
            raise
        return self

    async def close(self):
        from .manager import _connections
        for connection in self.connections.values():
            await connection.close()
        self.connections.clear()
        for key, connection in list(_connections.items()):
            if key[0] is asyncio.get_running_loop() and connection.closed:
                _connections.pop(key, None)
        if self.runner:
            await self.runner.cleanup()
        with suppress(OSError):
            descriptor_path().unlink()
        self.lease.__exit__(None, None, None)

    async def handle(self, request):
        from .manager import discover_endpoint, get_direct_connection, BrowserUnavailable, browser_root
        if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self.token):
            raise web.HTTPUnauthorized()
        if request.headers.get('Origin'):
            raise web.HTTPForbidden()
        try:
            data = await request.json()
            endpoint = data['endpoint']
            operation = data['operation']
            if not isinstance(endpoint, str):
                raise ValueError('Invalid endpoint')
            connection = self.connections.get(endpoint)
            if operation == 'status':
                return web.json_response({'result': {'connected': bool(connection and not connection.closed)}})
            if operation == 'disconnect':
                async with self.lock:
                    if connection and not connection.closed:
                        await connection.close()
                    self.connections.pop(endpoint, None)
                return web.json_response({'result': {'disconnected': True}})
            if (browser_root() / '.stopping').exists():
                raise BrowserUnavailable('Orion Hub kapanıyor.')
            if operation == 'connect':
                # Reconnecting can prompt the user; only Settings asks for it.
                async with self.lock:
                    valid = [discover_endpoint(name) for name in ('chrome', 'edge', 'brave')]
                    if endpoint not in [entry[1] for entry in valid if entry]:
                        raise BrowserUnavailable('Tarayıcı bağlantısı değişti; lütfen yeniden bağlanın.')
                    connection = self.connections.get(endpoint)
                    if connection is None or connection.closed:
                        connection = await get_direct_connection(endpoint)
                        self.connections[endpoint] = connection
                return web.json_response({'result': {'connected': True}})
            if operation != 'send':
                raise ValueError('Invalid operation')
            if connection is None or connection.closed:
                raise BrowserUnavailable('Tarayıcı bağlantısı kapandı. Lütfen mevcut tarayıcınıza bağlanın.')
            method = data['method']
            if method == 'Browser.close':
                raise ValueError('Personal browser cannot be closed by Orion')
            timeout = min(max(float(data.get('timeout', 10)), .1), 60)
            result = await connection.send(method, data.get('params'),
                                           session_id=data.get('session_id'), timeout=timeout)
            return web.json_response({'result': result})
        except CDPError as exc:
            return web.json_response({'error': 'cdp', 'method': exc.method,
                                      'details': {'code': exc.code, 'message': str(exc), 'data': exc.data}})
        except (CDPConnectionLost, CDPTimeout) as exc:
            return web.json_response({'error': 'uncertain', 'message': str(exc)})
        except BrowserUnavailable as exc:
            return web.json_response({'error': 'unavailable', 'message': str(exc)})
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest()


class SharedConnection:
    """CDP-shaped RPC client. Closing a client never closes the shared socket."""
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.closed = False

    async def rpc(self, operation, **data):
        from .manager import BrowserUnavailable
        try:
            descriptor = json.loads(descriptor_path().read_text(encoding='utf-8'))
            port, token = descriptor['port'], descriptor['token']
            if type(port) is not int or not 0 < port < 65536 or not isinstance(token, str):
                raise ValueError('Invalid bridge descriptor')
            timeout = 65 if operation == 'connect' else data.get('timeout', 10) + 5
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout)) as http:
                async with http.post(f'http://127.0.0.1:{port}/rpc',
                    headers={'Authorization': 'Bearer ' + token},
                    json={'operation': operation, 'endpoint': self.endpoint, **data},
                    allow_redirects=False) as response:
                    response.raise_for_status()
                    result = await response.json()
        except (OSError, ValueError, KeyError, aiohttp.ClientError, TimeoutError) as exc:
            self.closed = True
            if operation == 'send':
                raise CDPConnectionLost('Browser bridge disconnected; command outcome unknown') from exc
            raise BrowserUnavailable('Orion tarayıcı bağlantı servisine ulaşılamadı. Hub’ı yeniden başlatın.') from exc
        if result.get('error') == 'cdp':
            raise CDPError(result['method'], result['details'])
        if result.get('error') == 'uncertain':
            raise CDPConnectionLost(result['message'])
        if result.get('error') == 'unavailable':
            raise BrowserUnavailable(result['message'])
        return result['result']

    async def send(self, method, params=None, *, session_id=None, timeout=10):
        return await self.rpc('send', method=method, params=params,
                              session_id=session_id, timeout=timeout)

    async def close(self):
        self.closed = True
