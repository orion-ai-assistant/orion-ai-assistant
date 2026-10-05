import asyncio
import unittest
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import httpx

from orion.api.local_peer import PeerClient, PeerUnavailable
from orion.api.shortcuts import OwnerShortcut

ID = '00000000-0000-4000-8000-000000000001'
PREFIX = '/api/v1/peers/' + ID


class PeerClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.claimed = ID
        self.identity_redirect = False
        async def identity(request):
            if self.identity_redirect:
                return web.HTTPFound('/capture')
            return web.json_response({'id': self.claimed, 'name': 'Other'})
        async def capture(request):
            self.seen.append(dict(request.headers))
            return web.json_response({'caught': True})
        async def api(request):
            self.seen.append({'path': request.path, 'headers': dict(request.headers),
                              'body': await request.read(), 'query': dict(request.query)})
            if request.path.endswith('/redirect'):
                return web.HTTPFound('/capture')
            if request.path.endswith('/html'):
                return web.Response(text='<script>alert(1)</script>', content_type='text/html')
            if request.path.endswith('/stream'):
                stream = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await stream.prepare(request)
                await stream.write(b': heartbeat\n\n')
                await stream.write(b'data: {"message":"ok"}\n\n')
                await stream.write_eof()
                return stream
            return web.json_response({'ok': True}, headers={'Set-Cookie': 'evil=1', 'Retry-After': '7'})
        async def audio(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    await ws.send_bytes(message.data)
                elif message.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_str(message.data)
            return ws
        app = web.Application()
        app.router.add_get('/api/v1/discovery/self', identity)
        app.router.add_get('/api/v1/audio/transcriptions/stream', audio)
        app.router.add_get('/capture', capture)
        app.router.add_route('*', '/api/v1/{path:.*}', api)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        self.directory = AsyncMock()
        self.directory.list.return_value = [{'id': ID, 'url': 'http://192.168.1.20:8910/'}]
        self.entry = OwnerShortcut(None, directory=self.directory)
        # Only network integration tests substitute our owned ephemeral loopback server.
        self.entry.peers.endpoint = AsyncMock(return_value=self.base)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.entry, client=('127.0.0.1', 1234)), base_url='http://localhost')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.runner.cleanup()

    async def test_bearer_and_body_are_forwarded_without_admin_cookies(self):
        response = await self.client.post(PREFIX + '/auth/login', json={'username': 'u', 'password': 'test-password'},
                                          headers={'Authorization': 'Bearer peer-token', 'Cookie': 'orion_owner_panel=secret', 'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.seen[0]['headers']['Authorization'], 'Bearer peer-token')
        self.assertNotIn('Cookie', self.seen[0]['headers'])
        self.assertIn(b'test-password', self.seen[0]['body'])
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertEqual(response.headers['retry-after'], '7')
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_uuid_mismatch_never_sends_a_password_or_bearer(self):
        self.claimed = 'another-device'
        response = await self.client.post(PREFIX + '/auth/login', json={'password': 'secret'}, headers={'Authorization': 'Bearer secret'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.seen, [])

    async def test_identity_redirect_is_not_followed(self):
        self.identity_redirect = True
        response = await self.client.post(PREFIX + '/auth/login', json={'password': 'secret'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.seen, [])

    async def test_missing_device_does_not_fall_back_to_another(self):
        self.entry.peers.endpoint = PeerClient(self.directory).endpoint
        self.directory.list.return_value = []
        self.assertEqual((await self.client.get(PREFIX + '/auth/me')).status_code, 503)
        self.assertEqual(self.seen, [])

    async def test_no_arbitrary_url_or_loopback_or_conflicting_identity(self):
        client = PeerClient(self.directory)
        for url in ['http://127.0.0.1:8909/', 'http://8.8.8.8:8910/', 'http://192.168.1.20:8910/owner',
                    'http://user:pass@192.168.1.20:8910/', 'http://192.168.1.20:8910/?url=evil', 'http://orion.local:8910/']:
            self.directory.list.return_value = [{'id': ID, 'url': url}]
            with self.assertRaises(PeerUnavailable):
                await client.endpoint(ID)
        self.directory.list.return_value = [{'id': ID, 'url': 'http://192.168.1.20:8910/', 'conflict': True}]
        with self.assertRaises(PeerUnavailable):
            await client.endpoint(ID)

    async def test_no_owner_browser_scan_or_cross_origin_proxy(self):
        for path in ['/owner/login', '/admin/chats', '/browser/open', '/discovery/hubs', '/chats/../owner/login']:
            self.assertEqual((await self.client.post(PREFIX + path, json={})).status_code, 403, path)
        for headers in [{'X-Admin-Key': 'secret'}, {'Origin': 'http://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'X-Forwarded-For': '127.0.0.1'}]:
            self.assertEqual((await self.client.post(PREFIX + '/auth/login', json={}, headers=headers)).status_code, 403)
        self.assertEqual((await self.client.get(PREFIX + '/auth/me?admin_key=secret')).status_code, 403)
        self.assertEqual(self.seen, [])

    async def test_remote_html_and_redirects_are_not_served_on_localhost(self):
        self.assertEqual((await self.client.get(PREFIX + '/chats/html')).status_code, 502)
        self.assertEqual((await self.client.get(PREFIX + '/chats/redirect')).status_code, 503)
        self.assertEqual(len(self.seen), 2)

    async def test_sse_content_and_query_reach_only_the_selected_peer(self):
        response = await self.client.get(PREFIX + '/chat/stream?token=peer-token')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'text/event-stream')
        self.assertEqual(response.content, b': heartbeat\n\ndata: {"message":"ok"}\n\n')
        self.assertEqual(self.seen[0]['query'], {'token': 'peer-token'})

    async def test_audio_websocket_transfers_binary_and_text_and_closes(self):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({'type': 'websocket.connect'})
        scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws',
                 'path': PREFIX + '/audio/transcriptions/stream', 'root_path': '',
                 'query_string': b'token=test-token', 'headers': [(b'host', b'localhost'), (b'origin', b'http://localhost')],
                 'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 80), 'subprotocols': []}
        task = asyncio.create_task(self.entry(scope, incoming.get, outgoing.put))
        try:
            self.assertEqual((await asyncio.wait_for(outgoing.get(), 3))['type'], 'websocket.accept')
            await incoming.put({'type': 'websocket.receive', 'bytes': b'pcm-audio'})
            self.assertEqual((await asyncio.wait_for(outgoing.get(), 3))['bytes'], b'pcm-audio')
            await incoming.put({'type': 'websocket.receive', 'text': 'control'})
            self.assertEqual((await asyncio.wait_for(outgoing.get(), 3))['text'], 'control')
            await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
            await asyncio.wait_for(task, 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
