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
    async def test_one_short_link_carries_only_the_pairing(self):
        app = FastAPI()
        app.include_router(owner_router)
        app.state.redis = AsyncMock()
        store = Mock()
        store.create_pairing.return_value = ('ABCDEFGH', 1234)
        fp = '0123456789abcdef' * 4
        with patch('orion.api.mobile_security.require_owner'), patch('orion.api.mobile_security.SecurityStore', return_value=store), patch('orion.api.mobile_security.tls_identity', return_value={'id':ID,'fp':fp,'name':'PC'}), patch('orion.api.mobile_security.local_address', return_value='192.168.1.20'), patch('orion.api.invitations.create_invitation', AsyncMock()) as invite:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://localhost') as client:
                response = await client.post('/api/v1/owner/secure/pairing', json={})
                self.assertEqual(response.status_code, 200)
                data = response.json()
        # A domain name, so messaging apps make it tappable; the pairing stays after "#", on the phone.
        base, token = data['link'].split('#', 1)
        self.assertEqual(base, 'https://orion-ai-assistant.github.io/p')
        self.assertLessEqual(len(data['link']), 80)
        self.assertEqual(data['app_link'], 'orion://pair?l=' + token)
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        self.assertEqual(raw[0], 2)
        self.assertEqual(raw[1:5], bytes([192, 168, 1, 20]))
        self.assertEqual(int.from_bytes(raw[5:7], 'big'), 8443)
        self.assertEqual(raw[12:28], bytes.fromhex(fp)[:16])
        self.assertEqual(len(raw), 28)
        # One credential, one lifetime: no separate account invitation any more.
        invite.assert_not_awaited()
        self.assertNotIn('invitation_token', data['qr'])
        self.assertEqual(data['expires_in'], 180)
        self.assertEqual(app.state.redis.set.await_args.kwargs['ex'], 180)

    def test_code_packs_into_five_bytes(self):
        from orion.api.mobile_security import CODE_ALPHABET, pairing_token
        token = pairing_token('fe80::1', 8443, 'Z2Z2Z2Z2', 'ff' * 32)
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        self.assertEqual(raw[0], 3)
        self.assertEqual(len(raw), 40)
        number = int.from_bytes(raw[19:24], 'big')
        code = ''.join(CODE_ALPHABET[(number >> shift) & 31] for shift in range(35, -1, -5))
        self.assertEqual(code, 'Z2Z2Z2Z2')

    async def test_mobile_root_explains_usage_without_exposing_protected_api(self):
        app = MobileBoundary(FastAPI())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://192.168.1.20:8443') as client:
            response = await client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('8911', response.text)
            self.assertNotIn('<a ', response.text, 'No browser chat to link to')
            landing = await client.get('/connect')
            self.assertEqual(landing.status_code, 200)
            self.assertEqual(landing.headers['referrer-policy'], 'no-referrer')
            self.assertIn('orion://pair?data=', landing.text)
            self.assertNotIn('/dashboard', landing.text)
            self.assertNotIn('Tarayıcıda devam', landing.text)
            for path in ['/hub', '/dashboard/', '/js/main.js']:
                self.assertEqual((await client.get(path)).status_code, 403, path)
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
