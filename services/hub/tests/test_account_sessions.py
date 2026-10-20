"""Account sessions stay open while used, end when idle or revoked, and are the same on every entry point."""
import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orion.api import auth_routes, routes, security, sessions
from orion.kernel import registry

ENDED = 'Bearer error="invalid_token"'


class FakeSessions:
    """orion_sessions joined with orion_users, as registry.get_session returns it."""

    def __init__(self, users):
        self.users, self.rows, self.touched, self.down = users, {}, [], False

    def guard(self):
        if self.down:
            raise RuntimeError('Session database unavailable')

    async def create(self, session_id, user_id, device_id, credential, forget_after):
        self.guard()
        now = datetime.now(timezone.utc)
        self.rows[session_id] = {'id': session_id, 'user_id': user_id, 'device_id': device_id, 'credential': credential,
                                 'last_seen_at': now, 'revoked_at': None}

    async def get(self, session_id):
        self.guard()
        row = self.rows.get(session_id)
        user = row and self.users.get(row['user_id'])
        return {**row, 'password_hash': user['password_hash'], 'is_active': user['is_active']} if user else None

    async def touch(self, session_id):
        self.guard()
        self.touched.append(session_id)
        self.rows[session_id]['last_seen_at'] = datetime.now(timezone.utc)

    async def revoke(self, *, session_id=None, user_id=None, device_id=None):
        self.guard()
        for row in self.rows.values():
            if session_id in (None, row['id']) and user_id in (None, row['user_id']) and device_id in (None, row['device_id']):
                row['revoked_at'] = datetime.now(timezone.utc)

    def idle_for(self, token, age):
        self.rows[jwt.decode(token, options={'verify_signature': False})['sid']]['last_seen_at'] = datetime.now(timezone.utc) - age


class AccountSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.users = {'alice': {'id': 'alice', 'password_hash': security.hash_password('pw'), 'is_active': True}}
        self.store = FakeSessions(self.users)
        self.patches = [
            patch.object(registry, 'create_session', self.store.create),
            patch.object(registry, 'get_session', self.store.get),
            patch.object(registry, 'touch_session', self.store.touch),
            patch.object(registry, 'revoke_sessions', self.store.revoke),
            patch.object(auth_routes, 'get_user_by_username', AsyncMock(side_effect=lambda name: self.users.get(name))),
            patch.object(auth_routes, 'limit_login', AsyncMock()),
            patch.dict(os.environ, {'ADMIN_API_KEY': ''}),
        ]
        for started in self.patches:
            started.start()
        self.device = None
        self.app = FastAPI()
        self.app.state.mobile = False

        @self.app.middleware('http')
        async def paired_device(request: Request, call_next):
            # Stands in for MobileBoundary: the paired device the request comes from.
            request.state.device_id = self.device
            return await call_next(request)

        self.app.include_router(auth_routes.router, prefix='/api/v1')
        self.app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://hub')

    async def asyncTearDown(self):
        await self.client.aclose()
        for started in reversed(self.patches):
            started.stop()

    async def login(self):
        response = await self.client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'pw'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['access_token']

    async def me(self, token):
        return await self.client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token})

    async def test_login_opens_a_durable_session_without_a_fixed_expiry(self):
        token = await self.login()
        claims = jwt.decode(token, security.JWT_SECRET_KEY, algorithms=[security.ALGORITHM])
        self.assertEqual(claims['sub'], 'alice')
        self.assertNotIn('exp', claims, 'Only disuse or revocation ends a session, never the clock')
        self.assertEqual(self.store.rows[claims['sid']]['credential'], sessions.credential(self.users['alice']['password_hash']))
        self.assertEqual((await self.me(token)).status_code, 200)

    async def test_use_keeps_the_session_open_far_beyond_a_day(self):
        token = await self.login()
        for _ in range(12):  # About four months of use, ten days apart.
            self.store.idle_for(token, timedelta(days=10))
            self.assertEqual((await self.me(token)).status_code, 200)
        self.assertEqual(len(self.store.touched), 12)

    async def test_recent_use_is_not_rewritten_on_every_request(self):
        token = await self.login()
        for _ in range(3):
            self.assertEqual((await self.me(token)).status_code, 200)
        self.assertEqual(self.store.touched, [])

    async def test_fourteen_idle_days_end_the_session(self):
        token = await self.login()
        self.store.idle_for(token, sessions.IDLE + timedelta(minutes=1))
        response = await self.me(token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['www-authenticate'], ENDED)

    async def test_logout_revokes_the_session_on_the_hub(self):
        token = await self.login()
        response = await self.client.post('/api/v1/auth/logout', headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(response.status_code, 200)
        response = await self.me(token)
        self.assertEqual((response.status_code, response.headers['www-authenticate']), (401, ENDED))
        # Logging out again, or with an unusable token, has nothing left to end.
        for headers in ({'Authorization': 'Bearer ' + token}, {'Authorization': 'Bearer garbage'}, {}):
            self.assertEqual((await self.client.post('/api/v1/auth/logout', headers=headers)).status_code, 200)

    async def test_password_change_and_deactivation_end_every_session(self):
        first, second = await self.login(), await self.login()
        self.users['alice']['password_hash'] = security.hash_password('new')
        for token in (first, second):
            response = await self.me(token)
            self.assertEqual((response.status_code, response.headers['www-authenticate']), (401, ENDED))
        self.users['alice']['password_hash'] = security.hash_password('pw')
        token = await self.login()
        self.users['alice']['is_active'] = False
        self.assertEqual((await self.me(token)).headers['www-authenticate'], ENDED)

    async def test_unavailable_database_is_not_an_ended_session(self):
        token = await self.login()
        self.store.down = True
        response = await self.me(token)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('www-authenticate', response.headers)
        response = await self.client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'pw'})
        self.assertEqual(response.status_code, 503)
        self.store.down = False
        self.assertEqual((await self.me(token)).status_code, 200)

    async def test_forged_or_foreign_tokens_are_ended_sessions(self):
        token = await self.login()
        claims = jwt.decode(token, security.JWT_SECRET_KEY, algorithms=[security.ALGORITHM])
        forged = jwt.encode({**claims, 'sid': 'unknown'}, security.JWT_SECRET_KEY, algorithm=security.ALGORITHM)
        other_user = jwt.encode({**claims, 'sub': 'mallory'}, security.JWT_SECRET_KEY, algorithm=security.ALGORITHM)
        unsigned = jwt.encode(claims, 'another-secret-key-of-sufficient-length', algorithm=security.ALGORITHM)
        for bad in (forged, other_user, unsigned):
            self.assertEqual((await self.me(bad)).headers.get('www-authenticate'), ENDED)

    async def test_missing_credentials_are_not_reported_as_an_ended_session(self):
        response = await self.client.get('/api/v1/auth/me')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('invalid_token', response.headers.get('www-authenticate', ''))
        response = await self.client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'wrong'})
        self.assertNotIn('invalid_token', response.headers.get('www-authenticate', ''))

    async def test_pre_update_token_still_works_and_is_exchanged_for_a_session(self):
        legacy = security.create_access_token({'sub': 'alice'})
        self.assertEqual((await self.me(legacy)).status_code, 200)
        response = await self.client.post('/api/v1/auth/session', headers={'Authorization': 'Bearer ' + legacy})
        self.assertEqual(response.status_code, 200)
        exchanged = response.json()['access_token']
        self.assertIn(jwt.decode(exchanged, options={'verify_signature': False})['sid'], self.store.rows)
        self.assertEqual((await self.me(exchanged)).status_code, 200)
        # A session token is already current: the exchange returns it unchanged.
        again = await self.client.post('/api/v1/auth/session', headers={'Authorization': 'Bearer ' + exchanged})
        self.assertEqual(again.json()['access_token'], exchanged)
        self.assertEqual(len(self.store.rows), 1)

    async def test_pre_update_tokens_end_at_their_own_expiry(self):
        expired = security.create_access_token({'sub': 'alice'}, timedelta(seconds=-1))
        never_expiring = jwt.encode({'sub': 'alice'}, security.JWT_SECRET_KEY, algorithm=security.ALGORITHM)
        for token in (expired, never_expiring):
            self.assertEqual((await self.me(token)).headers.get('www-authenticate'), ENDED)
            response = await self.client.post('/api/v1/auth/session', headers={'Authorization': 'Bearer ' + token})
            self.assertEqual(response.status_code, 401)

    async def test_phone_sessions_belong_to_their_paired_device(self):
        self.app.state.mobile = True
        self.device = 'phone-a'
        token = await self.login()
        self.assertEqual(self.store.rows[jwt.decode(token, options={'verify_signature': False})['sid']]['device_id'], 'phone-a')
        self.assertEqual((await self.me(token)).status_code, 200)
        self.device = 'phone-b'
        self.assertEqual((await self.me(token)).headers.get('www-authenticate'), ENDED)
        self.device = 'phone-a'
        await sessions.revoke_device('phone-a')
        self.assertEqual((await self.me(token)).headers.get('www-authenticate'), ENDED)

    async def test_websocket_follows_the_same_session_rules(self):
        token = await self.login()
        client = TestClient(self.app)
        path = '/api/v1/audio/transcriptions/stream?token='
        self.store.down = True
        with self.assertRaises(WebSocketDisconnect) as unavailable:
            with client.websocket_connect(path + token):
                pass
        self.assertEqual(unavailable.exception.code, 1013)
        self.store.down = False
        await sessions.revoke(token)
        with self.assertRaises(WebSocketDisconnect) as revoked:
            with client.websocket_connect(path + token):
                pass
        self.assertEqual(revoked.exception.code, 1008)


