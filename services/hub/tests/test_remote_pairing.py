import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi import FastAPI
from orion.api.access_boundary import AccessBoundary
from orion.api.mobile_security import pairing_router

PHONE = ('192.168.1.42', 1234)
PC = ('127.0.0.1', 1234)


class DesktopOnlyPairingTests(unittest.IsolatedAsyncioTestCase):
    """New devices join only from the Orion window on the Hub's PC; phones never create QR codes."""
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.state.redis = MagicMock()
        self.app.state.redis.eval = AsyncMock(return_value=[1, 30])
        self.app.state.redis.set = AsyncMock()
        self.app.state.redis.exists = AsyncMock(return_value=True)
        self.app.include_router(pairing_router)
        self.app.add_middleware(AccessBoundary, public=True)
        self.auth = patch('orion.api.auth_routes.authenticate_token', AsyncMock(return_value='friend')); self.auth.start()
        self.key = patch('orion.api.security.valid_admin_key', return_value=True); self.mock_key = self.key.start()
        self.store = patch('orion.api.mobile_security.SecurityStore'); self.mock_store = self.store.start()
        self.mock_store.return_value.create_pairing.return_value = ('single-use-code', 12345)
        self.identity = patch('orion.api.mobile_security.tls_identity', return_value={'id': 'hub-id', 'fp': 'a' * 64, 'name': 'Hiro'}); self.identity.start()
        self.address = patch('orion.api.mobile_security.local_address', return_value='192.168.1.10'); self.address.start()
        self.lan = patch('orion.api.discovery.lan_addresses', return_value=['192.168.1.10']); self.lan.start()
        self.invites = patch('orion.api.invitations.create_invitation', AsyncMock()); self.mock_invites = self.invites.start()
        self.addCleanup(patch.stopall)

    async def create(self, client=PC, base_url='http://localhost:8910', headers=None, json=None, token=True):
        transport = httpx.ASGITransport(app=self.app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as http:
            return await http.post('/api/v1/pair/create', json=json,
                                   headers={**({'Authorization': 'Bearer account-token'} if token else {}), **(headers or {})})

    async def capabilities(self, client, base_url):
        transport = httpx.ASGITransport(app=self.app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as http:
            return (await http.get('/api/v1/pair/capabilities', headers={'Authorization': 'Bearer account-token'})).json()

    async def test_pc_window_creates_qr_and_invite_without_password(self):
        response = await self.create()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['qr']['code'], 'single-use-code'); self.assertIn('invitation_token', data['qr'])
        self.assertTrue(data['qr_png']); self.assertTrue(data['link'].startswith('https://192.168.1.10:8443/connect#data='))
        self.assertEqual(response.headers['cache-control'], 'no-store'); self.assertNotIn('set-cookie', response.headers)
        self.mock_key.assert_not_called(); self.mock_invites.assert_awaited_once()

    async def test_pc_window_answers_a_new_device_request(self):
        response = await self.create(json={'request_id': 'phone-request'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['request_id'], 'phone-request')
        self.app.state.redis.exists.assert_awaited_once_with('orion:pairing:request:phone-request')

    async def test_paired_phone_cannot_create_qr_even_with_the_owner_password(self):
        for json in (None, {'password': 'orion'}):
            response = await self.create(client=PHONE, base_url='https://192.168.1.10:8443', json=json)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn('Hub’ın kurulu olduğu bilgisayar', response.json()['detail'])
        self.mock_store.assert_not_called(); self.mock_invites.assert_not_awaited(); self.mock_key.assert_not_called()

    async def test_forwarded_request_to_this_pc_is_not_the_pc(self):
        response = await self.create(headers={'X-Forwarded-For': '192.168.1.42'})
        self.assertEqual(response.status_code, 403); self.mock_store.assert_not_called()

    async def test_pc_window_still_needs_an_account(self):
        response = await self.create(token=False)
        self.assertEqual(response.status_code, 401); self.mock_store.assert_not_called()

    async def test_only_this_pc_is_offered_qr_creation(self):
        self.assertTrue((await self.capabilities(PC, 'http://localhost:8910'))['can_create_pairing'])
        self.assertFalse((await self.capabilities(PHONE, 'https://192.168.1.10:8443'))['can_create_pairing'])


if __name__ == '__main__':
    unittest.main()
