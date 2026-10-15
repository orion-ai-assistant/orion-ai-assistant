import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from orion.api.access_boundary import AccessBoundary
from orion.api.auth_routes import get_current_user
from orion.api.browser_routes import router
from orion.api.cors import configure_cors


class FlutterBrowserAccessTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, origin='http://localhost:8090', peer='127.0.0.1', forwarded=False):
        app = FastAPI()
        configure_cors(app)
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: 'alice'
        app.add_middleware(AccessBoundary, public=True, local_entry=True)
        headers = {'Origin': origin, 'Sec-Fetch-Site': 'same-site'}
        if forwarded:
            headers['X-Forwarded-For'] = '127.0.0.1'
        manager = Mock(status=AsyncMock(return_value={'running': True}))
        with patch('orion.api.browser_routes.BrowserManager', return_value=manager), patch('orion.api.discovery.lan_addresses', return_value=[]):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 123)), base_url='http://localhost:8910') as client:
                response = await client.get('/api/v1/browser', headers=headers)
        return response, manager

    async def test_configured_flutter_origin_reaches_account_browser_api(self):
        response, manager = await self.request()
        self.assertEqual(response.status_code, 200)
        manager.status.assert_awaited_once()

    async def test_other_origins_remote_and_forwarded_peers_never_reach_manager(self):
        for args in [dict(origin='http://localhost:8091'), dict(origin='https://evil.example'),
                     dict(origin='http://localhost.evil.example:8090'), dict(peer='192.168.1.50'), dict(forwarded=True)]:
            response, manager = await self.request(**args)
            self.assertEqual(response.status_code, 403)
            manager.status.assert_not_awaited()


class NativeDesktopBrowserAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_mobile_listener_requires_local_peer_and_existing_device_pairing(self):
        from orion.api.mobile_security import MobileBoundary
        for peer, paired, expected in [('192.168.1.50', True, 403), ('127.0.0.1', False, 401), ('127.0.0.1', True, 200)]:
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_current_user] = lambda: 'alice'
            app.add_middleware(MobileBoundary)
            store = Mock(authenticate=Mock(return_value='device' if paired else None))
            manager = Mock(status=AsyncMock(return_value={'running': True}))
            with patch('orion.api.mobile_security.SecurityStore', return_value=store), patch('orion.api.browser_routes.BrowserManager', return_value=manager), patch('orion.api.discovery.lan_addresses', return_value=[]):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 123)), base_url='https://localhost:8443') as client:
                    response = await client.get('/api/v1/browser', headers={'X-Device-Token': 'paired-token'})
            self.assertEqual(response.status_code, expected)
            if expected != 200:
                manager.status.assert_not_awaited()
