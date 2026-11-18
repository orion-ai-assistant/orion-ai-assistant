import asyncio
import json
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
            # After a stop the stream is read a moment more, for the final usage chunk only.

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
        with patch.object(router, 'STOP_USAGE_GRACE', 0.1):
            events = await asyncio.wait_for(
                self.collect(response, AsyncMock(return_value=True)), timeout=1
            )
        self.assertEqual(events, [])
        self.assertTrue(response.closed)
        self.assertTrue(released.is_set())

    async def test_usage_sent_after_stop_is_kept_and_text_is_dropped(self):
        stopped = []

        async def checker():
            return bool(stopped)

        async def chunks():
            yield b'data: {"choices":[{"delta":{"content":"start"}}]}\n\n'
            await asyncio.sleep(0.3)
            yield (b'data: {"choices":[{"delta":{"content":" more"}}]}\n\n'
                   b'data: {"usage":{"prompt_tokens":120,"completion_tokens":7},"choices":[]}\n\n')

        async def stop_soon():
            await asyncio.sleep(0.15)
            stopped.append(1)

        response = Response(chunks())
        task = asyncio.create_task(stop_soon())
        with patch.object(router, 'STOP_USAGE_GRACE', 1.0):
            events = await self.collect(response, checker)
        await task
        self.assertEqual(events[0], ('content', 'start'))
        self.assertEqual([e[0] for e in events[1:]], ['usage'])
        self.assertEqual(json.loads(events[1][1])['prompt_tokens'], 120)
        self.assertTrue(response.closed)
