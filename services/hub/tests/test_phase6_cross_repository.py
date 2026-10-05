"""Live Phase 6 interoperability; no production database or identity is touched.

Set ORION_ROUTER_REPO and ORION_FLUTTER_REPO to run the optional sibling checks.
Run with PYTHONPATH=services/hub/src;<Hub repository> and unittest.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import aiohttp
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
import uvicorn

from orion.api.mobile_security import LegacyDeviceBoundary, MobileBoundary, mobile_router
from orion.api.https_boundary import RequireTLS
from orion.kernel.local_security import SecurityStore, tls_identity
from orion.kernel.pinned_router import SPKIPin, PinnedWebSocket, router_session, verify_router


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FixtureServer(uvicorn.Server):
    def install_signal_handlers(self):
        pass

    def capture_signals(self):
        from contextlib import nullcontext
        return nullcontext()


class CrossRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='orion-phase6-')
        self.directory = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {'ORION_SECURITY_DIR': str(self.directory / 'hub')})
        self.env.start()
        self.store = SecurityStore()
        self.hub = tls_identity()
        self.servers = []
        self.requests = []
        self.http_entries = []

    async def asyncTearDown(self):
        for server, task in reversed(self.servers):
            server.should_exit = True
            await asyncio.wait_for(task, 10)
        self.env.stop()
        self.temporary.cleanup()

    async def serve(self, app, certificate=None, key=None, host='127.0.0.1'):
        config = uvicorn.Config(app, host=host, port=0, lifespan='off',
            ssl_certfile=certificate, ssl_keyfile=key, proxy_headers=False,
            log_level='critical', timeout_graceful_shutdown=1)
        server = FixtureServer(config)
        task = asyncio.create_task(server.serve())
        self.servers.append((server, task))
        for _ in range(500):
            if server.started:
                port = server.servers[0].sockets[0].getsockname()[1]
                return f'{"https" if certificate else "http"}://{host}:{port}'
            if task.done():
                await task
                self.fail('Fixture listener exited before startup')
            await asyncio.sleep(.01)
        self.fail('Fixture listener startup timed out')

    def mobile_app(self):
        app = FastAPI()
        app.state.mobile = True
        app.include_router(mobile_router)
        app.add_middleware(MobileBoundary)

        captured = self.http_entries
        class CaptureRequests:
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope['type'] in {'http', 'websocket'}:
                    captured.append(scope['path'])
                await self.app(scope, receive, send)
        app.add_middleware(CaptureRequests)

        @app.post('/api/v1/chats/probe')
        async def probe(request: Request):
            self.requests.append({'path': request.url.path, 'body': await request.body(),
                                  'device': request.state.device_id})
            return {'ok': True}

        @app.get('/api/v1/chat/stream')
        async def events():
            async def generate():
                yield 'data: connected\n\n'
                while True:
                    await asyncio.sleep(.05)
                    yield ': alive\n\n'
            return StreamingResponse(generate(), media_type='text/event-stream')

        @app.websocket('/api/v1/audio/probe')
        async def socket(websocket: WebSocket):
            await websocket.accept()
            while True:
                await websocket.send_text(await websocket.receive_text())
        return app

    async def mobile_url(self):
        return await self.serve(self.mobile_app(), self.hub['cert'], self.hub['key'])

    async def assert_plaintext_rejected(self, url):
        from urllib.parse import urlsplit
        address = urlsplit(url)
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        try:
            writer.write(b'GET /api/v1/tls/identity HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            await writer.drain()
            try:
                received = await asyncio.wait_for(reader.read(), 3)
            except (ConnectionResetError, ConnectionAbortedError):
                received = b''
            self.assertEqual(received, b'', 'TLS listener accepted a plaintext HTTP request')
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, ConnectionAbortedError):
                pass

    async def test_hub_https_rejects_plaintext_and_certificate_supports_browser_hostname(self):
        url = await self.mobile_url()
        await self.assert_plaintext_rejected(url)
        self.assertEqual(self.http_entries, [])
        context = ssl.create_default_context(cafile=self.hub['cert'])
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context)) as client:
            # Exact trusted test certificate plus SAN hostname validation; no bypass.
            async with client.get(url + '/api/v1/tls/identity') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['fp'], self.hub['fp'])

    async def paired_device(self):
        code, _ = self.store.create_pairing()
        return await asyncio.to_thread(self.store.claim, code, 'Integration device', 'test', 'fixture')

    async def claim(self, session, url, code):
        async with session.post(url + '/api/v1/pair/claim', json={
            'code': code, 'device_name': 'Flutter probe', 'platform': 'test',
            'device_id': 'untrusted-client-id'}, allow_redirects=False) as response:
            return response.status, await response.json()

    async def test_live_claim_race_invalid_code_and_token_hash(self):
        url = await self.mobile_url()
        code, _ = self.store.create_pairing()
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SPKIPin(self.hub['fp']))) as client:
            invalid, detail = await self.claim(client, url, 'ZZZZZZZZ')
            self.assertEqual(invalid, 403)
            self.assertEqual(detail['detail'], 'pair_code_invalid')
            results = await asyncio.gather(self.claim(client, url, code), self.claim(client, url, code))
            self.assertEqual(sorted(status for status, _ in results), [200, 403])
            success = next(body for status, body in results if status == 200)
            self.assertEqual(success['hub_id'], self.hub['id'])
            self.assertEqual(success['fp'], self.hub['fp'])
            self.assertNotEqual(success['device_id'], 'untrusted-client-id')
            self.assertEqual(self.store.authenticate(success['device_token']), success['device_id'])
            with self.store.connect() as database:
                stored = database.execute('SELECT token_hash FROM devices').fetchone()['token_hash']
            self.assertNotEqual(stored, success['device_token'])
            self.assertEqual(stored, SecurityStore.digest(success['device_token']))

    async def test_expired_claim_and_web_token_injection_with_spoofed_tls_headers(self):
        url = await self.mobile_url()
        expired, _ = self.store.create_pairing(ttl=-1)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SPKIPin(self.hub['fp']))) as client:
            self.assertEqual((await self.claim(client, url, expired))[0], 403)
        device_id, token = await self.paired_device()
        legacy = FastAPI()
        legacy.add_middleware(LegacyDeviceBoundary)
        legacy.add_middleware(RequireTLS)
        legacy.include_router(mobile_router)
        legacy.state.mobile = False
        legacy_url = await self.serve(legacy, self.hub['cert'], self.hub['key'])
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SPKIPin(self.hub['fp']))) as client:
            for headers in ({'X-Device-Token': token}, {'Authorization': 'Bearer ' + token}):
                headers.update({'X-Forwarded-Proto': 'https', 'Forwarded': 'proto=https'})
                async with client.get(legacy_url + '/api/v1/tls/identity', headers=headers) as response:
                    self.assertEqual(response.status, 401)
            code, _ = self.store.create_pairing()
            self.assertEqual((await self.claim(client, legacy_url, code))[0], 403)
        self.assertTrue(self.store.active(device_id))

    async def test_live_revoke_closes_sse_websocket_and_rejects_requests(self):
        url = await self.mobile_url()
        device_id, token = await self.paired_device()
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SPKIPin(self.hub['fp'])),
                                         headers={'X-Device-Token': token}) as client:
            stream = await client.get(url + '/api/v1/chat/stream')
            self.assertIn(b'connected', await stream.content.readline())
            socket = await client.ws_connect(url.replace('https:', 'wss:') + '/api/v1/audio/probe')
            await socket.send_str('connected')
            self.assertEqual((await socket.receive()).data, 'connected')
            self.store.revoke(device_id)
            closed = await asyncio.wait_for(socket.receive(), 3)
            self.assertIn(closed.type, {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED})
            async def read_closed():
                try:
                    await stream.read()
                except aiohttp.ClientPayloadError:
                    pass  # An interrupted response also proves the live stream ended.
            await asyncio.wait_for(read_closed(), 3)
            async with client.post(url + '/api/v1/chats/probe', json={'secret': 'fixture'}) as response:
                self.assertEqual(response.status, 401)
            self.assertEqual(self.requests, [])

    async def test_flutter_self_revoke_contract_invalidates_only_its_device(self):
        url = await self.mobile_url()
        device_id, token = await self.paired_device()
        other_id, other_token = await self.paired_device()
        status, output, errors = await self.dart_probe(url, self.hub['fp'], mode='DELETE',
            path='/api/v1/devices/self', body='null', token=token)
        self.assertEqual(status, 0, errors + output)
        self.assertFalse(self.store.active(device_id))
        self.assertIsNone(self.store.authenticate(token))
        self.assertEqual(self.store.authenticate(other_token), other_id)

    def router_identity(self, directory='router', identity=None, now=None):
        root = os.environ.get('ORION_ROUTER_REPO')
        if not root:
            self.skipTest('Set ORION_ROUTER_REPO to enable actual Router identity checks')
        module = load_module('phase6_router_identity', Path(root) / 'core' / 'tls_identity.py')
        return module.load_identity(self.directory / directory, identity or str(uuid.uuid4()), now=now)

    def router_app(self, identity):
        app = FastAPI()

        @app.get('/api/v1/tls/identity')
        async def who():
            self.requests.append('identity')
            return identity.payload('Router integration')

        @app.post('/v1/chat/completions')
        async def chat(request: Request):
            self.requests.append({'body': await request.body(), 'authorization': request.headers.get('authorization')})
            return {'ok': True}

        @app.websocket('/v1/audio/stream')
        async def socket(websocket: WebSocket):
            self.requests.append('websocket-upgrade')
            await websocket.accept()
            await websocket.send_text(await websocket.receive_text())
            await websocket.close()

        @app.get('/v1/stream')
        async def stream():
            async def generate():
                yield 'data: connected\n\n'
                while True:
                    await asyncio.sleep(.05)
                    yield ': alive\n\n'
            return StreamingResponse(generate(), media_type='text/event-stream')
        return app

    async def router_url(self, identity, host='127.0.0.1'):
        return await self.serve(self.router_app(identity), str(identity.certificate_path), str(identity.key_path), host)

    async def test_router_https_rejects_plaintext_and_validates_browser_hostname(self):
        identity = self.router_identity()
        url = await self.router_url(identity)
        await self.assert_plaintext_rejected(url)
        self.assertEqual(self.requests, [])
        context = ssl.create_default_context(cafile=str(identity.certificate_path))
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context)) as client:
            async with client.get(url + '/api/v1/tls/identity') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['fp'], identity.fp)

    async def test_hub_router_identity_and_rest_websocket_share_spki_policy(self):
        identity = self.router_identity()
        url = await self.router_url(identity)
        payload = await verify_router(url, identity.id, identity.fp)
        self.store.trust_router(payload, url)
        async with router_session() as client:
            async with client.post(url + '/v1/chat/completions', json={'secret': 'fixture'},
                                   headers={'Authorization': 'Bearer fixture-api-key'}) as response:
                self.assertEqual(response.status, 200)
        async with PinnedWebSocket(url.replace('https:', 'wss:') + '/v1/audio/stream') as socket:
            await socket.send('fixture-stream')
            self.assertEqual(await socket.recv(), 'fixture-stream')
        self.assertEqual(len(self.requests), 3)

    async def test_hub_router_wrong_pin_sends_no_request_headers_body_or_ws_upgrade(self):
        identity = self.router_identity()
        url = await self.router_url(identity)
        self.store.trust_router({'id': identity.id, 'name': 'Router', 'fp': '0' * 64}, url)
        async with router_session() as client:
            with self.assertRaises(aiohttp.ClientError):
                await client.post(url + '/v1/chat/completions', json={'password': 'fixture'},
                                  headers={'Authorization': 'Bearer fixture-secret'})
        with self.assertRaises(aiohttp.ClientError):
            async with PinnedWebSocket(url.replace('https:', 'wss:') + '/v1/audio/stream'):
                self.fail('Wrong pin allowed WebSocket')
        self.assertEqual(self.requests, [])

    async def test_router_certificate_renewal_and_address_change_preserve_trust(self):
        identity = self.router_identity()
        original_certificate = identity.certificate_path.read_bytes()
        renewed = self.router_identity(identity=identity.id, now=datetime.now(timezone.utc) + timedelta(days=365 * 51))
        self.assertEqual(identity.fp, renewed.fp)
        self.assertNotEqual(original_certificate, renewed.certificate_path.read_bytes())
        url = await self.router_url(renewed, host='127.0.0.2')
        payload = await verify_router(url, identity.id, identity.fp)
        self.store.trust_router(payload, 'https://127.0.0.1:1')
        self.store.trust_router(payload, url)
        self.assertEqual(self.store.routers()[0]['id'], identity.id)
        self.assertEqual(self.store.routers()[0]['fp'], identity.fp)
        async with router_session() as client:
            async with client.post(url + '/v1/chat/completions', json={'secret': 'fixture'}) as response:
                self.assertEqual(response.status, 200)

    async def test_replaced_router_private_key_under_same_uuid_is_rejected(self):
        original = self.router_identity()
        replacement = self.router_identity(directory='replacement', identity=original.id)
        self.assertNotEqual(original.fp, replacement.fp)
        url = await self.router_url(replacement)
        with self.assertRaises(aiohttp.ClientError):
            await verify_router(url, original.id, original.fp)
        self.assertEqual(self.requests, [])

    async def test_removed_router_trust_closes_existing_stream(self):
        identity = self.router_identity()
        url = await self.router_url(identity)
        self.store.trust_router(identity.payload('Router'), url)
        # Another registration of the same key cannot preserve the removed target.
        self.store.trust_router({'id': str(uuid.uuid4()), 'name': 'Other registration',
                                 'fp': identity.fp}, 'https://127.0.0.1:1')
        async with router_session() as client:
            stream = await client.get(url + '/v1/stream')
            self.assertIn(b'connected', await stream.content.readline())
            self.store.remove_router(identity.id)
            async def read_closed():
                try:
                    await stream.read()
                except aiohttp.ClientError:
                    pass
            await asyncio.wait_for(read_closed(), 3)

    async def dart_probe(self, url, pin, mode='POST', token='fixture-token',
                         path='/api/v1/chats/probe', body='{"secret":"fixture"}'):
        root = os.environ.get('ORION_FLUTTER_REPO')
        dart = os.environ.get('ORION_DART_EXE') or shutil.which('dart')
        if not root or not dart:
            self.skipTest('Set ORION_FLUTTER_REPO and install Dart for live Flutter checks')
        app = Path(root) / 'main_app' / 'app'
        probe = app / 'tool' / 'phase6_integration_probe.dart'
        self.assertTrue(probe.exists(), 'Flutter production transport probe missing')
        process = await asyncio.create_subprocess_exec(dart, 'run', str(probe), url, pin, mode, path, body, token,
            cwd=str(app), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(process.communicate(), 45)
        return process.returncode, output.decode(), errors.decode()

    async def test_flutter_production_adapter_correct_and_wrong_hub_pin(self):
        url = await self.mobile_url()
        _, token = await self.paired_device()
        status, output, errors = await self.dart_probe(url, self.hub['fp'], token=token)
        self.assertEqual(status, 0, errors + output)
        self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        self.http_entries.clear()
        status, output, errors = await self.dart_probe(url, '0' * 64, token=token)
        self.assertNotEqual(status, 0, errors + output)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.http_entries, [])

    async def test_flutter_live_pair_claim_checks_pin_before_sending_code(self):
        url = await self.mobile_url()
        code, _ = self.store.create_pairing()
        body = json.dumps({'code': code, 'device_name': 'Dart integration', 'platform': 'test'})
        status, output, errors = await self.dart_probe(url, '0' * 64,
            path='/api/v1/pair/claim', body=body, token='')
        self.assertNotEqual(status, 0, errors + output)
        self.assertEqual(self.http_entries, [])
        self.assertEqual(self.store.devices(), [])
        status, output, errors = await self.dart_probe(url, self.hub['fp'],
            path='/api/v1/pair/claim', body=body, token='')
        self.assertEqual(status, 0, errors + output)
        self.assertEqual(len(self.store.devices()), 1)
        self.assertEqual(self.store.devices()[0]['name'], 'Dart integration')

    async def test_flutter_websocket_uses_same_pin_and_sends_no_wrong_pin_upgrade(self):
        url = await self.mobile_url()
        _, token = await self.paired_device()
        status, output, errors = await self.dart_probe(url, '0' * 64, mode='WS',
            path='/api/v1/audio/probe', body='null', token=token)
        self.assertNotEqual(status, 0, errors + output)
        self.assertEqual(self.http_entries, [])
        status, output, errors = await self.dart_probe(url, self.hub['fp'], mode='WS',
            path='/api/v1/audio/probe', body='null', token=token)
        self.assertEqual(status, 0, errors + output)
        self.assertEqual(self.http_entries, ['/api/v1/audio/probe'])

    async def test_flutter_same_key_renewal_and_replacement_rejection(self):
        url = await self.mobile_url()
        _, token = await self.paired_device()
        tls_identity(renew=True)
        new_url = await self.mobile_url()
        self.assertNotEqual(url, new_url)
        self.assertEqual(tls_identity()['fp'], self.hub['fp'])
        status, output, errors = await self.dart_probe(new_url, self.hub['fp'], token=token)
        self.assertEqual(status, 0, errors + output)
        other = tls_identity(self.directory / 'replacement-hub')
        replaced_url = await self.serve(self.mobile_app(), other['cert'], other['key'])
        self.requests.clear()
        status, output, errors = await self.dart_probe(replaced_url, self.hub['fp'], token=token)
        self.assertNotEqual(status, 0, errors + output)
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
