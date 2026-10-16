import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from fastapi import HTTPException
import httpx

from orion.api import router_connection as api
from orion.api.main import local_app
from orion.kernel.local_security import SecurityStore, tls_identity
from orion.kernel.pinned_router import verify_router, router_session
from services.shared import router_connection as store
from services.shared.environment import get_router_base_urls

ID = '00000000-0000-4000-8000-000000000001'
OTHER = '12345678-1234-4321-8123-123456789abc'
FP = 'a' * 64
RECORD = {'id': ID, 'name': 'Router', 'url': 'https://192.0.2.20:9443', 'fp': FP}


class Redis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, **kwargs):
        self.data[key] = value

    async def eval(self, *args):
        return [1, 60]


class RouterConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'selection.json'
        env = patch.dict(os.environ, {
            'ORION_SECURITY_DIR': str(self.root / 'security'),
            'ORION_ROUTER_CONNECTION_FILE': str(self.path),
            'LOCALAPPDATA': str(self.root / 'local'),
            'ROUTER_HOST': 'router', 'ORION_ROUTER_TLS_PORT': '9443',
            'ADMIN_API_KEY': 'owner-password',
        })
        env.start()
        self.addCleanup(env.stop)
        self.security = SecurityStore()
        self.redis = Redis()
        self.manager = api.RouterAssociation(self.redis)
        self.manager.directory.list = AsyncMock(return_value=[dict(RECORD)])
        for name, replacement in [
            ('orion.kernel.router_bootstrap.bootstrap_local_router', AsyncMock()),
            ('orion.api.router_connection.resolve_trusted_routers', AsyncMock()),
            ('orion.kernel.router_bootstrap.local_router_identity', AsyncMock(return_value=None)),
            ('orion.kernel.router_models.clear_catalog_cache', None),
        ]:
            item = patch(name, replacement) if replacement is not None else patch(name)
            item.start()
            self.addCleanup(item.stop)
        self.verifier = AsyncMock(return_value={'v': 1, **RECORD})
        item = patch.object(api, 'verify_router', self.verifier)
        item.start()
        self.addCleanup(item.stop)

    def trust(self, record=RECORD):
        self.security.trust_router(record, record['url'])

    async def choose(self, payload):
        with patch.object(api, 'association', return_value=self.manager):
            return await api.select(payload, None)

    async def test_single_trusted_router_persists_uuid_and_pin_for_worker(self):
        self.trust()
        selected = await self.manager.refresh()
        self.assertEqual(selected, RECORD)
        self.assertEqual(get_router_base_urls(), [RECORD['url']])
        self.assertEqual(store.read_connection()['id'], ID)
        self.assertEqual(store.read_connection()['fp'], FP)
        self.assertNotIn('api_key', self.path.read_text())
        self.manager.directory.list.assert_not_awaited()

    async def test_multiple_trusted_routers_are_not_automatically_selected(self):
        self.trust()
        self.trust({**RECORD, 'id': OTHER, 'url': 'https://other.test:9443'})
        self.assertIsNone(await self.manager.refresh())
        self.assertFalse(self.path.exists())
        with self.assertRaises(RuntimeError):
            get_router_base_urls()

    async def test_discovery_and_legacy_hostname_do_not_create_trust(self):
        with patch.dict(os.environ, {'ROUTER_BASE_URL': 'http://orionrouter.local:20128'}):
            self.assertIsNone(await self.manager.refresh())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.security.routers(), [])
        self.manager.directory.list.return_value = [{k: v for k, v in RECORD.items() if k != 'fp'}]
        with self.assertRaises(HTTPException) as failure:
            await self.choose(api.Selection(id=ID))
        self.assertEqual(failure.exception.status_code, 400)
        self.verifier.assert_not_awaited()

    async def test_failed_identity_verification_never_changes_trust_or_selection(self):
        self.trust()
        await self.manager.refresh()
        original = self.path.read_bytes()
        self.manager.directory.list.return_value = [{**RECORD, 'id': OTHER}]
        for reason in ('wrong uuid', 'wrong pin'):
            self.verifier.side_effect = ValueError(reason)
            with patch.object(SecurityStore, 'trust_router') as trust, patch.object(api, 'write_connection') as write:
                with self.assertRaises(HTTPException) as failure:
                    await self.choose(api.Selection(id=OTHER, fp=FP))
                self.assertEqual(failure.exception.status_code, 409)
                trust.assert_not_called()
                write.assert_not_called()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(self.security.routers(), [RECORD])

    async def test_saved_router_updates_address_but_preserves_uuid_and_pin(self):
        self.trust()
        await self.manager.refresh()
        before = self.path.read_bytes()
        async def resolve():
            self.security.update_router_address(ID, FP, 'https://router-new.local:9443')
        with patch.object(api, 'resolve_trusted_routers', AsyncMock(side_effect=resolve)):
            selected = await self.manager.refresh()
        self.assertEqual((selected['id'], selected['fp']), (ID, FP))
        self.assertEqual(get_router_base_urls(), ['https://router-new.local:9443'])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_offline_saved_router_does_not_switch_to_other_trust_or_env(self):
        self.trust()
        await self.manager.refresh()
        self.trust({**RECORD, 'id': OTHER, 'url': 'https://other.test:9443'})
        selected = await self.manager.refresh()
        self.assertEqual(selected, RECORD)
        self.assertEqual(get_router_base_urls(), [RECORD['url']])
        # Deterministically closed local port: real pinned transport must fail.
        server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        self.security.update_router_address(ID, FP, f'https://127.0.0.1:{port}')
        async with router_session(timeout=aiohttp.ClientTimeout(total=1)) as session:
            with self.assertRaises((aiohttp.ClientError, TimeoutError)):
                await session.get(get_router_base_urls()[0] + '/api/v1/tls/identity')
        self.assertEqual(store.read_connection()['id'], ID)

    async def test_conflicting_discovery_uuid_is_rejected_before_verification(self):
        for records in ([{**RECORD, 'conflict': True}], [dict(RECORD), dict(RECORD)]):
            self.manager.directory.list.return_value = records
            with patch.object(SecurityStore, 'trust_router') as trust, patch.object(api, 'write_connection') as write:
                with self.assertRaises(HTTPException) as failure:
                    await self.choose(api.Selection(id=ID, fp=FP))
                self.assertEqual(failure.exception.status_code, 409)
                self.verifier.assert_not_awaited()
                trust.assert_not_called()
                write.assert_not_called()

    async def test_saved_identity_is_preserved_when_environment_changes(self):
        self.trust()
        await self.manager.refresh()
        before = self.path.read_bytes()
        with patch.dict(os.environ, {'ROUTER_HOST': 'other.test', 'ROUTER_BASE_URL': 'http://legacy'}):
            self.assertEqual(await self.manager.refresh(), RECORD)
            self.assertEqual(get_router_base_urls(), [RECORD['url']])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_manual_selection_requires_uuid_and_verified_pin(self):
        with self.assertRaises(ValueError):
            api.Selection(manual_url='https://localhost:9443', fp=FP)
        result = await self.choose(api.Selection(manual_url='https://localhost:9443/v1/', manual_id=ID, fp=FP))
        self.verifier.assert_awaited_once_with('https://127.0.0.1:9443', ID, FP)
        self.assertTrue(result['identity_verified'])
        self.assertEqual(store.read_connection()['mode'], 'manual')
        self.assertEqual(get_router_base_urls(), ['https://127.0.0.1:9443'])

    async def test_corrupt_selection_is_not_deleted_or_replaced(self):
        for count in (0, 1, 2):
            if count == 1:
                self.trust()
            elif count == 2:
                self.trust({**RECORD, 'id': OTHER})
            self.path.write_text('broken', encoding='utf-8')
            with self.subTest(trusted=count):
                with self.assertRaises(RuntimeError):
                    await self.manager.refresh()
                with self.assertRaises(RuntimeError):
                    get_router_base_urls()
                self.assertEqual(self.path.read_text(), 'broken')

    async def test_corrupt_selection_status_returns_safe_error_without_bootstrap(self):
        self.path.write_text('broken', encoding='utf-8')
        with patch.object(api, 'association', return_value=self.manager), patch(
                'orion.kernel.router_bootstrap.bootstrap_local_router', AsyncMock()) as bootstrap:
            with self.assertRaises(HTTPException) as failure:
                await api.status(None)
        self.assertEqual(failure.exception.status_code, 503)
        self.assertNotIn('broken', failure.exception.detail)
        self.assertEqual(self.path.read_text(), 'broken')
        bootstrap.assert_not_awaited()

    async def test_refresh_rejects_saved_pin_mismatch(self):
        self.trust()
        store.write_connection({**RECORD, 'mode': 'manual', 'fp': 'b' * 64})
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            await self.manager.refresh()
        self.assertEqual(self.path.read_bytes(), before)

    async def test_revoked_selection_is_not_replaced_by_single_remaining_router(self):
        self.trust()
        await self.manager.refresh()
        self.security.remove_router(ID)
        self.trust({**RECORD, 'id': OTHER})
        self.assertIsNone(await self.manager.refresh())
        self.assertEqual(store.read_connection()['id'], ID)
        with self.assertRaises(RuntimeError):
            get_router_base_urls()

    async def test_owner_selection_requires_admin_scope_and_mount_stays_local(self):
        state = local_app.state.panel_app.state
        for name, value in [('redis', self.redis), ('router_association', self.manager)]:
            item = patch.object(state, name, value, create=True)
            item.start()
            self.addCleanup(item.stop)
        with patch('orion.kernel.router_accounts.ensure_connection', AsyncMock()), patch(
                'orion.kernel.router_accounts.request_router', AsyncMock(return_value={})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
                self.assertEqual((await client.get('/panel/api/v1/owner/router')).status_code, 401)
                self.assertEqual((await client.post('/panel/api/v1/owner/login', json={'password': 'wrong-password', 'scope': 'admin'})).status_code, 401)
                for scope, expected in [('people', 401), ('admin', 200)]:
                    self.assertEqual((await client.post('/panel/api/v1/owner/login', json={'password': 'owner-password', 'scope': scope})).status_code, 200)
                    self.assertEqual((await client.get('/panel/api/v1/owner/router')).status_code, expected)
                    if scope == 'people':
                        self.assertEqual((await client.post('/panel/api/v1/owner/router', json={'id': ID, 'fp': FP})).status_code, 401)
                response = await client.post('/panel/api/v1/owner/router', json={'id': ID, 'fp': FP})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['identity_verified'])
                self.assertEqual((await client.post('/panel/api/v1/owner/router', json={'id': ID, 'fp': FP}, headers={'Origin': 'http://evil.example'})).status_code, 403)
                for value in ('https://user:fixture-password@router.test', 'https://router.test?token=fixture-secret', 'https://router.test/#fixture-secret'):
                    invalid = await client.post('/panel/api/v1/owner/router', json={'manual_url': value, 'manual_id': ID, 'fp': FP})
                    self.assertEqual(invalid.status_code, 422)
                    self.assertNotIn('fixture-password', invalid.text)
                    self.assertNotIn('fixture-secret', invalid.text)
                page = await client.get('/panel/router/')
                self.assertEqual(page.status_code, 200)
                self.assertIn('/panel/owner-assets/router.js', page.text)
                self.assertIn('href="/panel/"', page.text)
                self.assertNotIn('src="/owner-assets/', page.text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('192.0.2.10', 1234)), base_url='http://localhost') as remote:
                self.assertIn((await remote.get('/panel/router/')).status_code, (403, 404))

    async def test_verified_identity_can_exist_without_account_access(self):
        from orion.kernel.router_accounts import RouterAccountError
        self.trust()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(router_association=self.manager)))
        with patch.object(api, 'association', return_value=self.manager), patch(
                'orion.kernel.router_accounts.ensure_connection', AsyncMock(side_effect=RouterAccountError('not enrolled'))), patch(
                'orion.kernel.router_accounts.request_router', AsyncMock()) as call:
            result = await api.status(request)
        self.assertTrue(result['identity_verified'])
        self.assertFalse(result['access_ready'])
        call.assert_not_awaited()

    async def test_real_tls_pin_and_uuid_are_checked_before_account_auth(self):
        tls = tls_identity(self.root / 'tls')
        identity = {key: tls[key] for key in ('v', 'id', 'name', 'fp')}
        seen = []
        async def handler(request):
            seen.append(dict(request.headers))
            return web.json_response(identity)
        app = web.Application()
        app.router.add_get('/api/v1/tls/identity', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls['cert'], tls['key'])
        site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=context)
        await site.start()
        base = 'https://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        self.assertEqual(await verify_router(base, tls['id'], tls['fp']), identity)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('Authorization', seen[0])
        with self.assertRaises(ValueError):
            await verify_router(base, OTHER, tls['fp'])
        self.assertEqual(len(seen), 2)
        with self.assertRaises(aiohttp.ServerFingerprintMismatch):
            await verify_router(base, tls['id'], 'b' * 64)
        self.assertEqual(len(seen), 2)
        self.trust({**identity, 'url': base, 'fp': 'b' * 64})
        async with router_session(timeout=aiohttp.ClientTimeout(total=1)) as session:
            with self.assertRaises(aiohttp.ClientError):
                await session.get(base + '/api/v1/tls/identity', headers={'Authorization': 'Bearer fixture-account'})
        self.assertEqual(len(seen), 2)
