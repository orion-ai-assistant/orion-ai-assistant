"""The account's own profile: a display name, a new password, a small picture."""
import base64
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orion.api import auth_routes, security
from orion.kernel import registry

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32


class ProfileRouteTests(unittest.TestCase):
    def setUp(self):
        self.user = {'id': 'alice', 'password_hash': security.hash_password('old'), 'is_active': True,
                     'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc), 'avatar_updated_at': None}
        self.avatar = None
        self.created = []

        self.renamed = []

        async def rename(redis, user, new):
            if new == 'taken':
                raise registry.UsernameTaken(new)
            self.renamed.append((user['id'], new))
            self.user['id'] = new

        async def set_password(username, password_hash):
            self.user['password_hash'] = password_hash
            return True

        async def set_avatar(username, data, mime):
            self.avatar = (mime, data) if data is not None else None
            self.user['avatar_updated_at'] = datetime.now(timezone.utc) if data is not None else None
            return True

        async def create_session(session_id, user_id, device_id, credential, forget_after):
            self.created.append(session_id)

        self.patches = [
            patch.object(auth_routes, 'get_user_by_username', AsyncMock(side_effect=lambda name: dict(self.user))),
            patch('orion.api.services.account_rename.rename_account', AsyncMock(side_effect=rename)),
            patch.object(auth_routes, 'set_user_password', AsyncMock(side_effect=set_password)),
            patch.object(auth_routes, 'set_user_avatar', AsyncMock(side_effect=set_avatar)),
            patch.object(auth_routes, 'get_user_avatar', AsyncMock(side_effect=lambda name: self.avatar)),
            patch.object(auth_routes, 'limit_attempts', AsyncMock()),
            patch.object(registry, 'create_session', AsyncMock(side_effect=create_session)),
            patch.dict(os.environ, {'ADMIN_API_KEY': ''}),
        ]
        for started in self.patches:
            started.start()
        app = FastAPI()
        app.state.mobile = False
        app.state.redis = object()
        app.include_router(auth_routes.router, prefix='/api/v1')
        app.dependency_overrides[auth_routes.get_current_user] = lambda: 'alice'
        self.client = TestClient(app)

    def tearDown(self):
        for started in reversed(self.patches):
            started.stop()

    def test_username_change_checks_the_password_and_the_name(self):
        post = lambda name, pw='old': self.client.post('/api/v1/auth/username',
                                                       json={'new_username': name, 'password': pw})
        self.assertEqual(post('bob', 'wrong').status_code, 400)
        for bad in ('a', 'with space', 'x/y', 'global', 'GLOBAL', 'ü' * 41):
            self.assertEqual(post(bad).status_code, 400, bad)
        self.assertEqual(post('alice').status_code, 400)
        self.assertEqual(post('taken').status_code, 409)
        self.assertEqual(self.renamed, [])

        response = post('  Kürşat.A_1  ')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'Kürşat.A_1')
        self.assertEqual(self.renamed, [('alice', 'Kürşat.A_1')])
        claims = jwt.decode(response.json()['access_token'], options={'verify_signature': False})
        self.assertEqual(claims['sub'], 'Kürşat.A_1')

    def test_password_change_needs_the_current_one_and_returns_a_new_token(self):
        wrong = self.client.post('/api/v1/auth/password', json={'current_password': 'nope', 'new_password': 'new'})
        self.assertEqual(wrong.status_code, 400)
        self.assertTrue(security.verify_password('old', self.user['password_hash']))

        response = self.client.post('/api/v1/auth/password', json={'current_password': 'old', 'new_password': 'new'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(security.verify_password('new', self.user['password_hash']))
        claims = jwt.decode(response.json()['access_token'], options={'verify_signature': False})
        self.assertEqual(claims['sub'], 'alice')
        self.assertEqual([claims['sid']], self.created)

    def test_avatar_round_trip_and_validation(self):
        data = base64.b64encode(PNG).decode()
        response = self.client.put('/api/v1/auth/avatar', json={'mime_type': 'image/png', 'data': data})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()['avatar_version'])
        image = self.client.get('/api/v1/auth/avatar')
        self.assertEqual(image.content, PNG)
        self.assertEqual(image.headers['content-type'], 'image/png')

        # Content must match the type it claims; other types and big files are refused.
        for mime, raw in [('image/jpeg', PNG), ('image/svg+xml', b'<svg/>'),
                          ('image/png', b'\x89PNG\r\n\x1a\n' + b'\0' * (512 * 1024))]:
            bad = self.client.put('/api/v1/auth/avatar',
                                  json={'mime_type': mime, 'data': base64.b64encode(raw).decode()})
            self.assertIn(bad.status_code, (400, 422), mime)
        self.assertEqual(self.avatar, ('image/png', PNG))

        removed = self.client.delete('/api/v1/auth/avatar')
        self.assertIsNone(removed.json()['avatar_version'])
        self.assertEqual(self.client.get('/api/v1/auth/avatar').status_code, 404)


if __name__ == '__main__':
    unittest.main()
