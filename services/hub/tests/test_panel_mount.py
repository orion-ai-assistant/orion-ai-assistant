import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from orion.api.main import shortcuts_app
from orion.api import invitations


class PanelRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, **kwargs):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def eval(self, *args):
        return [1, 60]


class PanelMountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        shortcuts_app.state.panel_app.state.redis = PanelRedis()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=shortcuts_app, client=('127.0.0.1', 1234)),
            base_url='https://localhost',
        )
        self.environment = patch.dict(os.environ, {'ADMIN_API_KEY':'owner-password'})
        self.environment.start()
        self.password_hash = patch('orion.api.security.owner_password_hash', return_value=None)
        self.password_hash.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.environment.stop()
        self.password_hash.stop()

    async def login(self, **headers):
        return await self.client.post('/panel/api/v1/owner/login',
            json={'password':'owner-password', 'scope':'people'}, headers=headers)

    async def test_login_cookie_and_authenticated_requests_stay_beneath_panel(self):
        response = await self.login(Origin='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Path=/panel', response.headers['set-cookie'])
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('orion_owner_panel=', response.headers['set-cookie'])
        self.client.cookies.set('orion_owner', 'old-direct-port-cookie', path='/')
        with patch.object(invitations, 'list_people', AsyncMock(return_value=[])), \
             patch.object(invitations, 'list_invitations', AsyncMock(return_value=[])), \
             patch('orion.api.owner_routes.invitation_address', return_value='https://orion.local'):
            response = await self.client.get('/panel/api/v1/owner/people')
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get('/panel/api/v1/admin/chats')).status_code, 401)
        self.assertEqual((await self.client.get('/panel/api/v1/chats')).status_code, 403)
        logout = await self.client.post('/panel/api/v1/owner/logout', headers={'Origin':'https://localhost'})
        self.assertEqual(logout.status_code, 200)
        self.assertIn('Path=/panel', logout.headers['set-cookie'])
        self.assertEqual((await self.client.get('/panel/api/v1/owner/people')).status_code, 401)

    async def test_cross_origin_login_is_rejected(self):
        self.assertEqual((await self.login(Origin='https://evil.example')).status_code, 403)
        self.assertEqual((await self.login(**{'Sec-Fetch-Site':'cross-site'})).status_code, 403)

    async def test_assets_and_navigation_use_panel_prefix(self):
        for path in ['/panel/owner-assets/owner.css', '/panel/owner-assets/base.js',
                     '/panel/owner-assets/gate.js', '/panel/owner-assets/people.js',
                     '/panel/admin/css/admin.css', '/panel/admin/js/admin.js']:
            self.assertEqual((await self.client.get(path)).status_code, 200, path)
        for path in ['/panel/manage/', '/panel/admin/', '/panel/router/', '/panel/manage/security']:
            response = await self.client.get(path)
            self.assertIn('/panel/owner-assets/base.js', response.text)
            self.assertIn('href="/panel/"', response.text)
        for path in ['/panel', '/panel/manage/']:
            response = await self.client.get(path)
            self.assertIn('href="/hub"', response.text)
            self.assertNotIn('localhost:8910', response.text)
        response = await self.client.get('/panel/admin')
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers['location'], 'https://localhost/panel/admin/')

    async def test_remote_client_cannot_read_panel_pages_assets_or_api(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=shortcuts_app, client=('192.168.1.100',1234)),
            base_url='https://localhost',
        ) as remote:
            for path in ['/panel', '/panel/admin/', '/panel/owner-assets/base.js', '/panel/api/v1/owner/people']:
                self.assertEqual((await remote.get(path)).status_code, 403, path)