class LogRedactionTests(unittest.TestCase):
    def test_query_tokens_never_reach_server_logs(self):
        record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                                   ('127.0.0.1:1', 'GET', '/api/v1/chat/stream?token=secret.jwt&x=1', '1.1', 200), None)
        sessions.RedactQuerySecrets().filter(record)
        self.assertNotIn('secret', record.getMessage())
        self.assertIn('token=***&x=1', record.getMessage())
        socket = logging.LogRecord('uvicorn.error', logging.INFO, '', 0, '%s - "WebSocket %s" [accepted]',
                                   ('127.0.0.1:1', '/api/v1/audio/transcriptions/stream?language=tr&token=secret'), None)
        sessions.RedactQuerySecrets().filter(socket)
        self.assertNotIn('secret', socket.getMessage())


@unittest.skipUnless(os.getenv('ORION_LIVE_ACCESS_TESTS') == '1', 'opt-in isolated live PostgreSQL')
class LiveSessionStorage(unittest.IsolatedAsyncioTestCase):
    """The SQL behind the session rules, in an isolated schema."""

    async def asyncSetUp(self):
        import asyncpg
        from services.shared.environment import get_postgres_url
        self.schema = 'orion_session_test_' + uuid4().hex
        self.dsn = get_postgres_url()
        self.admin = await asyncpg.connect(self.dsn)
        await self.admin.execute('create schema ' + self.schema)

        async def connect(*_, **__):
            return await asyncpg.connect(self.dsn, server_settings={'search_path': self.schema})
        self.connect = patch.object(registry, '_connect', side_effect=connect)
        self.connect.start()

    async def asyncTearDown(self):
        self.connect.stop()
        await self.admin.execute(f'drop schema {self.schema} cascade')
        await self.admin.close()

    async def test_session_rows_follow_use_revocation_and_deactivation(self):
        await registry.create_local_user('alice', 'hash-1')
        await registry.create_session('s1', 'alice', None, 'cred', forget_after=timedelta(days=28))
        await registry.create_session('s2', 'alice', 'phone', 'cred', forget_after=timedelta(days=28))
        row = await registry.get_session('s1')
        self.assertEqual((row['user_id'], row['password_hash'], row['is_active'], row['revoked_at']), ('alice', 'hash-1', True, None))
        conn = await registry._connect()
        try:
            await conn.execute("update orion_sessions set last_seen_at = now() - interval '1 day' where id = 's1'")
        finally:
            await conn.close()
        before = (await registry.get_session('s1'))['last_seen_at']
        await registry.touch_session('s1')
        self.assertGreater((await registry.get_session('s1'))['last_seen_at'], before)
        await registry.revoke_sessions(device_id='phone')
        self.assertIsNotNone((await registry.get_session('s2'))['revoked_at'])
        self.assertIsNone((await registry.get_session('s1'))['revoked_at'])
        await registry.set_user_active('alice', False)
        self.assertIsNotNone((await registry.get_session('s1'))['revoked_at'])
        await registry.set_user_active('alice', True)
        self.assertIsNotNone((await registry.get_session('s1'))['revoked_at'], 'Reactivation must not revive sessions')

    async def test_long_unused_and_revoked_rows_are_forgotten(self):
        await registry.create_local_user('alice', 'hash-1')
        await registry.create_session('old', 'alice', None, 'cred', forget_after=timedelta(days=28))
        conn = await registry._connect()
        try:
            await conn.execute("update orion_sessions set last_seen_at = now() - interval '29 days' where id = 'old'")
        finally:
            await conn.close()
        await registry.create_session('new', 'alice', None, 'cred', forget_after=timedelta(days=28))
        self.assertIsNone(await registry.get_session('old'))
        self.assertIsNotNone(await registry.get_session('new'))


if __name__ == '__main__':
    unittest.main()
