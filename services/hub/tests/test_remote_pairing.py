import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi import FastAPI
from orion.api.access_boundary import AccessBoundary
from orion.api.mobile_security import pairing_router

class RemotePairingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.state.redis = MagicMock()
        self.app.state.redis.eval = AsyncMock(return_value=[1,30])
        self.app.state.redis.set = AsyncMock()
        self.app.include_router(pairing_router)
        self.app.add_middleware(AccessBoundary, public=True)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('192.168.1.42',1234)), base_url='https://hub.example')
        self.auth = patch('orion.api.auth_routes.authenticate_token', AsyncMock(return_value='friend')); self.auth.start()
        self.key = patch('orion.api.security.valid_admin_key', side_effect=lambda value: value == 'private-owner-password');self.key.start()
        self.store = patch('orion.api.mobile_security.SecurityStore');self.mock_store=self.store.start()
        self.mock_store.return_value.create_pairing.return_value=('single-use-code',12345)
        self.identity = patch('orion.api.mobile_security.tls_identity',return_value={'id':'hub-id','fp':'a'*64,'name':'Hiro'});self.identity.start()
        self.address = patch('orion.api.mobile_security.local_address',return_value='192.168.1.10');self.address.start()
        self.invitation_address = patch('orion.api.owner_routes.invitation_address',return_value='https://hub.example');self.invitation_address.start()
        self.invites = patch('orion.api.invitations.create_invitation',AsyncMock());self.mock_invites=self.invites.start()
        self.addAsyncCleanup(self.client.aclose)
        self.addCleanup(patch.stopall)
    async def create(self, password='private-owner-password', headers=None, url='/api/v1/pair/create'):
        return await self.client.post(url,headers={'Authorization':'Bearer account-token',**(headers or {})},json={'password':password})
    async def test_authenticated_https_creates_pairing_and_account_invite_only(self):
        response=await self.create()
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertIn('invitation_token',data['qr']);self.assertEqual(data['qr']['code'],'single-use-code')
        self.assertTrue(data['qr_png']);self.assertTrue(data['link'].startswith('https://hub.example/connect#data='))
        self.assertEqual(response.headers['cache-control'],'no-store');self.assertNotIn('set-cookie',response.headers)
        self.assertNotIn('password',data);self.mock_invites.assert_awaited_once()
        owner=await self.client.get('/api/v1/owner/people',headers={'Authorization':'Bearer account-token'})
        self.assertEqual(owner.status_code,403)
    async def test_owner_password_without_account_is_rejected(self):
        response=await self.client.post('/api/v1/pair/create',json={'password':'private-owner-password'})
        self.assertEqual(response.status_code,401);self.mock_store.assert_not_called()
    async def test_wrong_password_is_forbidden_without_account_expiry(self):
        response=await self.create(password='wrong')
        self.assertEqual(response.status_code,403);self.assertIn('şifresi yanlış',response.json()['detail']);self.mock_store.assert_not_called()
    async def test_plain_http_and_forwarded_requests_are_rejected(self):
        for url,headers in [('http://hub.example/api/v1/pair/create',{}),('/api/v1/pair/create',{'X-Forwarded-For':'127.0.0.1'})]:
            response=await self.create(url=url,headers=headers);self.assertEqual(response.status_code,403)
        self.mock_store.assert_not_called()
    async def test_default_owner_password_can_create_remote_qr(self):
        self.key.stop()
        with patch('orion.api.security.valid_admin_key',side_effect=lambda value:value=='orion'):
            response=await self.create(password='orion')
        self.assertEqual(response.status_code,200,response.text);self.assertIn('qr_png',response.json())
    async def test_rate_limit_blocks_creation(self):
        self.app.state.redis.eval.return_value=[6,25]
        response=await self.create();self.assertEqual(response.status_code,429);self.assertEqual(response.headers['retry-after'],'25');self.mock_store.assert_not_called()

if __name__=='__main__':unittest.main()
