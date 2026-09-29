import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable, file_lock
from orion.worker.tools._registry import get_registry
from orion.worker.tools.web_browser.tools import BrowserInput
from orion.worker.tools.youtube.tools import YouTubeInput


class BrowserManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patcher = patch('orion.browser.manager.browser_root', return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manager = BrowserManager('alice')

    async def test_manual_mode_blocks_automation_before_connecting(self):
        self.manager.directory.mkdir()
        self.manager._write(self.manager.config, {'mode': 'existing', 'manual': True, 'enabled': True})
        with patch.object(self.manager, 'ensure_launched', AsyncMock()) as launch:
            with self.assertRaises(BrowserUnavailable):
                async with self.manager.tab('google_ai'):
                    self.fail('Must not attach during login')
            launch.assert_not_awaited()
        with patch('orion.browser.manager.require_desktop'), \
                patch.object(self.manager, 'endpoint', AsyncMock(return_value=('', 'ws://127.0.0.1:1234/devtools/browser/test'))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=AsyncMock())):
            await self.manager.resume()
        self.assertFalse(self.manager._read(self.manager.config)['manual'])

    async def test_profiles_and_cross_process_lease(self):
        other = BrowserManager('../../bob')
        self.assertEqual(other.directory.parent, self.root)
        self.assertNotEqual(other.directory, self.manager.directory)
        with file_lock(self.manager.directory / 'operation.lock'):
            with self.assertRaises(BrowserBusy):
                async with self.manager.tab('google_ai'):
                    self.fail('Lease must be exclusive')
            with file_lock(other.directory / 'operation.lock'):
                pass

    async def test_closed_target_recreated_but_existing_target_reused(self):
        self.manager.directory.mkdir()
        endpoint = 'ws://127.0.0.1:1234/devtools/browser/owned'
        connection = MagicMock()
        connection.send = AsyncMock(side_effect=[
            {'targetInfos': []}, {'targetId': 'new'}, {'sessionId': 'session-1'}, {},
            {'targetInfos': [{'targetId': 'new', 'type': 'page'}]}, {'sessionId': 'session-2'}, {},
            {'targetInfos': []}, {'targetId': 'replacement'}, {'sessionId': 'session-3'}, {}])
        connection.closed = False
        connection.__aenter__ = AsyncMock(return_value=connection)
        connection.__aexit__ = AsyncMock(return_value=False)
        with patch.object(self.manager, 'ensure_launched', AsyncMock(return_value=('', endpoint))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            for expected in ('new', 'new', 'replacement'):
                async with self.manager.tab('google_ai') as page:
                    self.assertEqual(page.target_id, expected)
        calls = [c.args[0] for c in connection.send.call_args_list]
        self.assertEqual(calls.count('Target.createTarget'), 2)
        self.assertNotIn('Browser.close', calls)

    async def test_missing_setup_cannot_silently_launch(self):
        with patch('orion.browser.manager.require_desktop'), patch('subprocess.Popen') as process:
            with self.assertRaises(BrowserUnavailable):
                await self.manager.ensure_launched()
            process.assert_not_called()

    def test_tool_validation_and_discovery(self):
        registry = get_registry()
        self.assertIn('browser_control', registry.tools)
        self.assertIn('browser_tabs', registry.tools)
        self.assertIn('youtube_control', registry.tools)
        for kwargs in ({'action': 'navigate', 'url': 'file:///secret'}, {'action': 'click'}, {'action': 'type', 'selector': 'input'}):
            with self.assertRaises(ValueError):
                BrowserInput(**kwargs)
        for url in ('https://youtube.com.attacker.net/watch', 'https://user:pass@youtube.com/watch', 'javascript:alert(1)'):
            with self.assertRaises(ValueError):
                YouTubeInput(action='open', target=url)

    async def test_old_profile_configuration_requires_explicit_new_connection(self):
        self.manager.directory.mkdir()
        self.manager._write(self.manager.config, {'enabled': True, 'browser': 'brave'})
        with patch('orion.browser.manager.require_desktop'):
            with self.assertRaises(BrowserUnavailable):
                await self.manager.ensure_launched()
        self.assertEqual(self.manager.browser_choice(), 'chrome')

    async def test_hidden_target_cleanup_and_no_foreground_even_on_verification(self):
        connection = MockConnection()
        with patch.object(self.manager, 'ensure_launched', AsyncMock(return_value=('', 'ws://127.0.0.1:1234/devtools/browser/test'))), patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            async with self.manager.tab('google_ai', background=True) as page:
                await page.require_verification()
        calls = connection.send.call_args_list
        self.assertEqual(calls[0].args, ('Target.createTarget', {'url': 'about:blank', 'hidden': True, 'background': True}))
        methods = [c.args[0] for c in calls]
        self.assertIn('Target.closeTarget', methods)
        self.assertNotIn('Target.activateTarget', methods)
        self.assertNotIn('Browser.setWindowBounds', methods)
        self.assertNotIn('Browser.close', methods)

    def test_endpoint_discovery_rejects_untrusted_coordinates(self):
        from orion.browser.manager import discover_endpoint
        with patch('orion.browser.manager.user_data_dir', return_value=self.root):
            file = self.root / 'DevToolsActivePort'
            for content in ('0\n/devtools/browser/a', '65536\n/devtools/browser/a', '1234\n//attacker/a', '1234\n/devtools/browser/a?redirect=x'):
                file.write_text(content)
                self.assertIsNone(discover_endpoint('chrome'))
            file.write_text('9333\n/devtools/browser/abc-123')
            self.assertEqual(discover_endpoint('chrome')[1], 'ws://127.0.0.1:9333/devtools/browser/abc-123')

    async def test_connection_denial_does_not_enable_new_mode(self):
        from orion.browser.manager import get_connection
        endpoint = ('http://127.0.0.1:1234', 'ws://127.0.0.1:1234/devtools/browser/denied')
        with patch('orion.browser.manager.require_desktop'), \
                patch('orion.browser.manager.open_inspect_window', AsyncMock(return_value=None)), \
                patch('orion.browser.manager.discover_endpoint', return_value=endpoint), \
                patch('orion.browser.manager.CDPSession.connect', AsyncMock(side_effect=OSError('denied'))):
            with self.assertRaises(BrowserUnavailable):
                await self.manager.open_manual('chrome')
        self.assertFalse(self.manager._read(self.manager.config).get('enabled'))

    async def test_failed_brave_setup_retains_selection(self):
        with patch('orion.browser.manager.require_desktop'), \
                patch('orion.browser.manager.open_inspect_window', AsyncMock(return_value=None)), \
                patch('orion.browser.manager.discover_endpoint', return_value=('', 'ws://127.0.0.1:1234/devtools/browser/brave')), \
                patch('orion.browser.manager.get_connection', AsyncMock(side_effect=BrowserUnavailable('denied'))):
            with self.assertRaises(BrowserUnavailable):
                await self.manager.open_manual('brave')
        self.assertEqual(self.manager.browser_choice(), 'brave')
        self.assertFalse(self.manager._read(self.manager.config)['enabled'])

    async def test_setup_opens_inspect_and_minimizes_only_new_window_after_approval(self):
        endpoint = ('', 'ws://127.0.0.1:1234/devtools/browser/brave')
        connection = AsyncMock()
        with patch('orion.browser.manager.require_desktop'), \
                patch('orion.browser.manager.open_inspect_window', AsyncMock(return_value=876)) as inspect, \
                patch('orion.browser.manager.discover_endpoint', return_value=endpoint), \
                patch('orion.browser.manager.get_connection', AsyncMock(side_effect=[BrowserUnavailable('not connected'), connection])) as connect, \
                patch('orion.browser.manager.minimize_window') as minimize:
            await self.manager.open_manual('brave')
        inspect.assert_awaited_once_with('brave')
        self.assertEqual(connect.await_args_list[0].args, (endpoint[1],))
        self.assertEqual(connect.await_args_list[1].kwargs, {'authorize': True})
        minimize.assert_called_once_with(876)
        self.assertEqual(self.manager.browser_choice(), 'brave')

    async def test_first_brave_setup_uses_brave_page_and_toggles_only_named_checkbox(self):
        from orion.browser.manager import open_inspect_window
        from orion.browser.desktop import enable_remote_debugging_checkbox
        with patch('orion.browser.manager.find_binary', return_value='brave.exe'), \
                patch('orion.browser.manager.browser_windows', side_effect=[[], [(876, 'brave')]]), \
                patch('orion.browser.manager.navigate_to_inspect_page'), \
                patch('orion.browser.manager.subprocess.Popen') as launch:
            self.assertEqual(await open_inspect_window('brave'), 876)
        # Now opens a plain new window; URL is set via UIA omnibox, not command-line arg.
        self.assertEqual(launch.call_args.args[0], ['brave.exe', '--new-window'])
        with patch('orion.browser.desktop.os.name', 'nt'), \
                patch('orion.browser.desktop.subprocess.run', return_value=MagicMock(returncode=0)) as invoke:
            self.assertTrue(enable_remote_debugging_checkbox(876))
        command = invoke.call_args.args[0]
        script = base64.b64decode(command[-1]).decode('utf-16-le')
        self.assertIn('Allow remote debugging for this browser instance', script)
        self.assertIn('$WindowHandle = 876', script)
        self.assertIn('TogglePattern', script)


    async def test_first_setup_tries_checkbox_before_connecting(self):
        endpoint = ('', 'ws://127.0.0.1:1234/devtools/browser/brave')
        order = []
        def discover(_):
            # Before opening/toggling, the opt-in endpoint is unavailable.
            return endpoint if 'toggle' in order else None
        async def open_window(_):
            order.append('inspect')
            return 876
        def toggle(*_, **__):
            order.append('toggle')
            return True
        async def connect(*_, **__):
            order.append('connect')
            return AsyncMock()
        with patch('orion.browser.manager.require_desktop'), \
                patch('orion.browser.manager.discover_endpoint', side_effect=discover), \
                patch('orion.browser.manager.open_inspect_window', side_effect=open_window), \
                patch('orion.browser.manager.auto_approve_browser_dialogs'), \
                patch('orion.browser.manager.enable_remote_debugging_checkbox', side_effect=toggle), \
                patch('orion.browser.manager.get_connection', side_effect=connect), \
                patch('orion.browser.manager.minimize_window'):
            await self.manager.open_manual('brave')
        # connect is called twice: once by ensure_launched, once to close the inspect tab via CDP
        self.assertEqual(order[:3], ['inspect', 'toggle', 'connect'])

    async def test_repeated_connect_uses_existing_session_without_opening_settings(self):
        endpoint = ('', 'ws://127.0.0.1:1234/devtools/browser/chrome')
        connection = AsyncMock()
        with patch('orion.browser.manager.require_desktop'), \
                patch('orion.browser.manager.discover_endpoint', return_value=endpoint), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)), \
                patch('orion.browser.manager.open_inspect_window', AsyncMock()) as inspect:
            await self.manager.open_manual('chrome')
        inspect.assert_not_awaited()
        self.assertTrue(self.manager._read(self.manager.config)['enabled'])

    async def test_current_browser_suggested_before_saved_choice(self):
        self.manager.directory.mkdir()
        self.manager._write(self.manager.config, {'mode': 'existing', 'browser': 'chrome', 'enabled': True})
        with patch('orion.browser.manager.suggested_browser', return_value='brave'):
            state = await self.manager.status()
        self.assertEqual(state['suggested_browser'], 'brave')
        self.assertEqual(state['browser'], 'chrome')

    async def test_startup_restores_only_saved_browser_in_background(self):
        from orion.browser.manager import restore_saved_browsers
        self.manager.directory.mkdir()
        self.manager._write(self.manager.config, {'mode': 'existing', 'browser': 'brave', 'enabled': True})
        with patch('orion.browser.manager.os.name', 'nt'), \
                patch('orion.browser.manager.running_browser_choices', return_value=set()), \
                patch('orion.browser.manager.find_binary', return_value='brave.exe'), \
                patch('orion.browser.manager.subprocess.STARTUPINFO') as startup, \
                patch('orion.browser.manager.watch_browser_reconnect', AsyncMock()), \
                patch('orion.browser.manager.subprocess.Popen') as launch:
            await restore_saved_browsers()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][0], 'brave.exe')
        self.assertNotIn('--remote-debugging-port', launch.call_args.args[0])
        self.assertEqual(startup.return_value.wShowWindow, 7)

    async def test_startup_keeps_already_running_browser_untouched(self):
        from orion.browser.manager import restore_saved_browsers
        self.manager.directory.mkdir()
        self.manager._write(self.manager.config, {'mode': 'existing', 'browser': 'brave', 'enabled': True})
        with patch('orion.browser.manager.os.name', 'nt'), \
                patch('orion.browser.manager.running_browser_choices', return_value={'brave'}), \
                patch('orion.browser.manager.subprocess.Popen') as launch:
            await restore_saved_browsers()
        launch.assert_not_called()

    async def test_resume_reconnects_and_checks_response_before_clearing_pause(self):
        from orion.browser.cdp import CDPConnectionLost
        self.manager.directory.mkdir()
        original = {'mode': 'existing', 'browser': 'brave', 'enabled': True, 'manual': True}
        self.manager._write(self.manager.config, original)
        connection = AsyncMock()
        connection.send.side_effect = CDPConnectionLost('closed')
        endpoint = 'ws://127.0.0.1:1234/devtools/browser/brave'
        with patch('orion.browser.manager.require_desktop'), \
                patch.object(self.manager, 'endpoint', AsyncMock(return_value=('', endpoint))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)) as connect:
            with self.assertRaises(BrowserUnavailable):
                await self.manager.resume()
            self.assertEqual(self.manager._read(self.manager.config), original)
            connect.assert_awaited_once_with(endpoint, authorize=True)
            connection.send.side_effect = None
            connection.send.return_value = {'product': 'Brave'}
            await self.manager.resume()
        self.assertFalse(self.manager._read(self.manager.config)['manual'])
        self.assertTrue(self.manager._read(self.manager.config)['enabled'])

    async def test_hidden_failure_never_opens_visible_fallback(self):
        from orion.browser.cdp import CDPError
        connection = MagicMock(closed=False)
        connection.send = AsyncMock(side_effect=CDPError('Target.createTarget', {'message': 'unsupported'}))
        with patch.object(self.manager, 'ensure_launched', AsyncMock(return_value=('', 'ws://127.0.0.1:1234/devtools/browser/failure'))), patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            with self.assertRaises(BrowserUnavailable):
                async with self.manager.tab('google_ai', background=True):
                    self.fail('Unsupported hidden target must fail')
        self.assertEqual(connection.send.await_count, 1)

    async def test_media_tab_is_real_but_created_without_focus(self):
        connection = MagicMock(closed=False)
        connection.send = AsyncMock(side_effect=[
            {'targetInfos': []}, {'targetId': 'media'}, {'sessionId': 'session'}, {}])
        with patch.object(self.manager, 'ensure_launched', AsyncMock(return_value=(
                '', 'ws://127.0.0.1:1234/devtools/browser/media'))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            async with self.manager.tab('youtube', inactive=True) as page:
                self.assertEqual(page.target_id, 'media')
        methods = [c.args[0] for c in connection.send.call_args_list]
        self.assertIn('Target.createTarget', methods)
        self.assertNotIn('Target.activateTarget', methods)
        self.assertNotIn('Browser.setWindowBounds', methods)
        self.assertNotIn('Target.closeTarget', methods)
        connection.send.assert_any_await('Target.createTarget', {
            'url': 'about:blank', 'background': True, 'focus': False})


class MockConnection:
    closed = False
    def __init__(self):
        self.send = AsyncMock(side_effect=[{'targetId': 'hidden'}, {'sessionId': 'session'}, {}, {}, {}])
