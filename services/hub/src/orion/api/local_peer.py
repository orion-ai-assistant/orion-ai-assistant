"""Local HTML client for discovered peers, never a general URL/admin proxy.

UUID matching prevents accidental misrouting; it is not TLS authentication.
Only the loopback launcher mounts this adapter. No peer HTML is served locally.
"""
import asyncio
import ipaddress
import json
import uuid
from urllib.parse import urlsplit

import aiohttp
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from orion.api.access_boundary import public_path
from orion.api.discovery import LAN_NETWORKS

MAX_BODY = 32 * 1024 * 1024
REQUEST_HEADERS = {'authorization', 'content-type', 'accept', 'accept-language'}
RESPONSE_HEADERS = {'content-type', 'retry-after', 'content-disposition'}


class PeerUnavailable(Exception):
    pass


class PeerClient:
    def __init__(self, directory):
        self.directory = directory

    async def endpoint(self, identity):
        records = await self.directory.list()
        matches = [record for record in records if record['id'] == identity]
        if len(matches) != 1 or matches[0].get('conflict'):
            raise PeerUnavailable('Kayıtlı Hub bulunamadı veya kimliği çakışıyor. Yenile ya da başka bir Hub seç.')
        uri = urlsplit(matches[0]['url'])
        try:
            address = ipaddress.ip_address(uri.hostname)
            valid = (uri.scheme == 'https' and not uri.username and not uri.password
                     and uri.path == '/' and not uri.query and not uri.fragment
                     and uri.port is not None and 1 <= uri.port <= 65535
                     and address.version == 4 and any(address in net for net in LAN_NETWORKS))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise PeerUnavailable('Hub adresi geçersiz.')
        return f'https://{uri.hostname}:{uri.port}'

    def session(self):
        # ws_connect otherwise follows redirects (potentially exposing its token).
        trace = aiohttp.TraceConfig()
        async def reject_redirect(session, context, params):
            raise PeerUnavailable('Hub başka bir adrese yönlendirdi; bağlantı durduruldu.')
        trace.on_request_redirect.append(reject_redirect)
        return aiohttp.ClientSession(
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), trace_configs=[trace],
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=3, sock_read=45))

    async def check_identity(self, session, base, identity):
        async with session.get(base + '/api/v1/discovery/self', allow_redirects=False,
                               timeout=aiohttp.ClientTimeout(total=4)) as response:
            if response.status != 200:
                raise PeerUnavailable('Hub kimlik bilgisi alınamadı.')
            raw = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > 16384:
                    raise PeerUnavailable('Hub kimlik yanıtı geçersiz.')
            try:
                data = json.loads(raw) if len(raw) <= 16384 else {}
            except (ValueError, UnicodeError):
                data = {}
            if not isinstance(data, dict) or data.get('id') != identity:
                raise PeerUnavailable('Bu adreste seçtiğin Hub yok. Bağlantı durduruldu.')

    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        request = Request(scope) if scope['type'] == 'http' else WebSocket(scope, receive, send)
        origin = request.headers.get('origin')
        scheme = 'https' if request.url.scheme in {'https', 'wss'} else 'http'
        if (origin and origin != f'{scheme}://{request.url.netloc}') or request.headers.get('sec-fetch-site') == 'cross-site':
            return await self.reject(scope, receive, send, 'Geçersiz istek kaynağı.', 403)
        try:
            identity, suffix = scope['path'][len('/api/v1/peers/'):].split('/', 1)
            identity = str(uuid.UUID(identity))
        except ValueError:
            return await self.reject(scope, receive, send, 'Hub kimliği geçersiz.', 400)
        path = '/api/v1/' + suffix
        # Never proxy owner, desktop/browser, discovery scanning, HTML or admin APIs.
        allowed = public_path(path) and (not path.startswith('/api/v1/discovery/') or path == '/api/v1/discovery/self')
        if ('\\' in path or '%' in path or any(part in {'.', '..', ''} for part in suffix.split('/'))
                or not allowed or b'x-admin-key' in headers or 'admin_key' in request.query_params):
            return await self.reject(scope, receive, send, 'Bu işlem uzak Hub bağlantısında kullanılamaz.', 403)
        if scope['type'] == 'websocket' and path != '/api/v1/audio/transcriptions/stream':
            return await self.reject(scope, receive, send, 'Geçersiz WebSocket yolu.', 403)
        if scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            return await self.reject(scope, receive, send, 'Geçersiz yöntem.', 405)
        session = None
        try:
            base = await self.endpoint(identity)
            session = self.session()
            # Check a claimed UUID before sending credentials to a changed IP.
            # This is an identifier comparison, not proof of a cryptographic key.
            await self.check_identity(session, base, identity)
            query = scope.get('query_string', b'').decode('ascii')
            url = base + path + ('?' + query if query else '')
            if scope['type'] == 'websocket':
                await self.websocket(session, url, request)
                return
            body = bytearray()
            async for chunk in Request(scope, receive).stream():
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    return await self.reject(scope, receive, send, 'İstek çok büyük.', 413)
            forwarded = {key: value for key, value in request.headers.items() if key.lower() in REQUEST_HEADERS}
            upstream = await session.request(scope['method'], url, data=bytes(body),
                                             headers=forwarded, allow_redirects=False)
            if 300 <= upstream.status < 400:
                upstream.close()
                raise PeerUnavailable('Hub başka bir adrese yönlendirdi; bağlantı durduruldu.')
        except (PeerUnavailable, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            if session:
                await session.close()
            detail = str(error) if isinstance(error, PeerUnavailable) else 'Hub’a bağlanılamadı. Yenile veya başka bir Hub seç.'
            return await self.reject(scope, receive, send, detail, 503)
        except BaseException:
            if session:
                await session.close()
            raise
        if scope['type'] == 'websocket':
            return
        async def stream():
            try:
                async for chunk in upstream.content.iter_any():
                    yield chunk
            finally:
                upstream.close()
                await session.close()
        response_headers = {key: value for key, value in upstream.headers.items() if key.lower() in RESPONSE_HEADERS}
        # The upstream must never install its cookies or executable HTML on localhost.
        response_headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "sandbox; default-src 'none'"})
        media_type = upstream.content_type
        if not (media_type in {'application/json', 'text/event-stream', 'application/octet-stream'}
                or media_type.startswith('audio/') or media_type.endswith('+json')):
            upstream.close()
            await session.close()
            return await self.reject(scope, receive, send, 'Hub beklenen API yanıtını vermedi.', 502)
        return await StreamingResponse(stream(), status_code=upstream.status,
                                       headers=response_headers)(scope, receive, send)

    async def websocket(self, session, url, downstream):
        try:
            async with session.ws_connect(url, max_msg_size=16 * 1024 * 1024) as upstream:
                await downstream.accept()
                async def to_peer():
                    while True:
                        data = await downstream.receive()
                        if data['type'] == 'websocket.disconnect':
                            return
                        if data.get('bytes') is not None:
                            await upstream.send_bytes(data['bytes'])
                        elif data.get('text') is not None:
                            await upstream.send_str(data['text'])
                async def to_browser():
                    async for message in upstream:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await downstream.send_text(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await downstream.send_bytes(message.data)
                        elif message.type == aiohttp.WSMsgType.ERROR:
                            return
                tasks = [asyncio.create_task(to_peer()), asyncio.create_task(to_browser())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if downstream.application_state.name == 'CONNECTED' and downstream.client_state.name == 'CONNECTED':
                        await downstream.close()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    async def reject(self, scope, receive, send, detail, status):
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        return await JSONResponse({'detail': detail}, status_code=status,
                                  headers={'Cache-Control': 'no-store'})(scope, receive, send)
