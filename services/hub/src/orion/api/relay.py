"""The Orion window's way to another Hub: this Hub pairs with it like a phone and relays the window.

    /api/v1/remote/hubs            paired Hubs (GET), forget one (DELETE /api/v1/remote/hubs/<id>)
    /api/v1/remote/discover        Hubs on the network (GET)
    /api/v1/remote/request         open the pairing QR on that Hub's PC (POST)
    /api/v1/remote/pair            pair with a link or scanned QR (POST)
    /remote/<hub id>/<path>        the window's requests to that Hub, HTTP and WebSocket

Only this computer's loopback reaches it (LocalTransportBoundary wraps it), and pages of other sites are
kept out: cross-site requests are refused and changes need a header no plain form or link can send.
The device token never leaves this Hub; the window's own account token passes through unchanged.
"""
import asyncio
import logging
import socket
import uuid

import aiohttp
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from orion.api.access_boundary import FORWARDED, local_browser_origin
from orion.kernel import remote_hubs

log = logging.getLogger(__name__)
REMOTE_API = '/api/v1/remote/'
REMOTE_PREFIX = '/remote/'
CHANGE_HEADER = 'x-orion-relay'
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
       'transfer-encoding', 'upgrade', 'host'}
# The window's browser context is not the remote Hub's business, and nothing may claim another identity.
DROPPED = HOP | FORWARDED | {'origin', 'referer', 'cookie', 'x-device-token', 'x-admin-key', CHANGE_HEADER,
                             'content-length'}
STREAM = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)


def relay_path(path):
    return path.startswith(REMOTE_API) or path.startswith(REMOTE_PREFIX)


def window_hubs(store=None):
    """The paired Hubs as the window's Hub list shows them: reached at /remote/<id> on this loopback."""
    try:
        hubs = (store or remote_hubs.RemoteHubStore()).hubs()
    except OSError:
        return []
    return [{'id': hub['id'], 'name': hub['name'], 'url': REMOTE_PREFIX + hub['id'], 'remote': True} for hub in hubs]


