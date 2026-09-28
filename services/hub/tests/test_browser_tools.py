import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from orion.worker.tools.youtube import tools as youtube
from orion.worker.tools.browser import tools as browser
from orion.browser.cdp import CDPConnectionLost
from orion.browser.page import BrowserPage


class BrowserToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_accepts_followup_redirect_loader(self):
        page = BrowserPage(None, 'session', 'target', None)
        page.send = AsyncMock(side_effect=[
            {'frameTree': {'frame': {'loaderId': 'old'}}},
            {'loaderId': 'requested'},
            {'frameTree': {'frame': {'loaderId': 'redirected'}}}])
        page.evaluate = AsyncMock(return_value=True)
        await page.navigate('https://www.youtube.com/results?search_query=test')
        page.evaluate.assert_awaited_once()

    def manager(self, page):
        lease = MagicMock()
        lease.__aenter__ = AsyncMock(return_value=page)
        lease.__aexit__ = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.tab.return_value = lease
        return manager

    async def test_pause_is_idempotent_and_verified(self):
        for already_paused in (True, False):
            page = AsyncMock()
            page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', True, already_paused,
                                         True, {'paused': True}]
            with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)):
                result = await youtube.youtube_control(youtube.YouTubeInput(action='pause'), SimpleNamespace(user_id='alice'))
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['paused'])
            self.assertEqual(page.click.await_count, 0 if already_paused else 1)

    async def test_youtube_open_keeps_orion_tab_in_foreground(self):
        page = AsyncMock()
        page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', True,
                                     {'url': 'https://www.youtube.com/watch?v=test',
                                      'title': 'Video', 'paused': False,
                                      'current_time': 1, 'duration': 10}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='open', url='https://www.youtube.com/watch?v=test'),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        manager.tab.assert_called_once_with('youtube', inactive=True)
        page.foreground.assert_not_awaited()
        page.navigate.assert_awaited_once()

    async def test_verification_does_not_click_consent(self):
        page = AsyncMock()
        page.evaluate.return_value = 'https://consent.youtube.com/m'
        with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)):
            result = await youtube.youtube_control(youtube.YouTubeInput(action='status'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'verification_required')
        page.click.assert_not_awaited()
        page.require_verification.assert_awaited_once()

    async def test_uncertain_click_is_not_replayed(self):
        page = AsyncMock()
        page.click.side_effect = CDPConnectionLost()
        with patch.object(browser, 'BrowserManager', return_value=self.manager(page)):
            result = await browser.browser_control(browser.BrowserInput(action='click', selector='#submit'),
                                                   SimpleNamespace(user_id='alice', chat_id='chat'))
        self.assertEqual(result['status'], 'outcome_unknown')
        page.click.assert_awaited_once()
