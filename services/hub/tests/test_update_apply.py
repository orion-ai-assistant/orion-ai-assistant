"""Explicit "update now": activity accounting, drain/hand-over and desktop-only control."""
import asyncio
import fnmatch
from unittest.mock import patch
import unittest

from fastapi import FastAPI, HTTPException
import httpx

from orion.api import update_routes as api
from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import AccessBoundary
from orion.kernel import activity


class FakeRedis:
    def __init__(self):
        self.values, self.hashes = {}, {}

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def expire(self, key, ttl):
        return key in self.values

    async def delete(self, key):
        self.values.pop(key, None)

    async def exists(self, key):
        return int(key in self.values or key in self.hashes)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def scan(self, cursor, match='*', count=10):
        names = [*self.values, *self.hashes]
        return 0, [name for name in names if fnmatch.fnmatch(name, match)]


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_voice_transfer_and_remote_work_all_count(self):
        redis = FakeRedis()
        self.assertEqual((await activity.snapshot(redis))['busy'], False)
        redis.hashes['chat:state:a'] = {'status': 'processing'}
        redis.hashes['chat:state:b'] = {'status': 'completed'}
        redis.hashes['chat:state:c'] = {'status': 'queued'}
        await activity.mark_remote_chat(redis, 'c')
        async with activity.lease(redis, 'voice'), activity.lease(redis, 'transfer', remote=True):
            seen = await activity.snapshot(redis)
            self.assertEqual(seen['counts'], {'chat': 2, 'voice': 1, 'transfer': 1, 'remote': 2})
            self.assertTrue(seen['busy'])
        self.assertEqual((await activity.snapshot(redis))['counts'], {'chat': 2, 'voice': 0, 'transfer': 0, 'remote': 1})
        redis.hashes.clear()
        self.assertFalse((await activity.snapshot(redis))['busy'])

    async def test_lease_never_fails_the_request_when_redis_is_down(self):
        class Broken(FakeRedis):
            async def set(self, *args, **kwargs):
                raise OSError('down')
        async with activity.lease(Broken(), 'voice'):
            pass

    async def test_draining_rejects_new_work_with_503(self):
        redis = FakeRedis()
        await activity.require_not_draining(redis)
        await activity.set_draining(redis, True)
        with self.assertRaises(HTTPException) as caught:
            await activity.require_not_draining(redis)
        self.assertEqual(caught.exception.status_code, 503)
        await activity.set_draining(redis, False)
        await activity.require_not_draining(redis)


class DrainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {'phase': 'waiting_for_idle'}
        self.enterContext(patch.object(api.update_now, 'read', side_effect=lambda registry: dict(self.state)))
        self.enterContext(patch.object(api.update_now, 'update',
                                       side_effect=lambda registry, only=None, **fields: self.state.update(fields)))
        self.enterContext(patch.object(api.update_now, 'handoff',
                                       side_effect=lambda registry: self.state.update(phase='requested') or True))

    async def test_waits_while_busy_and_hands_over_only_after_quiet_checks(self):
        redis = FakeRedis()
        redis.hashes['chat:state:a'] = {'status': 'processing'}
        task = asyncio.create_task(api.wait_for_idle_and_hand_over(redis, object(), interval=.01, quiet_checks=3))
        await asyncio.sleep(.1)
        self.assertFalse(task.done())
        self.assertEqual(self.state['phase'], 'waiting_for_idle')
        self.assertEqual(self.state['busy']['chat'], 1)
        self.assertFalse(await activity.draining(redis))  # Busy work is never blocked or interrupted.
        redis.hashes.clear()
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.state['phase'], 'requested')
        self.assertTrue(await activity.draining(redis))

    async def test_work_that_appears_after_the_quiet_window_reopens_admission(self):
        redis = FakeRedis()
        original = activity.snapshot
        calls = []

        async def racy(client):
            calls.append(1)
            if len(calls) == 4:  # The re-check after admission closed sees new work.
                return {'counts': {'chat': 1}, 'busy': True}
            return await original(client)
        with patch.object(activity, 'snapshot', racy):
            await asyncio.wait_for(
                api.wait_for_idle_and_hand_over(redis, object(), interval=.01, quiet_checks=3), 2)
        self.assertGreater(len(calls), 4)  # It started over instead of handing over at once.
        self.assertEqual(self.state['phase'], 'requested')

    async def test_cancel_stops_waiting(self):
        redis = FakeRedis()
        redis.hashes['chat:state:a'] = {'status': 'processing'}
        task = asyncio.create_task(api.wait_for_idle_and_hand_over(redis, object(), interval=.01))
        await asyncio.sleep(.05)
        self.state['phase'] = 'cancelled'
        await asyncio.wait_for(task, 2)
        self.assertFalse(await activity.draining(redis))

    async def test_unreadable_activity_fails_safe(self):
        class Broken(FakeRedis):
            async def scan(self, *args, **kwargs):
                raise OSError('redis down')
        await api.wait_for_idle_and_hand_over(Broken(), object(), interval=.01)
        self.assertEqual(self.state['phase'], 'failed')
        self.assertEqual(self.state['error'], 'ActivityCheckFailed')


class ApplyRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.state.mobile = False
        self.app.state.redis = FakeRedis()
        self.app.include_router(api.router)
        self.app.dependency_overrides[get_current_user] = lambda: 'alice'
        self.app.add_middleware(AccessBoundary, public=True)
        self.enterContext(patch('orion.api.discovery.lan_addresses', return_value=['192.168.1.10']))
        self.begin = self.enterContext(patch.object(api, 'begin_apply', return_value={'phase': 'waiting_for_idle'}))
        self.wait = self.enterContext(patch.object(api, 'wait_for_idle_and_hand_over', side_effect=self.finished))
        self.cancel = self.enterContext(patch.object(api.update_now, 'cancel', return_value={}))
        self.enterContext(patch.object(api, 'read_status',
                                       return_value={'available': True, 'apply_now': {'phase': 'waiting_for_idle'}}))
        self.enterContext(patch.object(api, 'Registry', return_value=object()))

    async def finished(self, *args, **kwargs):
        return None

    def client(self, peer='127.0.0.1', host='127.0.0.1', **kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=(peer, 1234)),
                                 base_url='https://' + host, **kwargs)

    async def test_phone_and_remote_sessions_cannot_apply_or_cancel(self):
        async with self.client('192.168.1.44', '192.168.1.10') as client:
            self.assertEqual((await client.post('/api/v1/updates/apply')).status_code, 403)
            self.assertEqual((await client.post('/api/v1/updates/apply/cancel')).status_code, 403)
        async with self.client(headers={'X-Forwarded-For': '127.0.0.1'}) as client:
            self.assertEqual((await client.post('/api/v1/updates/apply')).status_code, 403)
        async with self.client(headers={'Origin': 'https://evil.example'}) as client:
            self.assertEqual((await client.post('/api/v1/updates/apply')).status_code, 403)
        self.app.state.mobile = True
        async with self.client() as client:
            self.assertEqual((await client.post('/api/v1/updates/apply')).status_code, 403)
        self.begin.assert_not_called()
        self.cancel.assert_not_called()

    async def test_desktop_peer_starts_and_cancels(self):
        async with self.client(headers={'Origin': 'http://127.0.0.1:8090'}) as client:
            response = await client.post('/api/v1/updates/apply')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['apply_now']['phase'], 'waiting_for_idle')
            self.assertEqual((await client.post('/api/v1/updates/apply/cancel')).status_code, 200)
        self.begin.assert_called_once()
        self.cancel.assert_called_once()

    async def test_request_body_is_ignored_never_a_command(self):
        async with self.client() as client:
            await client.post('/api/v1/updates/apply', json={'command': 'calc.exe', 'path': 'C:/x'})
        self.assertEqual(len(self.begin.call_args.args), 1)  # Only the registry reaches the helper.

    async def test_refusals_are_conflicts_with_plain_messages(self):
        for code, text in (('NothingStaged', 'hazır'), ('WatcherUnavailable', 'yardımcısı')):
            self.begin.side_effect = api.UpdateError(code)
            async with self.client() as client:
                response = await client.post('/api/v1/updates/apply')
            self.assertEqual(response.status_code, 409)
            self.assertIn(text, response.json()['detail'])
        self.wait.assert_not_called()
