import asyncio
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from orion.api.mobile_security import owner_router, MobileBoundary
from services.shared.environment import get_router_base_urls

ID = '12345678-1234-4321-8123-123456789abc'


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_link_includes_pairing_and_optional_account_invitation(self):
        app = FastAPI()
        app.include_router(owner_router)
        store = Mock()
        store.create_pairing.return_value = ('ABCDEFGH', 1234)
        with patch('orion.api.mobile_security.require_owner'), patch('orion.api.mobile_security.SecurityStore', return_value=store), patch('orion.api.mobile_security.tls_identity', return_value={'id':ID,'fp':'a'*64,'name':'PC'}), patch('orion.api.mobile_security.local_address', return_value='192.168.1.20'), patch('orion.api.invitations.create_invitation', AsyncMock()) as invite:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://localhost') as client:
                for allowed in (False, True):
                    response = await client.post('/api/v1/owner/secure/pairing', json={'allow_registration':allowed})
                    self.assertEqual(response.status_code, 200)
                    data = response.json()
                    raw = data['link'].split('data=',1)[1]
                    decoded = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
                    self.assertEqual(decoded, data['qr'])
                    self.assertEqual(decoded['url'], 'https://192.168.1.20:8443')
                    self.assertEqual(decoded['code'], 'ABCDEFGH')
                    self.assertEqual('invitation_token' in decoded, allowed)
                    self.assertTrue(data['link'].startswith('orion://pair?data='))
                invite.assert_awaited_once()

    async def test_mobile_root_explains_usage_without_exposing_protected_api(self):
        app = MobileBoundary(FastAPI())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://192.168.1.20:8443') as client:
            response = await client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('https://192.168.1.20:8911/', response.text)
            with patch('orion.api.mobile_security.SecurityStore') as factory:
                factory.return_value.authenticate.return_value = None
                self.assertEqual((await client.get('/api/v1/chats')).status_code, 401)
            self.assertEqual((await client.get('http://localhost:8443/')).status_code, 403)


class StickyRouterTests(unittest.TestCase):
    def test_selected_router_never_falls_back_to_other_trusted_router(self):
        store = Mock()
        store.routers.return_value = [{'id':ID,'fp':'a'*64,'url':'https://192.168.1.20:9443'}, {'id':'other','fp':'b'*64,'url':'https://192.168.1.30:9443'}]
        with patch('orion.kernel.local_security.SecurityStore', return_value=store), patch('services.shared.router_connection.read_connection', return_value={'id':ID,'fp':'a'*64}):
            self.assertEqual(get_router_base_urls(), ['https://192.168.1.20:9443'])
            store.routers.return_value = [store.routers.return_value[1]]
            with self.assertRaises(RuntimeError):
                get_router_base_urls()

    def test_multiple_trusted_routers_without_selection_require_owner_choice(self):
        store = Mock()
        store.routers.return_value = [{'id':ID},{'id':'other'}]
        with patch('orion.kernel.local_security.SecurityStore', return_value=store), patch('services.shared.router_connection.read_connection', return_value=None):
            with self.assertRaises(RuntimeError):
                get_router_base_urls()
