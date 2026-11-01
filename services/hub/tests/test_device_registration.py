"""A phone paired with a QR from the Hub's PC may open one account, whenever it likes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from orion.api import auth_routes
from orion.kernel.local_security import SecurityStore


def paired_phone(app, device_id):
    async def wrapped(scope, receive, send):
        if scope['type'] == 'http' and device_id:
            scope.setdefault('state', {})['device_id'] = device_id  # As MobileBoundary does after the device token.
        await app(scope, receive, send)
    return wrapped


class DeviceStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SecurityStore(Path(folder.name) / 'devices.sqlite3')

    def pair(self, may_register=True):
        code, _ = self.store.create_pairing()
        return self.store.claim(code, 'Telefon', 'android', '192.168.1.42', may_register)[0]

    def test_one_account_per_qr_pairing_without_a_deadline(self):
        device = self.pair()
        self.assertTrue(self.store.may_register(device))
        self.assertTrue(self.store.take_registration(device))
        self.assertFalse(self.store.take_registration(device))
        self.store.return_registration(device)  # The account could not be created after all.
        self.assertTrue(self.store.may_register(device))

    def test_removed_device_and_local_pairing_have_no_account(self):
        device = self.pair()
        self.store.revoke(device)
        self.assertFalse(self.store.take_registration(device))
        self.assertFalse(self.store.may_register(self.pair(may_register=False)))

    def test_code_lasts_three_minutes(self):
        import time
        _, expires = self.store.create_pairing()
        self.assertAlmostEqual(expires - time.time(), 180, delta=2)


class RegistrationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SecurityStore(Path(folder.name) / 'devices.sqlite3')
        code, _ = self.store.create_pairing()
        self.device = self.store.claim(code, 'Telefon', 'android', '192.168.1.42', True)[0]
        self.app = FastAPI()
        self.app.state.mobile = True
        self.app.include_router(auth_routes.router, prefix='/api/v1')
        self.users = {}
        async def create(name, password_hash):
            if name in self.users:
                return False
            self.users[name] = password_hash
            return True
        for item in (patch('orion.kernel.local_security.SecurityStore', return_value=self.store),
                     patch('orion.kernel.registry.create_local_user', AsyncMock(side_effect=create)),
                     patch.object(auth_routes, 'limit_attempts', AsyncMock()),
                     patch('orion.kernel.router_accounts.provision_if_available', AsyncMock())):
            item.start()
            self.addCleanup(item.stop)

    async def call(self, method, path, device, json=None):
        transport = httpx.ASGITransport(app=paired_phone(self.app, device), client=('192.168.1.42', 5000))
        async with httpx.AsyncClient(transport=transport, base_url='https://192.168.1.10:8443') as client:
            return await client.request(method, '/api/v1/auth/' + path, json=json)

    async def register(self, name, device=None):
        return await self.call('POST', 'register', device or self.device, {'username': name, 'password': 'test-secret'})

    async def test_paired_phone_opens_one_account_later(self):
        self.assertTrue((await self.call('GET', 'registration', self.device)).json()['allowed'])
        self.assertEqual((await self.register('ayse')).status_code, 201)
        second = await self.register('veli')
        self.assertEqual(second.status_code, 403)
        self.assertIn('zaten bir hesap', second.json()['detail'])
        self.assertFalse((await self.call('GET', 'registration', self.device)).json()['allowed'])

    async def test_taken_name_keeps_the_right(self):
        self.users['ayse'] = 'x'
        self.assertEqual((await self.register('ayse')).status_code, 409)
        self.assertEqual((await self.register('ayse2')).status_code, 201)

    async def test_unpaired_client_cannot_register(self):
        response = await self.call('POST', 'register', None, {'username': 'x', 'password': 'test-secret'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse((await self.call('GET', 'registration', None)).json()['allowed'])


if __name__ == '__main__':
    unittest.main()
