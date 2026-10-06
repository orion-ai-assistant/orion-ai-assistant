import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orion.api import invitations, owner_routes, auth_routes
from orion.api.main import create_app
from orion.api.access_boundary import OWNER_COOKIE


class Redis:
    def __init__(self):
        self.values, self.counts = {}, {}
        self.remaining = 30
    async def eval(self, script, keys, key, ttl):
        assert ttl == 30
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], self.remaining]
    async def set(self, key, value, ex):
        self.values[key] = value
    async def get(self, key):
        return self.values.get(key)
    async def delete(self, key):
        self.values.pop(key, None)


class OwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {'ADMIN_API_KEY': 'owner-password'})
        self.env.start()
        self.app = create_app()
        self.app.state.redis = self.redis = Redis()
        self.user_app = create_app(public=True)
        self.user_app.state.redis = self.redis
        self.user_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.user_app, client=('127.0.0.1', 4567)), base_url='https://localhost:8910')
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 4567)), base_url='https://localhost:8909')
    async def asyncTearDown(self):
        await self.client.aclose()
        await self.user_client.aclose()
        self.env.stop()
    async def login(self, scope='people', password='owner-password'):
        return await self.client.post('/api/v1/owner/login', json={'password': password, 'scope': scope})

    async def test_private_port_rejects_flutter_apis_even_with_valid_user_or_admin(self):
        from orion.api.security import create_access_token
        token = create_access_token({'sub': 'alice'})
        await self.login(scope='admin')
        lookup = AsyncMock(return_value={'id': 'alice', 'is_active': True})
        with patch.object(auth_routes, 'get_user_by_username', lookup):
            for headers in ({}, {'Authorization': 'Bearer ' + token}, {'X-Admin-Key': 'owner-password'}):
                for method, path in [('POST', '/api/v1/auth/login'), ('POST', '/api/v1/auth/register'), ('GET', '/api/v1/auth/me'), ('GET', '/api/v1/chats'), ('POST', '/api/v1/chats/messages'), ('GET', '/api/v1/chat/stream'), ('GET', '/api/v1/models'), ('GET', '/api/v1/tools')]:
                    response = await self.client.request(method, path, headers=headers)
                    self.assertEqual(response.status_code, 403, path)
                    self.assertIn('8910', response.json()['detail'])
            lookup.assert_not_awaited()
            response = await self.user_client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token})
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get('/health')).status_code, 200)
        client = TestClient(self.app)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect('/api/v1/audio/transcriptions/stream?token=' + token):
                pass

    async def test_private_shared_settings_need_admin_but_public_accepts_user(self):
        from orion.api.security import create_access_token
        headers = {'Authorization': 'Bearer ' + create_access_token({'sub': 'alice'})}
        lookup = AsyncMock(return_value={'id': 'alice', 'is_active': True})
        with patch.object(auth_routes, 'get_user_by_username', lookup):
            self.assertEqual((await self.client.get('/api/v1/admin/settings/schema', headers=headers)).status_code, 401)
            self.assertEqual((await self.user_client.get('/api/v1/admin/settings/schema', headers=headers)).status_code, 200)
            await self.login(scope='admin')
            self.assertEqual((await self.client.get('/api/v1/admin/settings/schema')).status_code, 200)

    async def test_private_dashboard_redirects_to_public_port_and_owner_assets_stay_local(self):
        with patch.dict(os.environ, {'ORION_PUBLIC_PORT': '8910'}):
            for path in ['/dashboard', '/dashboard/', '/dashboard/js/main.js?version=1']:
                result = await self.client.get(path)
                self.assertEqual(result.status_code, 307)
                self.assertEqual(result.headers['location'], 'http://127.0.0.1:8910' + ('/js/main.js?version=1' if 'js/' in path else '/hub'))
            page = await self.client.get('/')
            self.assertIn('href="http://127.0.0.1:8910/hub"', page.text)
        self.assertEqual((await self.client.get('/owner-assets/auth_wait.js')).status_code, 200)
        page = await self.client.get('/manage/')
        self.assertIn('/owner-assets/auth_wait.js', page.text)

    async def test_invitation_without_address_uses_current_pc_ip(self):
        await self.login()
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.getsockname.return_value = ('192.168.1.102', 1234)
        with patch.dict(os.environ, {'ORION_PUBLIC_URL': '', 'ORION_PUBLIC_PORT': '8910'}), patch.object(owner_routes.os.path, 'exists', return_value=False), patch.object(owner_routes.socket, 'socket', return_value=probe), patch.object(invitations, 'create_invitation', AsyncMock()):
            response = await self.client.post('/api/v1/owner/invitations', json={'label': 'Phone'})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['url'].startswith('https://192.168.1.102:8911/dashboard/#invite='))
        probe.send.assert_not_called()

    def test_configured_tunnel_address_takes_precedence(self):
        with patch.dict(os.environ, {'ORION_PUBLIC_URL': 'https://orion.example/'}), patch.object(owner_routes.socket, 'socket') as probe:
            self.assertEqual(owner_routes.invitation_address(), 'https://orion.example')
            probe.assert_not_called()

    def test_no_network_does_not_generate_localhost_invitation(self):
        with patch.dict(os.environ, {'ORION_PUBLIC_URL': ''}), patch.object(owner_routes.os.path, 'exists', return_value=False), patch.object(owner_routes.socket, 'socket', side_effect=OSError), patch.object(owner_routes.socket, 'getaddrinfo', return_value=[(0,0,0,'',('127.0.0.1',0))]):
            with self.assertRaises(HTTPException) as failure:
                owner_routes.invitation_address()
        self.assertEqual(failure.exception.status_code, 503)

    async def test_owner_password_required_and_shared_with_admin(self):
        self.assertEqual((await self.client.get('/api/v1/owner/people')).status_code, 401)
        self.assertEqual((await self.login(password='wrong')).status_code, 401)
        response = await self.login()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('owner-password', response.text)
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=strict', cookie)
        self.assertIn(OWNER_COOKIE, cookie)
        with patch.object(invitations, 'list_people', AsyncMock(return_value=[])), patch.object(invitations, 'list_invitations', AsyncMock(return_value=[])):
            self.assertEqual((await self.client.get('/api/v1/owner/people')).status_code, 200)
        # People session cannot perform technical administrator operations.
        self.assertEqual((await self.client.get('/api/v1/admin/chats')).status_code, 401)
        self.assertEqual((await self.login(scope='admin')).status_code, 200)
        with patch('orion.api.routes.get_all_chats_admin', AsyncMock(return_value=[])):
            self.assertEqual((await self.client.get('/api/v1/admin/chats')).status_code, 200)

    async def test_tab_session_requires_its_header_and_expires(self):
        response = await self.client.post('/api/v1/owner/login', json={
            'password': 'owner-password', 'scope': 'admin', 'tab_session': True})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(response.json()['expires_in'], 900)
        token = response.json()['token']
        headers = {'X-Orion-Owner-Session': token}
        self.assertEqual((await self.client.get('/api/v1/owner/session')).status_code, 401)
        self.assertEqual((await self.client.get('/api/v1/owner/session', headers=headers)).status_code, 200)
        self.client.cookies.set(OWNER_COOKIE, token)
        self.assertEqual((await self.client.get('/api/v1/owner/session')).status_code, 401)
        await self.redis.delete('auth:owner:' + hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual((await self.client.get('/api/v1/owner/session', headers=headers)).status_code, 401)

    async def test_owner_wrong_password_countdown_uses_remaining_ttl(self):
        for _ in range(5):
            self.assertEqual((await self.login(password='wrong')).status_code, 401)
        self.redis.remaining = 17
        response = await self.login(password='wrong')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '17')
        self.redis.counts.clear()
        self.assertEqual((await self.login()).status_code, 200)

    async def test_admin_cookie_does_not_override_dashboard_bearer_identity(self):
        from orion.api.security import create_access_token
        await self.login(scope='admin')
        self.user_client.cookies.update(self.client.cookies)
        with patch.object(auth_routes, 'get_user_by_username', AsyncMock(return_value={'id': 'alice', 'is_active': True})):
            response = await self.user_client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + create_access_token({'sub': 'alice'})})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'alice')
        self.assertEqual((await self.client.get('/api/v1/chats')).status_code, 403)

    async def test_raw_wrong_admin_keys_share_owner_limit(self):
        for _ in range(5):
            self.assertEqual((await self.client.get('/api/v1/admin/chats', headers={'X-Admin-Key': 'wrong'})).status_code, 401)
        self.assertEqual((await self.login(password='wrong')).status_code, 429)

    async def test_sufficiently_long_registration_still_requires_invitation(self):
        with patch.object(auth_routes, 'get_user_by_username', AsyncMock()) as lookup:
            for _ in range(10):
                response = await self.user_client.post('/api/v1/auth/register', json={'username': 'new', 'password': 'password'})
                self.assertEqual(response.status_code, 403)
            response = await self.user_client.post('/api/v1/auth/register', json={'username': 'new', 'password': 'password'})
        self.assertEqual(response.status_code, 429)
        lookup.assert_not_awaited()

    async def test_logout_expiry_and_password_rotation_revoke_session(self):
        await self.login()
        with patch.dict(os.environ, {'ADMIN_API_KEY': 'rotated'}):
            self.assertEqual((await self.client.get('/api/v1/owner/people')).status_code, 401)
        self.redis.values.clear()
        self.assertEqual((await self.client.get('/api/v1/owner/people')).status_code, 401)
        await self.login()
        self.assertEqual((await self.client.post('/api/v1/owner/logout')).status_code, 200)
        self.assertFalse(self.redis.values)
        self.assertEqual((await self.client.get('/api/v1/owner/people')).status_code, 401)

    async def test_external_and_forwarded_owner_requests_denied(self):
        for peer, host, headers in [('192.168.1.8', 'localhost:8909', {}), ('127.0.0.1', 'public.example', {}), ('127.0.0.1', 'localhost:8909', {'X-Forwarded-For': '1.2.3.4'})]:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=(peer, 1234)), base_url='https://' + host) as client:
                for path in ['/', '/manage/', '/admin/', '/api/v1/owner/people', '/api/v1/admin/chats']:
                    self.assertEqual((await client.get(path, headers=headers)).status_code, 403, path)

    async def test_csrf_requests_rejected_even_with_valid_owner_session(self):
        await self.login()
        for origin in ['https://evil.example', 'http://localhost:7777', 'null']:
            response = await self.client.post('/api/v1/owner/invitations', headers={'Origin': origin}, json={'public_url': 'https://orion.example'})
            self.assertEqual(response.status_code, 403)

    async def test_invitation_link_qr_and_only_hash_storage(self):
        await self.login()
        with patch.object(invitations, 'create_invitation', AsyncMock()) as create:
            response = await self.client.post('/api/v1/owner/invitations', json={'public_url': 'https://orion.example', 'label': '<script>bad</script>', 'expires_minutes': 30})
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['url'].startswith('https://orion.example/dashboard/#invite='))
        token = data['url'].split('=', 1)[1]
        self.assertGreaterEqual(len(token), 40)
        self.assertIn('<svg', data['qr_svg'])
        self.assertNotIn('owner-password', data['url'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        conn = Mock(execute=AsyncMock(), close=AsyncMock())
        with patch.object(invitations, 'connection', AsyncMock(return_value=conn)), patch.object(invitations, '_ensure_tables', AsyncMock()):
            await invitations.create_invitation('id', token, 'label', datetime.now(timezone.utc))
        self.assertEqual(conn.execute.call_args.args[2], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, str(conn.execute.call_args))

    async def test_bad_share_addresses_and_unauthenticated_creation_rejected(self):
        response = await self.client.post('/api/v1/owner/invitations', json={'public_url': 'https://example.com'})
        self.assertEqual(response.status_code, 401)
        await self.login()
        for url in ['https://localhost:8910', 'javascript:alert(1)', 'https://secret@example.com', 'https://example.com/?x=1', 'http://127.0.0.1', 'https://example.com/dashboard']:
            response = await self.client.post('/api/v1/owner/invitations', json={'public_url': url})
            self.assertEqual(response.status_code, 422, url)

    async def test_existing_users_can_be_approved_and_disabled(self):
        await self.login()
        with patch.object(owner_routes, 'set_user_active', AsyncMock(return_value=True)) as set_active:
            for value in [True, False]:
                response = await self.client.put('/api/v1/owner/people/alice', json={'is_active': value})
                self.assertEqual(response.status_code, 200)
                set_active.assert_awaited_with('alice', value)

    async def test_no_invite_cannot_create_account_valid_invite_returns_login_token(self):
        with patch.object(auth_routes, 'get_user_by_username', AsyncMock()) as lookup:
            response = await self.user_client.post('/api/v1/auth/register', json={'username': 'alice', 'password': 'password'})
        self.assertEqual(response.status_code, 403)
        lookup.assert_not_awaited()
        with patch.object(invitations, 'preview_invitation', AsyncMock(return_value={})), patch.object(invitations, 'redeem_invitation', AsyncMock()) as redeem:
            response = await self.user_client.post('/api/v1/auth/register', json={'username': 'alice', 'password': 'password', 'invitation_token': 'a' * 43})
        self.assertEqual(response.status_code, 201)
        self.assertIn('access_token', response.json())
        self.assertEqual(redeem.call_args.args[:2], ('a' * 43, 'alice'))

    async def test_invalid_revoked_or_expired_invite_does_not_hash_or_create_user(self):
        with patch.object(invitations, 'preview_invitation', AsyncMock(side_effect=HTTPException(410, 'invalid'))), patch.object(auth_routes, 'hash_password') as hashing:
            response = await self.user_client.post('/api/v1/auth/register', json={'username': 'alice', 'password': 'password', 'invitation_token': 'a' * 43})
        self.assertEqual(response.status_code, 410)
        hashing.assert_not_called()

    async def test_public_listener_blocks_owner_even_when_tunnel_is_loopback(self):
        app = create_app(public=True)
        app.state.redis = self.redis
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)), base_url='https://localhost:8910') as client:
            home = await client.get('/')
            self.assertEqual(home.status_code, 200)
            self.assertNotIn('location', home.headers)
            for path in ['/manage/', '/admin/', '/owner-assets/people.js', '/api/v1/owner/people', '/api/v1/auth/users/alice/approval', '/api/v1/admin/chats', '/docs']:
                self.assertEqual((await client.get(path)).status_code, 404 if path == '/docs' else 403, path)
            self.assertEqual((await client.get('/api/v1/browser')).status_code, 401)
            legacy = await client.get('/dashboard/')
            self.assertEqual(legacy.status_code, 307)
            self.assertEqual(legacy.headers['location'], '/')
            favicon = await client.get('/favicon.ico')
            self.assertEqual(favicon.status_code, 200)
            self.assertIn('image/svg+xml', favicon.headers['content-type'])
            self.assertEqual((await client.get('/health')).status_code, 200)
            for path in ['/api/v1/chats?admin_key=owner-password', '/api/v1/chats?%61dmin_key=owner-password']:
                self.assertEqual((await client.get(path)).status_code, 403)
            self.assertEqual((await client.get('/api/v1/chats', headers={'X-Admin-Key': 'owner-password'})).status_code, 403)
            self.assertEqual((await client.get('/api/v1/admin/settings/schema')).status_code, 401)
        client = TestClient(app)
        try:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/api/v1/owner/whatever'):
                    pass
        finally:
            client.close()


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    def conn(self, row, result='INSERT 0 1'):
        conn = Mock(fetchrow=AsyncMock(return_value=row), execute=AsyncMock(return_value=result), close=AsyncMock())
        transaction = Mock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        conn.transaction.return_value = transaction
        return conn, transaction

    async def test_redemption_locks_invite_and_atomically_creates_active_account(self):
        conn, tx = self.conn({'id': 'id', 'expires_at': datetime.now(timezone.utc) + timedelta(days=1), 'used_by': None, 'revoked': False})
        with patch.object(invitations, 'connection', AsyncMock(return_value=conn)), patch.object(invitations, '_ensure_tables', AsyncMock()):
            await invitations.redeem_invitation('token', 'alice', 'hash')
        self.assertIn('for update', conn.fetchrow.call_args.args[0])
        self.assertIn('true', conn.execute.call_args_list[0].args[0])
        self.assertIn('used_by', conn.execute.call_args_list[1].args[0])
        tx.__aenter__.assert_awaited_once()
        tx.__aexit__.assert_awaited_once()

    async def test_reused_revoked_expired_and_duplicate_username_roll_back(self):
        row = {'id': 'id', 'expires_at': datetime.now(timezone.utc) + timedelta(days=1), 'used_by': None, 'revoked': False}
        for changed, result, status in [({'used_by': 'bob'}, 'INSERT 0 1', 410), ({'revoked': True}, 'INSERT 0 1', 410), ({'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}, 'INSERT 0 1', 410), ({}, 'INSERT 0 0', 409)]:
            conn, tx = self.conn({**row, **changed}, result)
            with patch.object(invitations, 'connection', AsyncMock(return_value=conn)), patch.object(invitations, '_ensure_tables', AsyncMock()):
                with self.assertRaises(HTTPException) as failure:
                    await invitations.redeem_invitation('token', 'alice', 'hash')
            self.assertEqual(failure.exception.status_code, status)
            self.assertFalse(any('set used_by' in call.args[0] for call in conn.execute.call_args_list))
            self.assertIs(tx.__aexit__.call_args.args[0], HTTPException)
