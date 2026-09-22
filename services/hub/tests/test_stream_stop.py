import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from orion.contracts.settings import RuntimeSettings
from orion.worker.services import router


class Response:
    status = 200

    def __init__(self, chunks):
        self.chunks = chunks
        self.content = self
        self.closed = False

    def iter_any(self):
        return self.chunks

    def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.close()


class StopTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, response, checker):
        session = unittest.mock.Mock()
        session.post.return_value = response
        with patch.object(router, 'get_session', AsyncMock(return_value=session)), patch.object(
            router, '_router_urls', return_value=['http://test/stream']
        ):
            return [event async for event in router.llama_stream_chat_typed(
                [], RuntimeSettings(), stop_checker=checker
            )]

    async def test_stop_drains_received_chunk_including_final_snapshot(self):
        async def chunks():
            yield (b'data: {"choices":[{"delta":{"content":"start"}}]}\n\n'
                   b'data: {"choices":[{"message":{"content":"start tail"}}]}\n\n')
            self.fail('Read upstream again after stop')

        response = Response(chunks())
        events = await self.collect(response, AsyncMock(return_value=True))
        self.assertEqual(events, [('content', 'start'), ('content_snapshot', 'start tail')])
        self.assertTrue(response.closed)

    async def test_stop_closes_idle_stream_without_waiting_for_token(self):
        released = asyncio.Event()

        async def chunks():
            try:
                await asyncio.Event().wait()
                yield b''
            finally:
                released.set()

        response = Response(chunks())
        events = await asyncio.wait_for(
            self.collect(response, AsyncMock(return_value=True)), timeout=1
        )
        self.assertEqual(events, [])
        self.assertTrue(response.closed)
        self.assertTrue(released.is_set())