class RelayGateway:
    def __init__(self, app, store=None, directory=None):
        self.app = app
        self._store = store
        self._directory = directory
        self._sessions = {}

    @property
    def store(self):
        if self._store is None:
            self._store = remote_hubs.RemoteHubStore()
        return self._store

    @property
    def directory(self):
        if self._directory is None:
            from orion.api.hub_directory import HubDirectory
            # The phones' own advertisement (orion.api.mobile_server), which names no API path.
            self._directory = HubDirectory('_orion-mobile._tcp.local.', api_path=None)
        return self._directory

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'} or not relay_path(scope['path']):
            return await self.app(scope, receive, send)
        headers = {key.decode('latin1').lower(): value.decode('latin1') for key, value in scope.get('headers', [])}
        origin = headers.get('origin')
        # The window (127.0.0.1:8090) and this Hub (localhost:8910) are different sites to the browser, so a
        # cross-site request passes only with an Origin that is a page of this PC.
        if origin and not local_browser_origin(origin) or not origin and headers.get('sec-fetch-site') == 'cross-site':
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'Geçersiz istek kaynağı.'}, status_code=403)(scope, receive, send)
        cors = {'access-control-allow-origin': origin, 'vary': 'Origin'} if origin else {}
        if scope['type'] == 'http' and scope['method'] == 'OPTIONS':
            return await JSONResponse({}, headers={
                **cors, 'access-control-allow-methods': 'GET, POST, PUT, PATCH, DELETE',
                'access-control-allow-headers': headers.get('access-control-request-headers', ''),
                'access-control-max-age': '600'})(scope, receive, send)
        if scope['path'].startswith(REMOTE_API):
            request = Request(scope, receive)
            try:
                response = await self.manage(request, headers)
            except ValueError as error:
                response = JSONResponse({'detail': str(error)}, status_code=400)
            except remote_hubs.PairingRejected as error:
                response = JSONResponse({'detail': str(error)}, status_code=403)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.info('Remote Hub unreachable', exc_info=True)
                response = JSONResponse({'detail': 'Hub’a ulaşılamadı. Aynı ağda ve açık mı?'}, status_code=502)
            response.headers.update(cors)
            response.headers['cache-control'] = 'no-store'
            return await response(scope, receive, send)
        hub_id, _, rest = scope['path'][len(REMOTE_PREFIX):].partition('/')
        hub = self._hub(hub_id)
        if hub is None:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'Bu Hub ile eşleşme yok.', 'code': 'remote_hub_unknown'},
                                      status_code=404, headers=cors)(scope, receive, send)
        if scope['type'] == 'websocket':
            return await self.socket(WebSocket(scope, receive, send), hub, '/' + rest)
        response = await self.forward(Request(scope, receive), hub, '/' + rest, cors)
        return await response(scope, receive, send)

    def _hub(self, hub_id):
        try:
            return self.store.get(str(uuid.UUID(hub_id)))
        except ValueError:
            return None

    async def manage(self, request, headers):
        path = request.url.path[len(REMOTE_API):].rstrip('/')
        if request.method != 'GET' and headers.get(CHANGE_HEADER) != '1':
            return JSONResponse({'detail': 'Geçersiz istek kaynağı.'}, status_code=403)
        if path == 'hubs' and request.method == 'GET':
            return JSONResponse({'hubs': [self._public(hub) for hub in self.store.hubs()]})
        if path.startswith('hubs/') and request.method == 'DELETE':
            hub = self._hub(path[len('hubs/'):])
            if hub is None:
                return JSONResponse({'detail': 'Bu Hub ile eşleşme yok.'}, status_code=404)
            await self._forget(hub)
            return JSONResponse({'ok': True})
        if path == 'discover' and request.method == 'GET':
            from orion.api.hub_directory import local_identity
            own = (await asyncio.to_thread(local_identity))['id']
            paired = {hub['id'] for hub in self.store.hubs()}
            found = [{'id': hub['id'], 'name': hub['name'], 'paired': hub['id'] in paired}
                     for hub in await self.directory.list() if hub['id'] != own]
            return JSONResponse({'hubs': found})
        if path == 'request' and request.method == 'POST':
            body = await request.json()
            wanted = body.get('id') if isinstance(body, dict) else None
            # Only a Hub just seen on the network: the window never names an address to call.
            hub = next((hub for hub in await self.directory.list() if hub['id'] == wanted and hub.get('fp')), None)
            if hub is None:
                return JSONResponse({'detail': 'Hub ağda bulunamadı. Yeniden ara.'}, status_code=404)
            data = await remote_hubs.request_pairing(hub['url'].rstrip('/'), hub['fp'], socket.gethostname())
            return JSONResponse({'ok': True, 'expires_at': data.get('expires_at')})
        if path == 'pair' and request.method == 'POST':
            body = await request.json()
            link = remote_hubs.parse_link(body.get('link') if isinstance(body, dict) else None)
            identity = await remote_hubs.resolve(link['url'], link['fp_prefix'])
            from orion.api.hub_directory import local_identity
            if identity['id'] == (await asyncio.to_thread(local_identity))['id']:
                raise ValueError('Bu bağlantı bu bilgisayarın kendi Hub’ına ait.')
            previous = self.store.get(identity['id'])
            if previous and previous['fp'] != identity['fp']:
                raise ValueError('Bu Hub’ın kimliği değişmiş. Eski eşleşmeyi kaldırıp yeniden bağlan.')
            hub = await remote_hubs.claim(link['url'], identity, link['code'], socket.gethostname())
            if previous:
                await self._forget(previous)
            self.store.save(hub)
            return JSONResponse(self._public(hub))
        return JSONResponse({'detail': 'Bu adres bulunamadı.'}, status_code=404)

    @staticmethod
    def _public(hub):
        return {'id': hub['id'], 'name': hub['name'], 'address': hub['url'].split('://', 1)[-1], 'fp': hub['fp']}

    async def _forget(self, hub):
        try:
            async with remote_hubs.session(hub['fp']) as client:
                await client.delete(hub['url'] + '/api/v1/devices/self', headers={'X-Device-Token': hub['device_token']})
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass  # The main Hub's PC can still remove the device from its list.
        self.store.remove(hub['id'])
        old = self._sessions.pop(hub['id'], None)
        if old:
            await old[1].close()

    def _session(self, hub):
        cached = self._sessions.get(hub['id'])
        if cached and cached[0] == hub['fp'] and not cached[1].closed:
            return cached[1]
        client = remote_hubs.session(hub['fp'], timeout=STREAM, auto_decompress=False)
        self._sessions[hub['id']] = (hub['fp'], client)
        return client

    async def _relocate(self, hub):
        """The main Hub's address changed: find it again by id; the certificate pin still decides."""
        for found in await self.directory.list():
            if found['id'] == hub['id'] and found['url'].rstrip('/') != hub['url']:
                self.store.move(hub['id'], found['url'].rstrip('/'))
                return {**hub, 'url': found['url'].rstrip('/')}
        return None

    @staticmethod
    def _outgoing(request_headers, hub):
        headers = {key: value for key, value in request_headers if key.lower() not in DROPPED}
        headers['X-Device-Token'] = hub['device_token']
        return headers

    async def forward(self, request, hub, path, cors):
        headers = self._outgoing(request.headers.items(), hub)
        body = await request.body()
        target = path + ('?' + request.url.query if request.url.query else '')

        def send(hub):
            return self._session(hub).request(request.method, hub['url'] + target, headers=headers, data=body,
                                              allow_redirects=False)
        unreachable = JSONResponse({'detail': f'{hub["name"]} Hub’ına ulaşılamadı. Açık ve aynı ağda mı?',
                                    'code': 'remote_hub_unreachable'}, status_code=502, headers=cors)
        try:
            try:
                upstream = await send(hub)
            except aiohttp.ClientConnectorError:
                # Nothing reached the Hub yet, so even a POST is safe to send again at its new address.
                moved = await self._relocate(hub)
                if moved is None:
                    return unreachable
                upstream = await send(moved)
        except aiohttp.ServerFingerprintMismatch:
            return JSONResponse({'detail': f'{hub["name"]} Hub’ının sertifikası değişmiş; bağlantı kesildi.',
                                 'code': 'remote_hub_identity'}, status_code=502, headers=cors)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return unreachable
        response_headers = {key: value for key, value in upstream.headers.items()
                            if key.lower() not in HOP and key.lower() != 'set-cookie'
                            and not key.lower().startswith('access-control-')}
        response_headers.update(cors)

        async def body_chunks():
            try:
                async for chunk in upstream.content.iter_any():
                    yield chunk
            finally:
                upstream.release()
        return StreamingResponse(body_chunks(), status_code=upstream.status, headers=response_headers)

    async def socket(self, websocket, hub, path):
        query = dict(websocket.query_params)
        headers = self._outgoing(websocket.headers.items(), hub)
        headers = {key: value for key, value in headers.items() if not key.lower().startswith('sec-websocket-')}
        # A browser cannot set headers on a WebSocket; the window sends its account token in the query.
        token = query.pop('token', None)
        if token:
            headers['Authorization'] = 'Bearer ' + token
        protocols = [value.strip() for value in websocket.headers.get('sec-websocket-protocol', '').split(',') if value.strip()]
        url = 'wss://' + hub['url'].split('://', 1)[1] + path
        try:
            upstream = await self._session(hub).ws_connect(url, params=query, headers=headers, protocols=protocols,
                                                           max_msg_size=16 * 1024 * 1024)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return await websocket.close(1011)
        await websocket.accept(subprotocol=upstream.protocol)

        async def window_to_hub():
            try:
                while True:
                    message = await websocket.receive()
                    if message['type'] == 'websocket.disconnect':
                        break
                    if message.get('bytes') is not None:
                        await upstream.send_bytes(message['bytes'])
                    elif message.get('text') is not None:
                        await upstream.send_str(message['text'])
            except (WebSocketDisconnect, RuntimeError):
                pass

        async def hub_to_window():
            async for message in upstream:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await websocket.send_text(message.data)
                elif message.type == aiohttp.WSMsgType.BINARY:
                    await websocket.send_bytes(message.data)
                else:
                    break

        tasks = [asyncio.create_task(window_to_hub()), asyncio.create_task(hub_to_window())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await upstream.close()
            try:
                await websocket.close(upstream.close_code or 1000)
            except RuntimeError:
                pass

    async def close(self):
        for _, client in self._sessions.values():
            await client.close()
        self._sessions.clear()
