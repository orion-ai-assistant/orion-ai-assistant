import contextlib
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from orion.api import auth_routes, routes, security
from orion.api.cors import configure_cors
from orion.api.services import job_service
from orion.browser.manager import BrowserManager
from orion.contracts.settings import RuntimeSettings
from orion.kernel import registry


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.hgetall = AsyncMock(return_value={"user_id": "alice", "chat_id": "a"})
        self.get = AsyncMock(return_value="alice-turn")
        self.pipeline = Mock()
        self.pubsub = Mock()

    def lock(self, *args, **kwargs):
        return contextlib.nullcontext()

    async def eval(self, script, count, key, ttl):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], ttl]



class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.users = {name: {"id": name, "password_hash": "hash", "is_active": True}
                      for name in ("alice", "bob", "k\u00fcr\u015fat")}
        self.redis = FakeRedis()
        self.security_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.security_dir.cleanup)
        app = FastAPI()
        app.state.redis = self.redis
        app.include_router(auth_routes.router, prefix="/api/v1")
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub")
        self.patches = [
            patch.object(auth_routes, "get_user_by_username", AsyncMock(side_effect=lambda name: self.users.get(name))),
            patch.object(auth_routes, "hash_password", return_value="hash"),
            patch.object(auth_routes, "verify_password", side_effect=lambda password, hashed: password == "correct"),
            patch.object(job_service, "get_runtime_settings", AsyncMock(return_value=RuntimeSettings())),
            # Keep the developer's own owner password (files/security) out of the test.
            patch.dict(os.environ, {"ADMIN_API_KEY": "", "ORION_SECURITY_DIR": self.security_dir.name}),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.client.aclose()

    def headers(self, user="bob"):
        return {"Authorization": "Bearer " + security.create_access_token({"sub": user})}

    async def test_foreign_chat_history_attachments_tools_and_mutations_are_denied(self):
        cases = [("GET", "/api/v1/chats/a", None), ("GET", "/api/v1/chats/a/history", None),
                 ("GET", "/api/v1/chats/a/tools", None), ("PUT", "/api/v1/chats/a/tools", {}), ("DELETE", "/api/v1/chats/a/tools", None),
                 ("DELETE", "/api/v1/chats/a", None), ("PATCH", "/api/v1/chats/a/rename", {"name": "stolen"}),
                 ("POST", "/api/v1/chats/a/stop", None),
                 ("POST", "/api/v1/chats/messages", {"user_id": "alice", "chat_id": "a", "input": {"text": "stolen"}})]
        for method, path, payload in cases:
            with self.subTest(path=path):
                response = await self.client.request(method, path, headers=self.headers(), json=payload)
                self.assertIn(response.status_code, (403, 404))
        self.redis.pipeline.assert_not_called()

    async def test_foreign_stream_identifiers_are_denied_before_subscribing(self):
        for query in ("chat_id=a", "user_id=alice", "chat_id=a&user_id=bob"):
            response = await self.client.get("/api/v1/chat/stream?" + query, headers=self.headers())
            self.assertEqual(response.status_code, 403)
        self.redis.pubsub.assert_not_called()

    async def test_foreign_turn_cannot_be_stopped_from_owned_chat(self):
        response = await self.client.post("/api/v1/chats/a/turns/bob-turn/stop", headers=self.headers("alice"))
        self.assertEqual(response.status_code, 404)
        self.redis.pipeline.assert_not_called()

    async def test_unconfigured_admin_key_cannot_authorize_any_admin_operation(self):
        for key in ({}, {"X-Admin-Key": "random"}):
            for path in ("/api/v1/admin/chats", "/api/v1/admin/users/settings", "/api/v1/admin/settings/defaults", "/api/v1/admin/settings/constraints"):
                response = await self.client.get(path, headers=key)
                self.assertEqual(response.status_code, 401)
        response = await self.client.get("/api/v1/chats?admin_key=random")
        self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.client.get("/health")).status_code, 200)

    async def test_settings_schema_requires_authentication_and_accepts_normal_user(self):
        response = await self.client.get("/api/v1/admin/settings/schema")
        self.assertEqual(response.status_code, 401)
        response = await self.client.get("/api/v1/admin/settings/schema", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("temperature", response.json())

    async def test_registration_stays_pending_until_owner_approves(self):
        async def create(name, hashed):
            self.users[name] = {"id": name, "password_hash": hashed, "is_active": False}
            return True
        async def approve(name, active):
            self.users[name]["is_active"] = active
            return True
        with patch.object(auth_routes, "create_user", AsyncMock(side_effect=create)), patch.object(auth_routes, "set_user_approval", AsyncMock(side_effect=approve)):
            response = await self.client.post("/api/v1/auth/register", json={"username": "pending", "password": "correct"})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('pending', self.users)
            self.users['pending'] = {'id': 'pending', 'password_hash': 'hash', 'is_active': False}
            response = await self.client.post("/api/v1/auth/login", json={"username": "pending", "password": "correct"})
            self.assertEqual(response.status_code, 400)
            response = await self.client.get("/api/v1/auth/me", headers=self.headers("pending"))
            self.assertEqual(response.status_code, 401)
            response = await self.client.patch("/api/v1/auth/users/pending/approval", json={"approved": True}, headers={"X-Admin-Key": "random"})
            self.assertEqual(response.status_code, 401)
            with patch.dict(os.environ, {"ADMIN_API_KEY": "owner-secret"}):
                response = await self.client.patch("/api/v1/auth/users/pending/approval", json={"approved": True}, headers={"X-Admin-Key": "owner-secret"})
                self.assertEqual(response.status_code, 200)
            response = await self.client.post("/api/v1/auth/login", json={"username": "pending", "password": "correct"})
            self.assertEqual(response.status_code, 200)
            self.users["pending"]["is_active"] = False
            response = await self.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + response.json()["access_token"]})
            self.assertEqual(response.status_code, 401)

    async def test_database_inserts_pending_user_and_migrates_default(self):
        conn = Mock(execute=AsyncMock(return_value="INSERT 0 1"), close=AsyncMock())
        with patch.object(registry, "_connect", AsyncMock(return_value=conn)):
            self.assertTrue(await registry.create_user("new", "hash"))
        statements = [call.args[0] for call in conn.execute.await_args_list]
        self.assertTrue(any("alter column is_active set default false" in sql for sql in statements))
        self.assertTrue(any("values ($1, $2, false)" in sql for sql in statements))

    async def test_login_limited_after_five_failed_attempts_and_redis_failure_is_closed(self):
        for _ in range(5):
            response = await self.client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong"})
            self.assertEqual(response.status_code, 401)
        response = await self.client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "30")
        with patch.object(self.redis, "eval", AsyncMock(side_effect=RuntimeError("down"))):
            response = await self.client.post("/api/v1/auth/login", json={"username": "alice", "password": "correct"})
            self.assertEqual(response.status_code, 503)

    async def test_unicode_username_login_and_long_utf8_password_validation(self):
        response = await self.client.post("/api/v1/auth/login", json={"username": "k\u00fcr\u015fat", "password": "correct"})
        self.assertEqual(response.status_code, 200)
        for endpoint in ("login", "register"):
            response = await self.client.post("/api/v1/auth/" + endpoint, json={"username": "unicode", "password": "\u015f" * 37})
            self.assertEqual(response.status_code, 422)

    async def test_durable_owner_check_when_redis_metadata_expires(self):
        self.redis.hgetall.return_value = {}
        with patch.object(job_service, "get_chat_db", AsyncMock(return_value={"user_id": "alice"})):
            response = await self.client.get("/api/v1/chats/a/history", headers=self.headers())
            self.assertEqual(response.status_code, 403)

    def test_cors_wildcard_configuration_is_refused(self):
        with patch.dict(os.environ, {"ORION_CORS_ORIGINS": "*"}):
            with self.assertRaises(RuntimeError):
                configure_cors(FastAPI())

    def test_browser_username_cannot_alias_another_users_profile_hash(self):
        alice = BrowserManager("alice")
        attacker = BrowserManager(hashlib.sha256(b"alice").hexdigest())
        self.assertNotEqual(alice.directory, attacker.directory)
        self.assertEqual(BrowserManager(alice.directory).directory, alice.directory)


