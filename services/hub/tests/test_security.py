import asyncio
import contextlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orion.api import auth_routes as auth, routes, security
from orion.api.cors import configure_cors
from orion.kernel import registry
from orion.contracts.settings import RuntimeSettings


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.users = {'alice': {'id': 'alice', 'is_active': True}, 'bob': {'id': 'bob', 'is_active': True}}
        async def lookup(name):
            return self.users.get(name)
        self.lookup = patch.object(auth, 'get_user_by_username', side_effect=lookup)
        self.lookup.start()
        # Keep the developer's own owner password (files/security) out of the test.
        self.security_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'ADMIN_API_KEY': '', 'ORION_SECURITY_DIR': self.security_dir.name})
        self.env.start()
        self.redis = Mock()
        self.redis.lock = Mock(side_effect=lambda *args, **kwargs: contextlib.nullcontext())
        self.redis.eval = AsyncMock(return_value=[1, 30])
        self.redis.hgetall = AsyncMock(return_value={'user_id': 'alice', 'chat_id': 'a'})
        self.redis.ping = AsyncMock(return_value=True)
        self.app = FastAPI()
        self.app.state.redis = self.redis
        self.app.include_router(auth.router, prefix='/api/v1')
        self.app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://hub')
        self.headers = {'Authorization': 'Bearer ' + security.create_access_token({'sub': 'bob'})}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.lookup.stop()
        self.env.stop()
        self.security_dir.cleanup()

    async def test_empty_admin_key_never_grants_admin_or_global_access(self):
        for headers in ({}, {'X-Admin-Key': 'random'}):
            response = await self.client.get('/api/v1/admin/chats', headers=headers)
            self.assertEqual(response.status_code, 401)
        for path in ('/api/v1/chats?admin_key=random', '/api/v1/admin/settings/schema?admin_key=random'):
            self.assertEqual((await self.client.get(path)).status_code, 401)
        with patch.dict(os.environ, {'ADMIN_API_KEY': 'configured'}):
            with patch.object(routes, 'get_all_chats_admin', AsyncMock(return_value=[])):
                self.assertEqual((await self.client.get('/api/v1/admin/chats', headers={'X-Admin-Key': 'configured'})).status_code, 200)
                self.assertEqual((await self.client.get('/api/v1/admin/chats', headers={'X-Admin-Key': 'random'})).status_code, 401)

    async def test_unicode_admin_key_is_rejected(self):
        with patch.dict(os.environ, {'ADMIN_API_KEY':'owner'}):
            response = await self.client.get('/api/v1/chats?admin_key=%C5%9Fifre')
            self.assertEqual(response.status_code, 401)

    async def test_approval_database_outage_is_controlled(self):
        with patch.dict(os.environ, {'ADMIN_API_KEY':'owner'}), patch.object(auth, 'set_user_active', AsyncMock(side_effect=RuntimeError('db down'))):
            response = await self.client.put('/api/v1/auth/users/bob/approval', json={}, headers={'X-Admin-Key':'owner'})
            self.assertEqual(response.status_code, 503)

    async def test_lifespan_rejects_remote_secret_before_resource_startup(self):
        from orion.api.main import lifespan
        with patch.object(security, 'JWT_SECRET_KEY', security.DEFAULT_JWT_SECRET), patch.dict(os.environ, {'ORION_DEPLOYMENT_MODE':'remote'}), patch.object(security.sys, 'argv', []), patch('orion.worker.tools._registry.get_registry') as tools:
            with self.assertRaises(RuntimeError):
                async with lifespan(self.app):
                    pass
            tools.assert_not_called()

    async def test_registration_requires_approval_and_old_tokens_cannot_bypass_it(self):
        async def create(name, password):
            self.users[name] = {'id': name, 'password_hash': password, 'is_active': False}
            return True
        with patch.object(auth, 'create_user', side_effect=create), patch.object(auth, 'hash_password', return_value='hashed'), patch.object(auth, 'verify_password', return_value=True):
            data = {'username': 'pending', 'password': 'password'}
            response = await self.client.post('/api/v1/auth/register', json=data)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('pending', self.users)
            # Pre-existing pending accounts still require owner approval.
            self.users['pending'] = {'id': 'pending', 'password_hash': 'hashed', 'is_active': False}
            self.assertEqual((await self.client.post('/api/v1/auth/login', json=data)).status_code, 400)
            token = security.create_access_token({'sub': 'pending'})
            self.assertEqual((await self.client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token})).status_code, 401)
            with patch.dict(os.environ, {'ADMIN_API_KEY': 'owner'}):
                async def approve(name, active):
                    self.users[name]['is_active'] = active
                    return True
                with patch.object(auth, 'set_user_active', side_effect=approve):
                    path = '/api/v1/auth/users/pending/approval'
                    self.assertEqual((await self.client.put(path, json={})).status_code, 401)
                    self.assertEqual((await self.client.put(path, json={}, headers={'X-Admin-Key': 'owner'})).status_code, 200)
                    self.assertEqual((await self.client.post('/api/v1/auth/login', json=data)).status_code, 200)

    async def test_create_user_persists_inactive_flag(self):
        conn = Mock(execute=AsyncMock(), close=AsyncMock())
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), patch.object(registry, '_ensure_tables', AsyncMock()):
            self.assertTrue(await registry.create_user('pending', 'hash'))
        sql = conn.execute.call_args.args[0]
        self.assertIn('is_active', sql)
        self.assertIn('false', sql)

    async def test_chat_and_attachment_history_and_tools_deny_other_owner(self):
        # Attachments are inline history records, not a separately downloadable ID API.
        paths = ['/api/v1/chats/a', '/api/v1/chats/a/history', '/api/v1/chats/a/tools']
        for path in paths:
            response = await self.client.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 403, path)
            self.assertNotIn('attachments', response.text)
        for method, path, data in [('post','/api/v1/chats/a/stop',None), ('post','/api/v1/chats/a/turns/t/stop',None), ('patch','/api/v1/chats/a/rename',{'name':'stolen'}), ('delete','/api/v1/chats/a',None), ('put','/api/v1/chats/a/tools',{}), ('delete','/api/v1/chats/a/tools',None), ('post','/api/v1/chats/messages',{'user_id':'alice','chat_id':'a','input':{'text':'stolen'}})]:
            with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), patch('orion.api.services.job_service.get_runtime_settings', AsyncMock(return_value=RuntimeSettings())):
                response = await self.client.request(method, path, headers=self.headers, json=data)
            self.assertEqual(response.status_code, 403, path)
        self.redis.hgetall.return_value = {}
        with patch('orion.api.services.job_service.get_chat_db', AsyncMock(return_value=None)):
            self.assertEqual((await self.client.get('/api/v1/chats/missing/history', headers=self.headers)).status_code, 404)

    async def test_foreign_turn_cannot_be_stopped_using_owned_chat(self):
        self.redis.hgetall.return_value = {'user_id':'bob'}
        self.redis.get = AsyncMock(return_value='owned-turn')
        with patch('orion.api.services.job_service.get_runtime_settings', AsyncMock(return_value=RuntimeSettings())):
            response = await self.client.post('/api/v1/chats/b/turns/alice-turn/stop', headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.redis.pipeline.assert_not_called()

    async def test_stream_uses_authenticated_channel_despite_forged_ids(self):
        pubsub = Mock(subscribe=AsyncMock())
        self.redis.pubsub.return_value = pubsub
        with patch.object(routes, 'StreamingResponse', return_value=Response('stream')):
            response = await self.client.get('/api/v1/chat/stream?user_id=bob', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        pubsub.subscribe.assert_awaited_once_with(routes.ROOM_USER_PREFIX + 'bob')
        self.assertEqual((await self.client.get('/api/v1/chat/stream?user_id=alice&chat_id=a', headers=self.headers)).status_code, 403)
        self.assertEqual((await self.client.get('/api/v1/chat/stream?user_id=alice&chat_id=a')).status_code, 401)

    async def test_login_rate_limit_is_atomic_and_fails_closed(self):
        counts = {}
        async def evaluate(script, numkeys, key, seconds):
            self.assertIn("'EXPIRE'", script)
            counts[key] = counts.get(key, 0) + 1
            self.assertEqual(seconds, 30)
            return [counts[key], 30]
        self.redis.eval.side_effect = evaluate
        for _ in range(5):
            self.assertEqual((await self.client.post('/api/v1/auth/login', json={'username':'missing','password':'bad'})).status_code, 401)
        response = await self.client.post('/api/v1/auth/login', json={'username':'missing','password':'bad'}, headers={'X-Forwarded-For':'new-ip'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '30')
        self.redis.eval.side_effect = RuntimeError('redis down')
        self.assertEqual((await self.client.post('/api/v1/auth/login', json={'username':'new','password':'bad'})).status_code, 503)

    async def test_health_remains_public(self):
        self.assertEqual((await self.client.get('/health')).status_code, 200)

    async def test_websocket_rejects_unapproved_account(self):
        self.users['bob']['is_active'] = False
        token = security.create_access_token({'sub':'bob'})
        with TestClient(self.app) as client:
            with self.assertRaises(WebSocketDisconnect) as caught:
                with client.websocket_connect('/api/v1/audio/transcriptions/stream?token=' + token):
                    pass
            self.assertEqual(caught.exception.code, 1008)


class StartupSecurityTests(unittest.TestCase):
    def test_default_secret_rejected_for_remote_prod_and_network_binding(self):
        for mode, host, argv in [('remote','127.0.0.1',[]), ('production','127.0.0.1',[]), ('local','0.0.0.0',[]), ('local','127.0.0.1',['uvicorn','--host','0.0.0.0'])]:
            with patch.object(security, 'JWT_SECRET_KEY', security.DEFAULT_JWT_SECRET), patch.dict(os.environ, {'ORION_DEPLOYMENT_MODE':mode,'ORION_BIND_HOST':host}), patch.object(security.sys, 'argv', argv):
                with self.assertRaises(RuntimeError):
                    security.validate_security_configuration()

    def test_local_warning_and_strong_remote_secret(self):
        with patch.object(security.sys, 'argv', []), patch.dict(os.environ, {'ORION_DEPLOYMENT_MODE':'local','ORION_BIND_HOST':'127.0.0.1'}), patch.object(security, 'JWT_SECRET_KEY', security.DEFAULT_JWT_SECRET):
            with self.assertLogs(security.__name__, level='WARNING'):
                security.validate_security_configuration()
        with patch.object(security.sys, 'argv', []), patch.dict(os.environ, {'ORION_DEPLOYMENT_MODE':'remote','ORION_BIND_HOST':'0.0.0.0'}), patch.object(security, 'JWT_SECRET_KEY', 'a-secure-random-secret'):
            security.validate_security_configuration()

    def test_uvicorn_environment_host_is_network_facing(self):
        with patch.dict(os.environ, {'UVICORN_HOST':'0.0.0.0','ORION_DEPLOYMENT_MODE':'local'}), patch.object(security.sys,'argv', []), patch.object(security, 'JWT_SECRET_KEY', security.DEFAULT_JWT_SECRET):
            with patch.dict(os.environ):
                os.environ.pop('ORION_BIND_HOST', None)
                with self.assertRaises(RuntimeError):
                    security.validate_security_configuration()

    def test_conflicting_bind_markers_cannot_hide_network_listener(self):
        for orion_host, uvicorn_host in [('127.0.0.1','0.0.0.0'), ('0.0.0.0','127.0.0.1')]:
            with patch.dict(os.environ, {'ORION_BIND_HOST':orion_host,'UVICORN_HOST':uvicorn_host,'ORION_DEPLOYMENT_MODE':'local'}), patch.object(security.sys, 'argv', []), patch.object(security, 'JWT_SECRET_KEY', security.DEFAULT_JWT_SECRET):
                with self.assertRaises(RuntimeError):
                    security.validate_security_configuration()

    def test_cors_wildcard_configuration_rejected(self):
        with patch.dict(os.environ, {'ORION_CORS_ORIGINS':'*'}):
            with self.assertRaises(RuntimeError):
                configure_cors(FastAPI())


class InstallerSecurityTests(unittest.TestCase):
    def test_installer_generates_secret_only_when_missing_or_default(self):
        from installer.app.services import _ensure_hub_jwt_secret
        for value in (None, "", "super-secret-default-key-for-dev"):
            settings, added = {"JWT_SECRET_KEY": value}, {}
            _ensure_hub_jwt_secret(settings, added)
            self.assertGreaterEqual(len(settings['JWT_SECRET_KEY']), 64)
            self.assertEqual(settings, added)
        settings, added = {"JWT_SECRET_KEY": "existing-custom-secret"}, {}
        _ensure_hub_jwt_secret(settings, added)
        self.assertEqual(settings['JWT_SECRET_KEY'], "existing-custom-secret")
        self.assertEqual(added, {})


class ReplicaSecurityTests(unittest.TestCase):
    def test_all_api_replicas_load_same_local_secret_overrides(self):
        from pathlib import Path
        import yaml
        root = Path(__file__).parents[1]
        for filename, service in [('docker-compose.yml','api'), ('docker-compose.lb.yml','api_2')]:
            configuration = yaml.safe_load((root / filename).read_text(encoding='utf-8'))
            self.assertEqual(configuration['services'][service]['env_file'], ['../.env.global','../.env.global.local'])
