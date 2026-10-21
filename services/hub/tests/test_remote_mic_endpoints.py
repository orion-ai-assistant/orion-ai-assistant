"""Both relay endpoints over real WebSockets, sharing an in-memory Redis."""
import asyncio
import unittest
from unittest import mock

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from orion.api import remote_mic


class FakePubSub:
    def __init__(self, redis):
        self.redis, self.queue, self.channels = redis, asyncio.Queue(), []

    async def subscribe(self, channel):
        self.channels.append(channel)
        self.redis.subscribers.setdefault(channel, []).append(self.queue)

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        try:
            data = await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        return {'type': 'message', 'data': data}

    async def unsubscribe(self, channel):
        self.redis.subscribers.get(channel, []).remove(self.queue)

    async def close(self):
        pass


class FakeRedis:
    def __init__(self):
        self.keys, self.subscribers = {}, {}

    def pubsub(self):
        return FakePubSub(self)

    async def publish(self, channel, data):
        for queue in self.subscribers.get(channel, []):
            queue.put_nowait(data)

    async def get(self, key):
        return self.keys.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def eval(self, script, count, key, value, *args):
        if self.keys.get(key) != value:
            return 0
        if 'DEL' in script:
            del self.keys[key]
        return 1


def build(redis):
    def app(mobile):
        application = FastAPI()
        application.include_router(remote_mic.router)
        application.state.mobile = mobile
        application.state.redis = redis
        return application
    return Starlette(routes=[Mount('/m', app=app(True)), Mount('/p', app=app(False))])


async def user(token, connection=None):
    return 'user'


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patches = [mock.patch('orion.api.auth_routes.authenticate_token', user),
                   mock.patch('orion.api.access_boundary.is_desktop_peer', lambda scope: True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(build(self.redis))

    def test_turn_round_trip(self):
        with self.client.websocket_connect('/p/api/v1/audio/remote-mic/pc?token=t') as pc:
            self.assertEqual(pc.receive_json()['type'], 'ready')
            with self.client.websocket_connect('/m/api/v1/audio/remote-mic/phone?token=t') as phone:
                self.assertEqual(phone.receive_json(), {'type': 'arm'})
                self.assertEqual(pc.receive_json(), {'type': 'phone_ready'})
                phone.send_json({'type': 'turn_start', 'turn_id': 'x1', 'sample_rate': 16000,
                                 'start_sample': 10, 'wake_end_sample': 12, 'wake_score': .5,
                                 'preroll_samples': 3})
                phone.send_bytes(b'\x01\x00\x02\x00\x03\x00')
                phone.send_bytes(b'\x04\x00')
                start = pc.receive_json()
                self.assertEqual(start['type'], 'turn_start')
                self.assertEqual(start['wake_end_sample'], 12)
                self.assertNotIn('phone', start)
                self.assertEqual(pc.receive_bytes(), b'\x01\x00\x02\x00\x03\x00')
                self.assertEqual(pc.receive_bytes(), b'\x04\x00')
                pc.send_json({'type': 'turn_end', 'turn_id': 'x1'})
                message = phone.receive_json()
                while message['type'] == 'heartbeat':
                    message = phone.receive_json()
                self.assertEqual(message, {'type': 'turn_end', 'turn_id': 'x1', 'reason': 'pc'})
                self.assertNotIn(remote_mic.turn_key('user'), self.redis.keys)
        self.assertNotIn(remote_mic.pc_key('user'), self.redis.keys)

    def test_phone_without_pc_is_disarmed_and_rejected(self):
        with self.client.websocket_connect('/m/api/v1/audio/remote-mic/phone?token=t') as phone:
            self.assertEqual(phone.receive_json(), {'type': 'disarm'})
            phone.send_json({'type': 'turn_start', 'turn_id': 'x1', 'sample_rate': 16000,
                             'start_sample': 0, 'wake_end_sample': 1, 'preroll_samples': 2})
            message = phone.receive_json()
            while message['type'] == 'heartbeat':
                message = phone.receive_json()
            self.assertEqual(message, {'type': 'turn_reject', 'turn_id': 'x1', 'reason': 'no_pc'})

    def test_endpoints_refuse_the_wrong_listener(self):
        from starlette.websockets import WebSocketDisconnect
        for path in ('/p/api/v1/audio/remote-mic/phone?token=t', '/m/api/v1/audio/remote-mic/pc?token=t'):
            with self.subTest(path=path), self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(path) as socket:
                    socket.receive_json()

    def test_pc_endpoint_requires_this_pc(self):
        from starlette.websockets import WebSocketDisconnect
        with mock.patch('orion.api.access_boundary.is_desktop_peer', lambda scope: False):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/p/api/v1/audio/remote-mic/pc?token=t') as socket:
                    socket.receive_json()


if __name__ == '__main__':
    unittest.main()
