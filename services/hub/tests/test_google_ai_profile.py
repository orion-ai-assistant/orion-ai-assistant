import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from orion.api import google_ai_routes as routes
from orion.worker.tools._base import ToolContext
from orion.worker.tools.google_ai import profile, tools


class GoogleProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'profiles'
        self.patcher = patch.object(profile, 'profile_root', return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_profiles_isolated_and_delete_cannot_interrupt_browser(self):
        user = '../../alice'
        with profile.profile_lock(user) as directory:
            self.assertEqual(directory.parent, self.root)
            directory.mkdir()
            (directory / '.orion-enabled').touch()
            self.assertEqual(profile.profile_status(user), {'saved': True, 'busy': True})
            with self.assertRaises(profile.ProfileBusy):
                profile.delete_profile(user)
            with profile.profile_lock('bob') as other:
                other.mkdir()
                (other / '.orion-enabled').touch()
        profile.delete_profile(user)
        self.assertFalse(directory.exists())
        self.assertTrue(other.exists())

    async def test_busy_does_not_start_browser_and_blocked_offers_recovery(self):
        context = ToolContext('alice', 'chat', 'turn', AsyncMock(return_value=False))
        args = tools.GoogleAIInput(query='example')
        with profile.profile_lock('alice'), patch.object(tools, '_read_browser', AsyncMock()) as reader:
            result = await tools.google_ai_mode(args, context)
            self.assertEqual(result['status'], 'busy')
            reader.assert_not_awaited()
        with patch.object(tools, '_read_browser', AsyncMock(return_value={
            'status': 'blocked', 'message': 'CAPTCHA',
        })) as reader:
            result = await tools.google_ai_mode(args, context)
            self.assertEqual(reader.call_args.args[1], 'alice')
            self.assertEqual(result['recovery'], 'google_session')
            self.assertNotIn('answer', result)
        self.assertFalse(profile.profile_status('alice')['busy'])

    async def test_saved_profile_used_and_closed_on_block(self):
        try:
            from playwright import async_api
        except ImportError:
            self.skipTest('Optional Playwright dependency is not installed')
        directory = profile.profile_dir('alice')
        directory.mkdir(parents=True)
        (directory / '.orion-enabled').touch()
        page = AsyncMock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.url = 'https://www.google.com/sorry/index'
        page.evaluate.return_value = {'body': 'unusual traffic'}
        context = AsyncMock()
        context.new_page.return_value = page
        page.set_default_timeout = Mock()
        chromium = SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context), launch=AsyncMock())
        manager = AsyncMock()
        manager.__aenter__.return_value = SimpleNamespace(chromium=chromium)
        with patch.object(async_api, 'async_playwright', return_value=manager), \
                patch.object(tools, '_compatible_user_agent', AsyncMock(return_value='Chrome/test')):
            result = await tools._read_browser('https://www.google.com/search?udm=50', 'alice')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(chromium.launch_persistent_context.call_args.args[0], str(directory))
        self.assertTrue(chromium.launch_persistent_context.call_args.kwargs['headless'])
        self.assertEqual(chromium.launch_persistent_context.call_args.kwargs['user_agent'], 'Chrome/test')
        chromium.launch.assert_not_awaited()
        context.close.assert_awaited_once()

    async def test_local_only_and_authenticated_identity_used(self):
        remote = SimpleNamespace(client=SimpleNamespace(host='203.0.113.1'))
        with self.assertRaises(HTTPException) as error:
            await routes.session_open(routes.OpenSession(), remote, 'alice')
        self.assertEqual(error.exception.status_code, 403)
        with patch.object(routes, 'require_local'), patch.object(routes, 'launch_login') as launch:
            await routes.session_open(routes.OpenSession(verify=True, query='a&b Türkçe'), remote, 'alice')
            launch.assert_called_once_with('alice', True, 'a&b Türkçe')

    def test_verification_opens_real_query_and_login_continues_to_it(self):
        from urllib.parse import parse_qs, urlsplit
        from orion.worker.tools.google_ai.login import session_url
        query = 'a&b Türkçe?'
        search = session_url(query, True)
        self.assertEqual(parse_qs(urlsplit(search).query), {'q': [query], 'udm': ['50'], 'hl': ['tr']})
        self.assertEqual(parse_qs(urlsplit(session_url(query, False)).query)['continue'], [search])

    async def test_cancel_releases_profile_and_does_not_expose_browser_error(self):
        import asyncio
        context = ToolContext('alice', 'chat', 'turn', AsyncMock(return_value=False))
        with patch.object(tools, '_read_browser', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await tools.google_ai_mode(tools.GoogleAIInput(query='test'), context)
        self.assertFalse(profile.profile_status('alice')['busy'])
        with patch.object(tools, '_read_browser', AsyncMock(side_effect=RuntimeError('private cookie data'))):
            result = await tools.google_ai_mode(tools.GoogleAIInput(query='test'), context)
        self.assertNotIn('private cookie', str(result))
