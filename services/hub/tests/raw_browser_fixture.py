"""Opt-in isolated test browser; production never launches a browser."""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from orion.browser import CDPSession, CDPConnectionLost
from orion.browser.manager import BrowserManager, find_binary, _connections

class RawBrowserFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('ORION_BROWSER_TESTS') != '1':
            raise unittest.SkipTest('Set ORION_BROWSER_TESTS=1 for isolated browser tests')
        root = Path(__file__).resolve().parents[3] / '.tmp' / 'browser-tests'
        root.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(dir=root)).resolve()
        cls.profile = cls.root / 'test-profile'
        cls.profile.mkdir()
        cls.runner = asyncio.Runner()
        cls.addClassCleanup(cls.runner.close)
        for target, value in [('browser_root', cls.root), ('user_data_dir', cls.profile)]:
            patcher = patch('orion.browser.manager.' + target, return_value=value)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        # The fixture supplies its own isolated profile; setup must never open
        # the person's normal Chrome via the production inspect launcher.
        inspect = patch('orion.browser.manager.auto_approve_browser_dialogs', return_value=None)
        inspect.start()
        cls.addClassCleanup(inspect.stop)
        startup = None
        if os.name == 'nt':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7
        options = (['--headless=new', '--disable-background-networking', '--disable-sync',
                    '--disable-component-update', '--host-resolver-rules=MAP * 127.0.0.1']
                   if os.getenv('ORION_BROWSER_TEST_HEADLESS') == '1' else [])
        cls.process = subprocess.Popen([find_binary('chrome'), *options, '--remote-debugging-port=0',
            '--user-data-dir=' + str(cls.profile), '--no-first-run', '--no-default-browser-check',
            'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
        cls.manager = BrowserManager('isolated-test')
        cls.addClassCleanup(lambda: cls.runner.run(cls.stop_browser()))
        cls.runner.run(cls.start())

    @classmethod
    async def start(cls):
        from orion.browser.bridge import BrowserBridge
        cls.bridge = await BrowserBridge().start()
        for _ in range(100):
            if await cls.manager.endpoint():
                await cls.manager.open_manual('chrome')
                return
            await asyncio.sleep(.1)
        raise RuntimeError('Test browser did not start')

    @classmethod
    async def stop_browser(cls):
        if hasattr(cls, 'bridge'):
            await cls.bridge.close()
        endpoint = await cls.manager.endpoint()
        if endpoint:
            async with await CDPSession.connect(endpoint[1]) as connection:
                try:
                    await connection.send('Browser.close')
                except CDPConnectionLost:
                    pass
        for key, connection in list(_connections.items()):
            if key[0] is asyncio.get_running_loop():
                await connection.close()
                _connections.pop(key, None)
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.terminate()

    def run_async(self, coroutine):
        return self.runner.run(coroutine)
