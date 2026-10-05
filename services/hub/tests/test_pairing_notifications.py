import unittest
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from orion.api.mobile_security import pairing_router, get_current_user, MobileBoundary, PairRequest, pairing_request_stream, owner_pairing_events
from orion.api.mobile_security import claim, Claim, PAIR_CODE_TAKE


class PairingNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_publishes_completion_with_redis5_atomic_consume(self):
        request = Mock(scope={'scheme': 'https'})
        request.app.state.mobile = True
        request.app.state.redis.eval = AsyncMock(return_value=json.dumps({
            'pairing_id': 'qr-id', 'request_id': 'phone-request'}))
        request.app.state.redis.set = AsyncMock()
        request.app.state.redis.publish = AsyncMock()
        with patch('orion.api.mobile_security.SecurityStore') as store, patch(
                'orion.api.mobile_security.tls_identity', return_value={'id': 'hub', 'name': 'Hiro', 'fp': 'pin'}):
            store.return_value.claim.return_value = ('device', 'token')
            result = await claim(Claim(code='ABCDEFGH', device_name='Phone'), request)
        self.assertEqual(result['device_id'], 'device')
        request.app.state.redis.getdel.assert_not_called()
        self.assertEqual(request.app.state.redis.eval.await_args.args[:2], (PAIR_CODE_TAKE, 1))
        event = json.loads(request.app.state.redis.publish.await_args.args[1])
        self.assertEqual(event['event'], 'pairing_completed')
        self.assertEqual(event['hub_id'], 'hub')
        self.assertEqual(event['request_id'], 'phone-request')

    async def test_completion_stream_keeps_event_name_in_json_and_sse(self):
        notice = {'event': 'pairing_completed', 'hub_id': 'hub', 'pairing_id': 'qr',
                  'expires_at': int(time.time()) + 120}
        pubsub = Mock(subscribe=AsyncMock(), unsubscribe=AsyncMock(), aclose=AsyncMock(),
                      get_message=AsyncMock(return_value={'data': json.dumps(notice)}))
        request = Mock(scope={}, is_disconnected=AsyncMock(return_value=False))
        request.app.state.redis.pubsub.return_value = pubsub
        with patch('orion.api.access_boundary.is_desktop_peer', return_value=True):
            response = await pairing_request_stream(request, 'account')
        stream = response.body_iterator
        await anext(stream)
        frame = await anext(stream)
        self.assertTrue(frame.startswith('event: pairing_completed\n'))
        self.assertEqual(json.loads(frame.split('data: ', 1)[1])['event'], 'pairing_completed')
        await stream.aclose()

    def app(self, authenticated=True):
        app = FastAPI()
        app.state.mobile = True
        app.state.redis = Mock(eval=AsyncMock(return_value=1), exists=AsyncMock(return_value=False), set=AsyncMock(), publish=AsyncMock())
        app.include_router(pairing_router)
        if authenticated:
            app.dependency_overrides[get_current_user] = lambda: 'account'
        return app

    async def test_anonymous_notice_has_no_pairing_secrets_and_is_rate_limited(self):
        app = self.app(False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://localhost:8443') as client:
            with patch('orion.api.mobile_security.tls_identity', return_value={'id': 'hub'}):
                response = await client.post('/api/v1/pair/request', json={'device_name': 'Phone\n', 'platform': 'android'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {'ok', 'request_id', 'expires_at'})
            self.assertEqual(app.state.redis.set.await_args.kwargs['ex'], 120)
            self.assertNotIn('code', app.state.redis.publish.await_args.args[1])
            app.state.redis.eval.return_value = 0
            response = await client.post('/api/v1/pair/request', json={'device_name': 'Phone'})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(app.state.redis.publish.await_count, 1)

    async def test_rejection_cooldown_suppresses_notice(self):
        app = self.app(False)
        app.state.redis.exists.return_value = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://localhost:8443') as client:
            response = await client.post('/api/v1/pair/request', json={'device_name': 'Phone'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '10')
        app.state.redis.publish.assert_not_awaited()

    async def test_remote_account_cannot_subscribe_or_manage_local(self):
        app = self.app()
        transport = httpx.ASGITransport(app=app, client=('192.168.1.22', 1234))
        async with httpx.AsyncClient(transport=transport, base_url='https://192.168.1.20:8443') as client:
            response = await client.get('/api/v1/pair/requests/stream')
            self.assertEqual(response.status_code, 403)
            app.state.redis.pubsub.assert_not_called()
            response = await client.get('/api/v1/pair/capabilities')
            self.assertFalse(response.json()['can_manage_local'])
            self.assertIsNone(response.json()['management_url'])

    async def test_unauthenticated_desktop_cannot_subscribe(self):
        app = self.app(False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://localhost:8443') as client:
            self.assertEqual((await client.get('/api/v1/pair/requests/stream')).status_code, 401)
            app.state.redis.pubsub.assert_not_called()

    async def test_revoked_device_has_machine_readable_pairing_code(self):
        app = self.app()
        boundary = MobileBoundary(app)
        with patch('orion.api.mobile_security.SecurityStore') as store:
            store.return_value.authenticate.return_value = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary), base_url='https://localhost:8443') as client:
                response = await client.get('/api/v1/auth/login')
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['code'], 'device_pairing_required')

    def test_names_strip_invisible_controls(self):
        self.assertEqual(PairRequest(device_name='My\x00 Phone\n').device_name, 'My Phone')

    async def test_stream_blocks_on_redis_and_closes_subscription(self):
        pubsub = Mock(subscribe=AsyncMock(), unsubscribe=AsyncMock(), aclose=AsyncMock(),
                      get_message=AsyncMock(return_value=None))
        request = Mock(scope={}, is_disconnected=AsyncMock(return_value=False))
        request.app.state.redis.pubsub.return_value = pubsub
        with patch('orion.api.access_boundary.is_desktop_peer', return_value=True):
            response = await pairing_request_stream(request, 'account')
        stream = response.body_iterator
        self.assertEqual(await anext(stream), ': connected\n\n')
        self.assertEqual(await anext(stream), ': heartbeat\n\n')
        pubsub.get_message.assert_awaited_once_with(ignore_subscribe_messages=True, timeout=15)
        await stream.aclose()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_fast_claim_closes_qr_even_before_eventsource_connected(self):
        pubsub = Mock(subscribe=AsyncMock(), unsubscribe=AsyncMock(), aclose=AsyncMock())
        request = Mock(is_disconnected=AsyncMock(return_value=False))
        request.state.local_owner_access = True
        request.state.owner_scope = 'people'
        request.app.state.redis.pubsub.return_value = pubsub
        request.app.state.redis.exists = AsyncMock(return_value=True)
        pairing_id = '378054d2-f833-4ec2-ac0c-41ccf38c6067'
        response = await owner_pairing_events(pairing_id, request)
        stream = response.body_iterator
        self.assertIn('event: pairing_completed', await anext(stream))
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        request.app.state.redis.exists.assert_awaited_once_with('orion:pairing:completed:' + pairing_id)
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()


    async def test_same_phone_request_closes_other_qr_after_fast_claim(self):
        pubsub = Mock(subscribe=AsyncMock(), unsubscribe=AsyncMock(), aclose=AsyncMock())
        request = Mock(is_disconnected=AsyncMock(return_value=False))
        request.state.local_owner_access = True
        request.state.owner_scope = 'people'
        request.app.state.redis.pubsub.return_value = pubsub
        request.app.state.redis.get = AsyncMock(return_value='{}')
        request.app.state.redis.exists = AsyncMock(side_effect=[False, True])
        request_id = '378054d2-f833-4ec2-ac0c-41ccf38c6067'
        response = await owner_pairing_events('12345678-1234-4321-8123-123456789abc', request, request_id)
        stream = response.body_iterator
        self.assertIn('event: pairing_completed', await anext(stream))
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        request.app.state.redis.exists.assert_any_await('orion:pairing:request-completed:' + request_id)


if __name__ == '__main__':
    unittest.main()
