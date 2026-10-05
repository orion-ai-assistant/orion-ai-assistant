"""Opt-in real PostgreSQL/Redis checks in an isolated schema and key namespace.

Run with ORION_LIVE_ACCESS_TESTS=1. Never touches existing users or invitations.
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import asyncpg
import httpx
from fastapi import HTTPException
from redis.asyncio import Redis
from services.shared.environment import get_postgres_url, get_redis_url
from orion.api import invitations, security
from orion.api.main import create_app
from orion.kernel import registry


class NamespacedRedis:
    def __init__(self, redis, prefix):
        self.redis, self.prefix, self.keys = redis, prefix, set()
    def key(self, value):
        value = self.prefix + value
        self.keys.add(value)
        return value
    async def eval(self, script, count, key, ttl):
        return await self.redis.eval(script, count, self.key(key), ttl)
    async def set(self, key, value, **options):
        return await self.redis.set(self.key(key), value, **options)
    async def get(self, key):
        return await self.redis.get(self.key(key))
    async def delete(self, key):
        return await self.redis.delete(self.key(key))


@unittest.skipUnless(os.getenv('ORION_LIVE_ACCESS_TESTS') == '1', 'opt-in isolated live dependencies')
class LiveInvitations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = 'orion_access_test_' + uuid4().hex
        self.dsn = get_postgres_url()
        self.admin = await asyncpg.connect(self.dsn)
        await self.admin.execute('create schema ' + self.schema)
        self.redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
        self.namespace = NamespacedRedis(self.redis, self.schema + ':')
        async def connect():
            return await asyncpg.connect(self.dsn, server_settings={'search_path': self.schema})
        self.connect = connect
        self.patch = patch.object(registry, '_connect', side_effect=connect)
        self.patch.start()
        self.invite_patch = patch.object(invitations, '_connect', side_effect=connect)
        self.invite_patch.start()
        conn = await connect()
        try:
            await registry._ensure_tables(conn)
        finally:
            await conn.close()
        self.app = create_app(public=True)
        self.app.state.redis = self.namespace
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 1234)), base_url='https://localhost:8910')
    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.invite_patch.stop()
        if self.namespace.keys:
            await self.redis.delete(*self.namespace.keys)
        await self.redis.aclose()
        # Identifier is a fixed prefix plus UUID hex, never user-supplied SQL.
        await self.admin.execute('drop schema ' + self.schema + ' cascade')
        await self.admin.close()

    async def test_concurrent_redemption_only_creates_one_account(self):
        await invitations.create_invitation('race', 'a' * 43, '', datetime.now(timezone.utc) + timedelta(minutes=5))
        async def use(name):
            try:
                await invitations.redeem_invitation('a' * 43, name, 'hash')
                return 201
            except HTTPException as error:
                return error.status_code
        results = await asyncio.gather(use('alice'), use('bob'))
        self.assertEqual(sorted(results), [201, 410])
        users = await invitations.list_people()
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0]['is_active'])

    async def test_duplicate_username_does_not_consume_invite_and_expiry_revoke_hold(self):
        await registry.create_user('existing', 'hash')
        await invitations.create_invitation('dup', 'b' * 43, '', datetime.now(timezone.utc) + timedelta(minutes=5))
        with self.assertRaises(HTTPException) as error:
            await invitations.redeem_invitation('b' * 43, 'existing', 'hash')
        self.assertEqual(error.exception.status_code, 409)
        await invitations.preview_invitation('b' * 43)
        await invitations.redeem_invitation('b' * 43, 'new', 'hash')
        await invitations.create_invitation('revoked', 'c' * 43, '', datetime.now(timezone.utc) + timedelta(minutes=5))
        await invitations.revoke_invitation('revoked')
        await invitations.create_invitation('expired', 'd' * 43, '', datetime.now(timezone.utc) - timedelta(seconds=1))
        for token in ['c' * 43, 'd' * 43]:
            with self.assertRaises(HTTPException):
                await invitations.redeem_invitation(token, 'rejected', 'hash')

    async def test_real_lua_30s_expiry_and_registration_auto_login(self):
        for _ in range(5):
            response = await self.client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong'})
            self.assertEqual(response.status_code, 401)
        response = await self.client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)
        self.assertTrue(1 <= int(response.headers['retry-after']) <= 30)
        for key in self.namespace.keys:
            self.assertTrue(1 <= await self.redis.ttl(key) <= 30)
            await self.redis.expire(key, 1)
        await asyncio.sleep(1.1)
        response = await self.client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        await invitations.create_invitation('signup', 'e' * 43, '', datetime.now(timezone.utc) + timedelta(minutes=5))
        response = await self.client.post('/api/v1/auth/register', json={'username': 'invited', 'password': 'correct', 'invitation_token': 'e' * 43})
        self.assertEqual(response.status_code, 201)
        token = response.json()['access_token']
        profile = await self.client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()['username'], 'invited')
        self.assertEqual((await self.client.post('/api/v1/auth/register', json={'username': 'another', 'password': 'correct', 'invitation_token': 'e' * 43})).status_code, 410)