class StartupSecurityTests(unittest.TestCase):
    def test_default_secret_is_refused_in_remote_prod_and_nonloopback_bind(self):
        cases = [({"ORION_HUB_MODE": "remote"}, []), ({"ORION_HUB_MODE": "production"}, []),
                 ({"ENVIRONMENT": "production"}, []), ({"ORION_INSTALL_MODE": "docker"}, []),
                 ({}, ["uvicorn", "--host", "0.0.0.0"]), ({}, ["uvicorn", "--host=::"])]
        for env, argv in cases:
            with self.subTest(env=env, argv=argv), patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", argv), patch.object(security, "JWT_SECRET_KEY", security.DEFAULT_JWT_SECRET):
                with self.assertRaises(RuntimeError):
                    security.validate_jwt_configuration()

    def test_local_default_warns_strong_remote_passes_blank_is_refused(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", []), patch.object(security, "JWT_SECRET_KEY", security.DEFAULT_JWT_SECRET):
            with self.assertLogs(security.__name__, level="WARNING"):
                security.validate_jwt_configuration()
        with patch.dict(os.environ, {"ORION_HUB_MODE": "remote"}), patch.object(security, "JWT_SECRET_KEY", "a" * 64):
            security.validate_jwt_configuration()
        with patch.object(security, "JWT_SECRET_KEY", ""):
            with self.assertRaises(RuntimeError):
                security.validate_jwt_configuration()
