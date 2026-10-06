import unittest
from unittest.mock import AsyncMock, patch
import httpx
from orion.api.main import local_app

class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def register(self, peer='127.0.0.1', headers=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=(peer, 4321)), base_url='http://localhost') as client:
            return await client.post('/api/v1/auth/register', json={'username':'new-local-user', 'password':'test-secret'}, headers=headers)

    async def asyncSetUp(self):
        self.limit = patch('orion.api.auth_routes.limit_attempts', AsyncMock())
        self.limit.start()
        self.create = patch('orion.kernel.registry.create_local_user', AsyncMock(return_value=True))
        self.mock_create = self.create.start()
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self):
        self.create.stop()
        self.limit.stop()

    async def test_local_account_can_register_without_invitation(self):
        response = await self.register()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn('access_token', response.json())
        self.mock_create.assert_awaited_once()

    async def test_remote_account_still_requires_invitation(self):
        response = await self.register(peer='192.168.1.99')
        self.assertEqual(response.status_code, 403, response.text)
        self.mock_create.assert_not_awaited()

    async def test_forwarded_local_request_is_not_local_registration(self):
        response = await self.register(headers={'X-Forwarded-For':'127.0.0.1'})
        self.assertEqual(response.status_code, 403, response.text)
        self.mock_create.assert_not_awaited()

    async def test_duplicate_does_not_issue_account_token(self):
        self.mock_create.return_value = False
        response = await self.register()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn('access_token', response.json())

    async def test_database_unavailable_returns_retryable_error(self):
        self.mock_create.side_effect = RuntimeError('Database unavailable')
        self.assertEqual((await self.register()).status_code, 503)
