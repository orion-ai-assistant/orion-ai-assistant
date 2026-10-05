"""Pairing persistence/races and TLS identity lifecycle security regressions."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock

from orion.kernel.local_security import SecurityStore, tls_identity, security_dir


class HubLocalSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SecurityStore(Path(self.directory.name) / 'security.sqlite3')

    def tearDown(self):
        self.directory.cleanup()

    def test_atomic_single_use_across_independent_stores(self):
        code, _ = self.store.create_pairing()
        def claim(index):
            try:
                return SecurityStore(self.store.path).claim(code, str(index), 'test', str(index))
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=8) as threads:
            results = list(threads.map(claim, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_wrong_code_does_not_consume_session(self):
        code, _ = self.store.create_pairing()
        with self.assertRaises(ValueError):
            self.store.claim('AAAAAAAA', 'phone', None, 'peer')
        device, token = self.store.claim(code, 'phone', None, 'peer')
        self.assertEqual(self.store.authenticate(token), device)
        self.assertNotIn(token, self.store.path.read_bytes().decode('latin1'))

    def test_expired_code_is_unusable(self):
        code, _ = self.store.create_pairing(ttl=-1)
        with self.assertRaises(ValueError):
            self.store.claim(code, 'phone', None, 'peer')

    def test_revoke_invalidates_token_persistently(self):
        code, _ = self.store.create_pairing()
        device, token = self.store.claim(code, 'phone', None, 'peer')
        self.store.revoke(device)
        fresh = SecurityStore(self.store.path)
        self.assertIsNone(fresh.authenticate(token))
        self.assertFalse(fresh.active(device))

    def test_rate_limit_preserves_valid_code(self):
        code, _ = self.store.create_pairing()
        for _ in range(10):
            with self.assertRaises(ValueError):
                self.store.claim('AAAAAAAA', 'phone', None, 'peer')
        with self.assertRaises(PermissionError):
            self.store.claim(code, 'phone', None, 'peer')
        self.assertIsNotNone(self.store.claim(code, 'phone', None, 'another-peer'))

    def test_restart_and_certificate_renewal_preserve_public_key(self):
        original = tls_identity(self.directory.name)
        restarted = tls_identity(self.directory.name)
        renewed = tls_identity(self.directory.name, renew=True)
        self.assertEqual(original['id'], renewed['id'])
        self.assertEqual(original['fp'], restarted['fp'])
        self.assertEqual(original['fp'], renewed['fp'])

    def test_corrupt_key_fails_closed_without_replacement(self):
        original = tls_identity(self.directory.name)
        key = Path(original['key'])
        key.write_bytes(b'invalid key')
        with self.assertRaises(ValueError):
            tls_identity(self.directory.name)
        self.assertEqual(key.read_bytes(), b'invalid key')

    def test_discovery_cannot_resurrect_removed_trust(self):
        router = {'id': 'router-id', 'name': 'Router', 'fp': 'a' * 64}
        self.store.trust_router(router, 'https://192.168.1.2:9443')
        self.store.remove_router(router['id'])
        self.store.update_router_address(router['id'], router['fp'], 'https://192.168.1.3:9443')
        self.assertEqual(self.store.routers(), [])

    def test_explicit_security_path_is_independent_of_container_source_depth(self):
        with patch.dict('os.environ', {'ORION_SECURITY_DIR': self.directory.name}), patch('orion.kernel.local_security.__file__', '/app/src/orion/kernel/local_security.py'):
            self.assertEqual(security_dir(), Path(self.directory.name))


class MobileAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from fastapi import FastAPI, Depends
        from orion.api.auth_routes import get_current_user, router as auth_router
        from orion.api.mobile_security import MobileBoundary, LegacyDeviceBoundary, mobile_router
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict('os.environ', {'ORION_SECURITY_DIR': self.directory.name})
        self.environment.start()
        self.store = SecurityStore()
        code, _ = self.store.create_pairing()
        self.device, self.token = self.store.claim(code, 'test', None, 'peer')
        self.account = patch('orion.api.auth_routes.require_active_user', AsyncMock())
        self.account.start()
        self.mobile = FastAPI()
        self.mobile.state.mobile = True
        self.mobile.include_router(mobile_router)
        self.mobile.include_router(auth_router, prefix='/api/v1')
        @self.mobile.get('/api/v1/chats')
        async def chats(user=Depends(get_current_user)):
            return {'user': user}
        self.mobile.add_middleware(MobileBoundary)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.mobile), base_url='https://hub.test')
        self.legacy = FastAPI()
        self.legacy.state.mobile = False
        @self.legacy.get('/api/v1/chats')
        async def legacy_chats(user=Depends(get_current_user)):
            return {'user': user}
        self.legacy.add_middleware(LegacyDeviceBoundary)
        self.legacy_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.legacy), base_url='http://hub.test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.legacy_client.aclose()
        self.account.stop(); self.environment.stop()
        self.directory.cleanup()

    def jwt(self, **claims):
        from orion.api.security import create_access_token
        return create_access_token({'sub': 'alice', **claims})

    async def test_device_and_account_authority_both_required(self):
        headers = {'X-Device-Token': self.token}
        self.assertEqual((await self.client.get('/api/v1/chats', headers=headers)).status_code, 401)
        headers['Authorization'] = 'Bearer ' + self.jwt()
        self.assertEqual((await self.client.get('/api/v1/chats', headers=headers)).status_code, 401)
        headers['Authorization'] = 'Bearer ' + self.jwt(device_id='another-device')
        self.assertEqual((await self.client.get('/api/v1/chats', headers=headers)).status_code, 401)
        headers['Authorization'] = 'Bearer ' + self.jwt(device_id=self.device)
        response = await self.client.get('/api/v1/chats', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user'], 'alice')

    async def test_mobile_rejects_administrator_key_in_headers_and_query(self):
        headers = {'X-Device-Token': self.token, 'X-Admin-Key': 'configured-admin'}
        self.assertEqual((await self.client.get('/api/v1/chats', headers=headers)).status_code, 403)
        self.assertEqual((await self.client.get('/api/v1/chats?admin_key=configured-admin', headers={'X-Device-Token': self.token})).status_code, 403)

    async def test_plain_http_cannot_forge_mobile_tls_scheme(self):
        response = await self.client.get('http://hub.test/api/v1/chats', headers={'X-Device-Token': self.token, 'X-Forwarded-Proto': 'https'})
        self.assertEqual(response.status_code, 403)

    async def test_legacy_rejects_device_token_and_bound_account_jwt(self):
        self.assertEqual((await self.legacy_client.get('/api/v1/chats', headers={'X-Device-Token': self.token})).status_code, 401)
        self.assertEqual((await self.legacy_client.get('/api/v1/chats', headers={'Authorization': 'Bearer ' + self.jwt(device_id=self.device)})).status_code, 401)
        self.assertEqual((await self.legacy_client.get('/api/v1/chats', headers={'Authorization': 'Bearer ' + self.jwt()})).status_code, 200)

    async def test_mobile_login_binds_account_jwt_to_paired_device(self):
        import jwt
        from orion.api.security import JWT_SECRET_KEY, ALGORITHM
        with patch('orion.api.auth_routes.limit_login', AsyncMock()), patch('orion.api.auth_routes.get_user_by_username', AsyncMock(return_value={'id': 'alice', 'is_active': True, 'password_hash': 'test'})), patch('orion.api.auth_routes.verify_password', return_value=True):
            response = await self.client.post('/api/v1/auth/login', headers={'X-Device-Token': self.token}, json={'username': 'alice', 'password': 'test'})
        self.assertEqual(response.status_code, 200)
        claims = jwt.decode(response.json()['access_token'], JWT_SECRET_KEY, algorithms=[ALGORITHM])
        self.assertEqual(claims['device_id'], self.device)

    async def test_device_can_revoke_only_itself(self):
        response = await self.client.delete('/api/v1/devices/self', headers={'X-Device-Token': self.token})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.authenticate(self.token))
        self.assertEqual((await self.client.delete('/api/v1/devices/self', headers={'X-Device-Token': self.token})).status_code, 401)


if __name__ == '__main__':
    unittest.main()
