import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
import httpx

from orion.api.router_connection import RouterAssociation
from orion.api.main import local_app
from services.shared import router_connection as store
from services.shared.environment import get_router_base_urls

ID = '00000000-0000-4000-8000-000000000001'
OTHER = '12345678-1234-4321-8123-123456789abc'
RECORD = {'id': ID, 'name': 'Hiro — Router', 'url': 'http://192.168.1.20:20128/'}


class Redis:
    def __init__(self):
        self.selection_lock = asyncio.Lock()
        self.data = {}

    def lock(self, *args, **kwargs):
        return self.selection_lock

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, **kwargs):
        self.data[key] = value

    async def eval(self, *args):
        return [1, 60]


class RouterConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'router-connection.json'
        self.patch = patch.object(store, 'connection_path', return_value=self.path)
        self.patch.start()
        self.environment = patch.dict('os.environ', {'ROUTER_BASE_URL': '', 'ROUTER_HOST': 'router', 'ROUTER_PORT': '20128', 'ADMIN_API_KEY': 'owner-password'})
        self.environment.start()
        self.redis = Redis()
        self.manager = RouterAssociation(self.redis)
        self.manager.directory.list = AsyncMock(return_value=[dict(RECORD)])
        self.manager.check = AsyncMock(return_value='http://192.168.1.20:20128')

    async def asyncTearDown(self):
        self.environment.stop()
        self.patch.stop()
        self.temp.cleanup()

    async def test_single_router_is_checked_and_uuid_persists_for_worker(self):
        selected = await self.manager.refresh()
        self.assertEqual(selected['id'], ID)
        self.manager.check.assert_awaited_once()
        self.assertEqual(get_router_base_urls(), ['http://192.168.1.20:20128'])
        self.assertEqual(store.read_connection()['id'], ID)
        self.assertNotIn('api_key', self.path.read_text())

    async def test_multiple_routers_are_not_automatically_selected(self):
        self.manager.directory.list.return_value.append({**RECORD, 'id': OTHER})
        self.assertIsNone(await self.manager.refresh())
        self.manager.check.assert_not_awaited()
        self.assertFalse(self.path.exists())

    async def test_existing_unique_hostname_migrates_to_its_same_uuid(self):
        with patch.dict('os.environ', {'ROUTER_BASE_URL': 'http://orionrouter-00000000000040008000000000000001.local:20128'}):
            selected = await self.manager.refresh()
            self.assertEqual(selected['id'], ID)
            self.assertEqual(get_router_base_urls(), ['http://192.168.1.20:20128'])

    async def test_failed_api_check_does_not_create_an_association(self):
        from fastapi import HTTPException
        self.manager.check.side_effect = HTTPException(503, 'offline')
        self.assertIsNone(await self.manager.refresh())
        self.assertFalse(self.path.exists())

    async def test_saved_router_updates_ip_but_never_changes_uuid(self):
        await self.manager.refresh()
        self.manager.directory.list.return_value = [{**RECORD, 'url': 'http://192.168.1.21:20128/'}, {**RECORD, 'id': OTHER}]
        self.manager.check.return_value = 'http://192.168.1.21:20128'
        selected = await self.manager.refresh()
        self.assertEqual(selected['id'], ID)
        self.assertEqual(get_router_base_urls(), ['http://192.168.1.21:20128'])

    async def test_offline_saved_router_and_expired_endpoint_fail_closed(self):
        await self.manager.refresh()
        self.manager.directory.list.return_value = [{**RECORD, 'id': OTHER}]
        selected = await self.manager.refresh()
        self.assertEqual(selected['id'], ID)
        self.assertIsNone(selected['url'])
        with self.assertRaises(RuntimeError):
            get_router_base_urls()
        store.write_connection({**selected, 'url': 'http://192.168.1.20:20128', 'last_seen': time.time() - 76})
        with self.assertRaises(RuntimeError):
            get_router_base_urls()

    async def test_conflicting_uuid_is_not_selected(self):
        self.manager.directory.list.return_value = [{**RECORD, 'conflict': True}]
        self.assertIsNone(await self.manager.refresh())
        self.manager.check.assert_not_awaited()

    async def test_explicit_environment_and_manual_selection_are_preserved(self):
        with patch.dict('os.environ', {'ROUTER_BASE_URL': 'http://192.168.1.44:20128'}):
            self.assertIsNone(await self.manager.refresh())
            self.manager.check.assert_not_awaited()
        store.write_connection({'mode': 'manual', 'url': 'http://localhost:20128', 'name': 'Manual'})
        await self.manager.refresh()
        self.assertEqual(get_router_base_urls(), ['http://127.0.0.1:20128'])

    async def test_corrupt_selection_is_not_deleted_or_replaced(self):
        self.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            await self.manager.refresh()
        self.assertEqual(self.path.read_text(), 'broken')

    async def test_owner_selection_requires_admin_scope_and_mount_stays_local(self):
        local_app.state.panel_app.state.redis = self.redis
        local_app.state.panel_app.state.router_association = self.manager
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            self.assertEqual((await client.get('/panel/api/v1/owner/router')).status_code, 401)
            for scope, expected in [('people', 401), ('admin', 200)]:
                self.assertEqual((await client.post('/panel/api/v1/owner/login', json={'password': 'owner-password', 'scope': scope})).status_code, 200)
                response = await client.get('/panel/api/v1/owner/router')
                self.assertEqual(response.status_code, expected)
            response = await client.post('/panel/api/v1/owner/router', json={'id': ID})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['identity_verified'])
            self.assertEqual((await client.post('/panel/api/v1/owner/router', json={'manual_url': 'http://localhost:20128'}, headers={'Origin': 'http://evil.example'})).status_code, 403)
            page = await client.get('/panel/router/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('/panel/owner-assets/router.js', page.text)
            self.assertIn('href="/hub"', page.text)

    async def test_real_identity_and_api_key_checks_happen_in_order(self):
        seen = []
        claimed = {'id': ID}
        async def identity(request):
            seen.append(('identity', request.headers.get('Authorization')))
            return web.json_response(claimed)
        async def models(request):
            seen.append(('models', request.headers.get('Authorization')))
            return web.json_response({'data': []})
        app = web.Application()
        app.router.add_get('/v1/discovery/self', identity)
        app.router.add_get('/v1/models', models)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        manager = RouterAssociation(self.redis)
        try:
            with patch('orion.api.router_connection.get_runtime_settings', AsyncMock(return_value=SimpleNamespace(router_api_key='test-router-key'))):
                self.assertEqual(await manager.check({**RECORD, 'url': base}), base)
                self.assertEqual(seen, [('identity', None), ('models', 'Bearer test-router-key')])
                seen.clear()
                claimed['id'] = OTHER
                from fastapi import HTTPException
                with self.assertRaises(HTTPException):
                    await manager.check({**RECORD, 'url': base})
                self.assertEqual(seen, [('identity', None)])
        finally:
            await runner.cleanup()
