import asyncio
import unittest

from aiohttp import web

from orion.browser import CDPConnectionLost, CDPError, CDPSession, CDPTimeout


class CDPSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = asyncio.Queue()
        self.connected = asyncio.get_running_loop().create_future()

        async def endpoint(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            self.connected.set_result(socket)
            async for frame in socket:
                await self.messages.put(frame.json())
            return socket

        app = web.Application()
        app.router.add_get('/devtools/page/test', endpoint)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = self.runner.addresses[0][1]
        self.client = await CDPSession.connect(f'ws://127.0.0.1:{port}/devtools/page/test')
        self.server = await self.connected

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_out_of_order_replies_and_events(self):
        first = asyncio.create_task(self.client.send('Page.navigate', {'url': 'about:blank'}))
        second = asyncio.create_task(self.client.send('DOM.getDocument', session_id='child'))
        a = await self.messages.get()
        b = await self.messages.get()
        self.assertEqual(b['sessionId'], 'child')
        event = {'method': 'Page.loadEventFired', 'params': {'timestamp': 1}}
        await self.server.send_json(event)
        await self.server.send_json({'id': b['id'], 'result': {'root': 2}})
        await self.server.send_json({'id': a['id'], 'result': {'frameId': 'main'}})
        self.assertEqual(await first, {'frameId': 'main'})
        self.assertEqual(await second, {'root': 2})
        self.assertEqual(await self.client.events.get(), event)

    async def test_protocol_error(self):
        task = asyncio.create_task(self.client.send('Invalid.command'))
        request = await self.messages.get()
        await self.server.send_json({'id': request['id'], 'error': {'code': -32601, 'message': 'Unknown method'}})
        with self.assertRaises(CDPError) as caught:
            await task
        self.assertEqual(caught.exception.code, -32601)

    async def test_deadline_not_reset_by_events_and_no_replay(self):
        task = asyncio.create_task(self.client.send('Input.insertText', {'text': 'hello'}, timeout=.08))
        request = await self.messages.get()
        for _ in range(5):
            await self.server.send_json({'method': 'Page.event'})
            await asyncio.sleep(.02)
        with self.assertRaises(CDPTimeout):
            await task
        self.assertTrue(self.messages.empty())
        self.assertEqual(self.client._pending, {})
        # A late response must not satisfy a later command.
        await self.server.send_json({'id': request['id'], 'result': {'late': True}})
        next_task = asyncio.create_task(self.client.send('DOM.getDocument'))
        next_request = await self.messages.get()
        await self.server.send_json({'id': next_request['id'], 'result': {'ok': True}})
        self.assertEqual(await next_task, {'ok': True})

    async def test_disconnect_fails_all_pending_commands(self):
        tasks = [asyncio.create_task(self.client.send('Page.navigate')) for _ in range(2)]
        await self.messages.get()
        await self.messages.get()
        await self.server.close()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(all(isinstance(result, CDPConnectionLost) for result in results))
        self.assertTrue(self.client.closed)

    async def test_cancellation_removes_pending_request(self):
        task = asyncio.create_task(self.client.send('DOM.getDocument'))
        await self.messages.get()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.client._pending, {})

    async def test_rejects_nonlocal_endpoint(self):
        with self.assertRaises(ValueError):
            await CDPSession.connect('ws://example.com/devtools/browser/test')

    async def test_close_fails_pending_and_is_idempotent(self):
        task = asyncio.create_task(self.client.send('DOM.getDocument'))
        await self.messages.get()
        await self.client.close()
        with self.assertRaises(CDPConnectionLost):
            await task
        await self.client.close()
        self.assertTrue(self.client._http.closed)

    async def test_event_overflow_closes_connection(self):
        self.client.events = asyncio.Queue(maxsize=1)
        task = asyncio.create_task(self.client.send('DOM.getDocument'))
        await self.messages.get()
        await self.server.send_json({'method': 'Page.first'})
        await self.server.send_json({'method': 'Page.second'})
        with self.assertRaisesRegex(CDPConnectionLost, 'overflow'):
            await task


if __name__ == '__main__':
    unittest.main()
