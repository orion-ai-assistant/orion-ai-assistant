"""The chat event stream releases its Redis subscription however the client leaves."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from orion.api import routes


class ChatStreamCleanupTests(unittest.IsolatedAsyncioTestCase):
    def request(self, disconnected=True):
        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()
        pubsub.get_message = AsyncMock(return_value=None)
        redis = MagicMock()
        redis.pubsub.return_value = pubsub
        request = MagicMock()
        request.app.state.redis = redis
        request.query_params = {}
        request.is_disconnected = AsyncMock(return_value=disconnected)
        return request, pubsub

    async def test_a_stream_that_never_started_is_released_by_its_background_task(self):
        request, pubsub = self.request()
        response = await routes.chat_stream(request, current_user='alice')
        pubsub.subscribe.assert_awaited_once()
        await response.background()  # Starlette runs it after the response, also when the client is gone.
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_a_finished_stream_is_released_once(self):
        request, pubsub = self.request(disconnected=True)
        settings = SimpleNamespace(sse_heartbeat_seconds=1)
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=settings)):
            response = await routes.chat_stream(request, current_user='alice')
            async for _ in response.body_iterator:
                pass
        await response.background()
        pubsub.aclose.assert_awaited_once()

    async def test_settings_failure_still_releases_the_subscription(self):
        request, pubsub = self.request(disconnected=False)
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=ConnectionError('redis down'))):
            response = await routes.chat_stream(request, current_user='alice')
            with self.assertRaises(ConnectionError):
                async for _ in response.body_iterator:
                    pass
        pubsub.aclose.assert_awaited_once()

    async def test_redis_gone_at_unsubscribe_still_closes(self):
        request, pubsub = self.request()
        pubsub.unsubscribe.side_effect = ConnectionError('redis down')
        response = await routes.chat_stream(request, current_user='alice')
        await response.background()
        pubsub.aclose.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
