import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp

from orion.browser.bridge import BrowserBridge, SharedConnection
from orion.browser.manager import BrowserUnavailable


class BrowserBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch_root = patch('orion.browser.manager.browser_root', return_value=self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.endpoint = 'ws://127.0.0.1:1234/devtools/browser/test'
        self.discovery = patch('orion.browser.manager.discover_endpoint', return_value=('', self.endpoint))
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.connection = AsyncMock()
        self.connection.closed = False
        self.connection.send.return_value = {'product': 'test-browser'}
        self.direct = patch('orion.browser.manager.get_direct_connection', AsyncMock(return_value=self.connection))
        self.connect = self.direct.start()
        self.addCleanup(self.direct.stop)
        self.bridge = await BrowserBridge().start()
        self.addAsyncCleanup(self.bridge.close)

    async def test_settings_and_separate_worker_share_one_browser_connection(self):
        await SharedConnection(self.endpoint).rpc('connect')
        # Even concurrent Settings requests reuse the already granted connection.
        await asyncio.gather(*(SharedConnection(self.endpoint).rpc('connect') for _ in range(3)))
        code = '''import asyncio, sys
from pathlib import Path
from orion.browser import manager
manager.browser_root = lambda: Path(sys.argv[1])
async def run():
    connection = await manager.get_connection(sys.argv[2])
    assert (await connection.send('Browser.getVersion'))['product'] == 'test-browser'
asyncio.run(run())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
            str(self.root), self.endpoint, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, error = await asyncio.wait_for(process.communicate(), 15)
        self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
        self.connect.assert_awaited_once_with(self.endpoint)
        self.connection.close.assert_not_awaited()

    async def test_closed_socket_never_prompts_from_tool_or_status(self):
        await SharedConnection(self.endpoint).rpc('connect')
        self.connection.closed = True
        client = SharedConnection(self.endpoint)
        self.assertFalse((await client.rpc('status'))['connected'])
        with self.assertRaises(BrowserUnavailable):
            await client.send('Target.createTarget', {'url': 'about:blank'})
        self.assertEqual(self.connect.await_count, 1)
        self.connection.send.assert_not_awaited()

    async def test_external_shutdown_closes_api_owned_socket_and_blocks_reconnect(self):
        await SharedConnection(self.endpoint).rpc('connect')
        code = '''import asyncio, sys
from pathlib import Path
from orion.browser import manager
manager.browser_root = lambda: Path(sys.argv[1])
from orion.browser.shutdown import close_all
asyncio.run(close_all())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
            str(self.root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, error = await asyncio.wait_for(process.communicate(), 15)
        self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
        self.connection.close.assert_awaited_once()
        self.connection.send.assert_not_awaited()
        self.assertTrue((self.root / '.stopping').exists())
        self.assertFalse((await SharedConnection(self.endpoint).rpc('status'))['connected'])
        with self.assertRaises(BrowserUnavailable):
            await SharedConnection(self.endpoint).rpc('connect')
        self.connect.assert_awaited_once()

    async def test_private_bridge_rejects_missing_token_and_browser_origin(self):
        descriptor = json.loads((self.root / 'bridge.json').read_text())
        async with aiohttp.ClientSession() as http:
            url = f"http://127.0.0.1:{descriptor['port']}/rpc"
            async with http.post(url, json={}) as response:
                self.assertEqual(response.status, 401)
            async with http.post(url, json={}, headers={
                'Authorization': 'Bearer ' + descriptor['token'], 'Origin': 'https://example.com'}) as response:
                self.assertEqual(response.status, 403)
        self.connect.assert_not_awaited()

    async def test_unknown_endpoint_cannot_trigger_connection(self):
        with self.assertRaises(BrowserUnavailable):
            await SharedConnection('ws://127.0.0.1:9999/devtools/browser/unknown').rpc('connect')
        self.connect.assert_not_awaited()
