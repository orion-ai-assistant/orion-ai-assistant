import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from orion.worker.tools.youtube import tools as youtube
from orion.worker.tools.ytmusic import tools as ytmusic
from orion.worker.tools.web_browser import tools as browser
from orion.browser.cdp import CDPConnectionLost
from orion.browser.page import BrowserPage
from orion.browser.manager import BrowserManager, BrowserTabMissing


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

    async def test_youtube_navigation_returns_after_commit(self):
        page = BrowserPage(None, 'session', 'target', None)
        page.send = AsyncMock(side_effect=[
            {'frameTree': {'frame': {'loaderId': 'old'}}},
            {'loaderId': 'requested'},
            {'frameTree': {'frame': {'loaderId': 'requested'}}}])
        page.evaluate = AsyncMock()
        await page.navigate('https://www.youtube.com/watch?v=test', wait_ready=False)
        page.evaluate.assert_not_awaited()

    async def test_user_gesture_evaluation_stays_in_attached_tab(self):
        page = BrowserPage(None, 'session', 'target', None)
        page.send = AsyncMock(return_value={'result': {'value': {'ok': True}}})
        result = await page.evaluate('document.querySelector("video").play()', user_gesture=True)
        self.assertEqual(result, {'ok': True})
        self.assertTrue(page.send.await_args.args[1]['userGesture'])

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
            state = {'title': 'Video', 'paused': True, 'current_time': 1,
                     'muted': False, 'volume': 1, 'ad_showing': False}
            page.evaluate.side_effect = (['https://www.youtube.com/watch?v=test', True, True,
                                          True, state] if already_paused else
                                         ['https://www.youtube.com/watch?v=test', True, False,
                                          None, True, state])
            with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)):
                result = await youtube.youtube_control(youtube.YouTubeInput(action='pause'), SimpleNamespace(user_id='alice'))
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['paused'])
            page.click.assert_not_awaited()

    async def test_youtube_open_does_not_activate_tab(self):
        page = AsyncMock()
        page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', True,
                                     {'url': 'https://www.youtube.com/watch?v=test',
                                      'title': 'Video', 'paused': False,
                                      'current_time': 1, 'duration': 10,
                                      'muted': False, 'volume': 1, 'ad_showing': False}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='open', target='https://www.youtube.com/watch?v=test'),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        manager.tab.assert_called_once_with('youtube', inactive=True, create=True)
        page.send.assert_awaited_once_with('Emulation.setFocusEmulationEnabled', {'enabled': True})
        page.navigate.assert_awaited_once()
        page.connection.send.assert_not_awaited()

    async def test_youtube_open_reports_stalled_playback(self):
        page = AsyncMock()
        page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', True,
                                     {'url': 'https://www.youtube.com/watch?v=test',
                                                   'paused': False, 'current_time': 0,
                                                   'duration': None, 'muted': False, 'volume': 1,
                                                   'ad_showing': False}]
        with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=False)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={
                    'ok': False, 'error': 'NotAllowedError'})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='open', target='https://www.youtube.com/watch?v=test'),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'playback_unconfirmed')
        self.assertEqual(result['play_error'], 'NotAllowedError')
        page.send.assert_awaited_once_with('Emulation.setFocusEmulationEnabled', {'enabled': True})
        page.click.assert_not_awaited()

    async def test_youtube_open_accepts_query_and_navigates_to_search_result(self):
        page = AsyncMock()
        page.evaluate.side_effect = [
            [{'title': 'Lust - Kasmicam', 'target': 'video:5USWa2wb2Pk'}],
            'https://www.youtube.com/watch?v=5USWa2wb2Pk', True,
            {'url': 'https://www.youtube.com/watch?v=5USWa2wb2Pk',
             'title': 'Video', 'paused': False, 'current_time': 1, 'duration': 10,
             'muted': False, 'volume': 1, 'ad_showing': False}]
        with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='open', query='lust kasmıcam'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(page.navigate.await_args_list[1].args[0],
                         'https://www.youtube.com/watch?v=5USWa2wb2Pk')

    async def test_play_query_searches_instead_of_waiting_on_blank_tab(self):
        page = AsyncMock()
        page.evaluate.side_effect = [
            [{'title': 'Lust - Kasmicam', 'target': 'video:5USWa2wb2Pk'}],
            'https://www.youtube.com/watch?v=5USWa2wb2Pk', True,
            {'url': 'https://www.youtube.com/watch?v=5USWa2wb2Pk',
             'title': 'Video', 'paused': False, 'current_time': 1, 'duration': 10,
             'muted': False, 'volume': 1, 'ad_showing': False}]
        with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='play', query='lust kasmıcam'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(page.navigate.await_count, 2)
        page.send.assert_awaited_once_with('Emulation.setFocusEmulationEnabled', {'enabled': True})

    async def test_search_result_plays_in_the_search_tab(self):
        page = AsyncMock()
        page.evaluate.return_value = [{'title': 'Song', 'target': 'video:5USWa2wb2Pk'}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            search = await youtube.youtube_control(youtube.YouTubeInput(
                action='search', query='Song'), SimpleNamespace(user_id='alice'))
            page.evaluate.side_effect = ['https://www.youtube.com/watch?v=5USWa2wb2Pk', True,
                                         {'title': 'Song', 'paused': False, 'current_time': 1,
                                          'muted': False, 'volume': 1, 'ad_showing': False}]
            with patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                    patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
                played = await youtube.youtube_control(youtube.YouTubeInput(
                    action='play', target=search['results'][0]['target']), SimpleNamespace(user_id='alice'))
        self.assertEqual(played['status'], 'ok')
        self.assertEqual([call.args[0] for call in manager.tab.call_args_list],
                         ['youtube-search', 'youtube-search'])
        self.assertEqual(page.navigate.await_args_list[1].args[0],
                         'https://www.youtube.com/watch?v=5USWa2wb2Pk')

    async def test_play_video_id_replaces_selected_existing_tab(self):
        page = AsyncMock(target_id='current-tab')
        page.evaluate.side_effect = [
            'https://www.youtube.com/watch?v=old',
            'https://www.youtube.com/watch?v=B-1ZzOp0UUA',
            True,
            {'title': 'New song', 'paused': False, 'current_time': 1,
             'muted': False, 'volume': 1, 'ad_showing': False}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='play', target='current-tab', query='video:B-1ZzOp0UUA'),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['tab_id'], 'current-tab')
        manager.tab.assert_called_once_with('youtube', target_id='current-tab')
        page.navigate.assert_awaited_once_with('https://www.youtube.com/watch?v=B-1ZzOp0UUA',
                                               wait_ready=False)

    async def test_tabs_lists_connected_browser_without_creating_tab(self):
        manager = MagicMock()
        manager.list_tabs = AsyncMock(return_value=[
            {'id': 'tab-1', 'title': 'Example', 'url': 'https://example.com'},
            {'id': 'tab-2', 'title': 'Video', 'url': 'https://youtube.com/watch?v=abc'}])
        with patch.object(browser, 'BrowserManager', return_value=manager):
            result = await browser.browser_tabs(browser.BrowserTabsInput(query='youtube'),
                                                SimpleNamespace(user_id='alice', chat_id='chat'))
        self.assertEqual([tab['id'] for tab in result['tabs']], ['tab-2'])
        self.assertNotIn('url', result['tabs'][0])
        with patch.object(browser, 'BrowserManager', return_value=manager):
            detailed = await browser.browser_tabs(browser.BrowserTabsInput(
                query='youtube', include_urls=True), SimpleNamespace(user_id='alice', chat_id='chat'))
        self.assertEqual(detailed['tabs'][0]['url'], 'https://youtube.com/watch?v=abc')
        manager.tab.assert_not_called()

    async def test_youtube_controls_existing_tab_without_creating_one(self):
        page = AsyncMock(target_id='tab-2')
        page.evaluate.side_effect = ['https://youtube.com/watch?v=abc',
                                     'https://youtube.com/watch?v=abc', True,
                                     {'url': 'https://youtube.com/watch?v=abc', 'title': 'Video',
                                      'paused': True, 'current_time': 35, 'duration': 120,
                                      'muted': False, 'volume': 1, 'ad_showing': False}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='status', target='tab-2'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['current_time'], 35)
        manager.tab.assert_called_once_with('youtube', target_id='tab-2')

    async def test_resume_without_saved_tab_does_not_open_blank_page(self):
        manager = MagicMock()
        manager.tab.side_effect = BrowserTabMissing('Sekme açık değil')
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(action='play'),
                                                   SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'no_video')
        manager.tab.assert_called_once_with('youtube', inactive=True, create=False)

    async def test_stale_tab_id_is_not_a_browser_connection_error(self):
        manager = MagicMock()
        manager.tab.side_effect = BrowserTabMissing('Seçilen sekme artık açık değil')
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='play', target='stale-tab'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'tab_missing')
        manager.tab.assert_called_once_with('youtube', target_id='stale-tab')

        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='search', query='Attack on Titan', target=' '),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'no_video')
        manager.tab.assert_called_with('youtube-search', inactive=True, create=True)

    def test_blank_optional_youtube_fields_are_omitted(self):
        args = youtube.YouTubeInput(action='search', query='Attack on Titan', target=' ')
        self.assertIsNone(args.target)

    async def test_play_new_uses_separate_tab_without_touching_others(self):
        page = AsyncMock()
        page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', True,
                                     {'title': 'Video', 'paused': False, 'current_time': 1,
                                      'muted': False, 'volume': 1, 'ad_showing': False}]
        manager = self.manager(page)
        with patch.object(youtube, 'BrowserManager', return_value=manager), \
                patch.object(youtube, '_time_advanced', AsyncMock(return_value=True)), \
                patch.object(youtube, '_play_in_background', AsyncMock(return_value={'ok': True})):
            result = await youtube.youtube_control(youtube.YouTubeInput(
                action='play_new', target='https://www.youtube.com/watch?v=test'),
                SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(manager.tab.call_args.args[0].startswith('youtube:'))
        self.assertEqual(manager.tab.call_args.kwargs, {'inactive': True, 'create': True})
        page.connection.send.assert_not_awaited()

    async def test_now_playing_checks_video_tabs_in_one_tool_call(self):
        active = peak = 0
        async def send(method, params=None, *, session_id=None, timeout=10):
            nonlocal active, peak
            if method == 'Target.getTargets':
                return {'targetInfos': [
                    {'targetId': 'paused', 'type': 'page', 'title': 'Paused song',
                     'url': 'https://www.youtube.com/watch?v=one'},
                    {'targetId': 'playing', 'type': 'page', 'title': 'Playing song',
                     'url': 'https://www.youtube.com/watch?v=two'},
                    {'targetId': 'search', 'type': 'page', 'url': 'https://www.youtube.com/results?search_query=music'}]}
            if method == 'Target.attachToTarget':
                return {'sessionId': params['targetId']}
            if method == 'Runtime.evaluate':
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(.01)
                active -= 1
                return {'result': {'value': {'paused': session_id == 'paused',
                    'ended': False, 'ready': 4, 'muted': False}}}
            return {}
        connection = MagicMock(closed=False)
        connection.send = AsyncMock(side_effect=send)
        lease = MagicMock()
        lease.__aenter__ = AsyncMock(return_value=connection)
        lease.__aexit__ = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.connected.return_value = lease
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(action='now_playing'),
                                                   SimpleNamespace(user_id='alice'))
        self.assertEqual(result, {'status': 'ok', 'playing': [
            {'tab_id': 'playing', 'title': 'Playing song', 'muted': False}]})
        self.assertEqual(peak, 2)
        manager.tab.assert_not_called()

    async def test_browser_control_uses_selected_existing_tab(self):
        page = AsyncMock()
        page.snapshot.return_value = {'url': 'https://example.com'}
        manager = self.manager(page)
        with patch.object(browser, 'BrowserManager', return_value=manager):
            result = await browser.browser_control(browser.BrowserInput(
                action='read', tab_id='tab-1'), SimpleNamespace(user_id='alice', chat_id='chat'))
        self.assertEqual(result['page']['url'], 'https://example.com')
        # Without visible=True the agent works in the tab without bringing it to the front.
        manager.tab.assert_called_once_with('browser:chat', target_id='tab-1', inactive=True)

    async def test_list_tabs_reads_existing_targets(self):
        manager = BrowserManager('alice')
        connection = AsyncMock()
        connection.send.return_value = {'targetInfos': [
            {'targetId': 'one', 'type': 'page', 'title': 'Example', 'url': 'https://example.com'},
            {'targetId': 'worker', 'type': 'service_worker', 'title': 'Worker', 'url': 'https://example.com/sw'},
            {'targetId': 'two', 'type': 'page', 'title': 'Settings', 'url': 'chrome://settings'},
            {'targetId': 'three', 'type': 'page', 'title': '', 'url': 'about:blank'}]}
        with patch('orion.browser.manager.file_lock', return_value=MagicMock(
                __enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))), \
                patch.object(manager, '_read', return_value={'mode': 'existing', 'manual': False}), \
                patch.object(manager, 'ensure_launched', AsyncMock(return_value=('', 'endpoint'))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            tabs = await manager.list_tabs()
        self.assertEqual(tabs, [
            {'id': 'one', 'title': 'Example', 'url': 'https://example.com'},
            {'id': 'two', 'title': 'Settings', 'url': 'chrome://settings'},
            {'id': 'three', 'title': '', 'url': 'about:blank'}])
        connection.send.assert_awaited_once_with('Target.getTargets')

    async def test_existing_tab_does_not_create_a_target(self):
        manager = BrowserManager('alice')
        connection = MagicMock(closed=False)
        connection.send = AsyncMock(side_effect=[
            {'targetInfos': [{'targetId': 'tab-1', 'type': 'page'}]},
            {'sessionId': 'session'}, {}])
        with patch('orion.browser.manager.file_lock', return_value=MagicMock(
                __enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))), \
                patch.object(manager, '_read', return_value={'mode': 'existing', 'manual': False}), \
                patch.object(manager, 'ensure_launched', AsyncMock(return_value=('', 'endpoint'))), \
                patch('orion.browser.manager.get_connection', AsyncMock(return_value=connection)):
            async with manager.tab('browser:chat', target_id='tab-1') as page:
                self.assertEqual(page.target_id, 'tab-1')
        self.assertNotIn('Target.createTarget', [call.args[0] for call in connection.send.call_args_list])

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

    async def test_youtube_next_and_previous_actions(self):
        for action in ('next', 'previous'):
            page = AsyncMock()
            state = {'title': 'Song 2', 'paused': False, 'current_time': 0,
                     'muted': False, 'volume': 0.8, 'ad_showing': False}
            page.evaluate.side_effect = ['https://www.youtube.com/watch?v=test', 'https://www.youtube.com/watch?v=test', True, {**state, 'title': 'Song 1'}, True, state]
            with patch.object(youtube, 'BrowserManager', return_value=self.manager(page)):
                result = await youtube.youtube_control(youtube.YouTubeInput(action=action, target='tab-1'), SimpleNamespace(user_id='alice'))
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['title'], 'Song 2')

    async def test_ytmusic_control_actions_and_tab_discovery(self):
        # 1. Test next and previous actions
        for action in ('next', 'previous'):
            page = AsyncMock()
            state = {'title': 'Şımarık - Tarkan | YouTube Music', 'paused': False, 'current_time': 10,
                     'muted': False, 'volume': 0.8, 'ad_showing': False}
            page.evaluate.side_effect = ['https://music.youtube.com/', 'https://music.youtube.com/', True, {**state, 'title': 'Old song'}, True, state]
            with patch.object(ytmusic, '_find_existing_ytmusic_tab', AsyncMock(return_value='tab-ytm')), \
                    patch.object(ytmusic, 'BrowserManager', return_value=self.manager(page)):
                result = await ytmusic.ytmusic_control(ytmusic.YTMusicInput(action=action), SimpleNamespace(user_id='alice'))
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['title'], 'Şımarık - Tarkan | YouTube Music')

        # 2. Test pause action
        page = AsyncMock()
        state = {'title': 'Şımarık - Tarkan | YouTube Music', 'paused': True, 'current_time': 10,
                 'muted': False, 'volume': 0.8, 'ad_showing': False}
        page.evaluate.side_effect = ['https://music.youtube.com/', 'https://music.youtube.com/', True, True, True, state]
        with patch.object(ytmusic, '_find_existing_ytmusic_tab', AsyncMock(return_value='tab-ytm')), \
                patch.object(ytmusic, 'BrowserManager', return_value=self.manager(page)):
            result = await ytmusic.ytmusic_control(ytmusic.YTMusicInput(action='pause'), SimpleNamespace(user_id='alice'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['title'], 'Şımarık - Tarkan | YouTube Music')

    async def test_browser_tabs_warns_on_captcha_url(self):
        tabs = [
            {'id': 'tab-1', 'title': 'Gemini', 'url': 'https://www.google.com/sorry/index?continue=https://gemini.google.com/'},
            {'id': 'tab-2', 'title': 'Normal Page', 'url': 'https://example.com'}
        ]
        with patch.object(browser.BrowserManager, 'list_tabs', AsyncMock(return_value=tabs)):
            result = await browser.browser_tabs(browser.BrowserTabsInput(include_urls=False), SimpleNamespace(user_id='alice'))
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['tabs'][0]['warning'], 'Google bot doğrulaması (CAPTCHA) sayfasında takılı')
            self.assertNotIn('warning', result['tabs'][1])

    async def test_browser_control_detects_captcha_and_activates(self):
        page = AsyncMock()
        page.snapshot.return_value = {
            'url': 'https://www.google.com/sorry/index?continue=https://gemini.google.com/',
            'title': 'Google Sorry',
            'elements': []
        }
        with patch.object(browser, 'BrowserManager', return_value=self.manager(page)):
            result = await browser.browser_control(browser.BrowserInput(action='read'), SimpleNamespace(user_id='alice', chat_id='chat-1'))
            self.assertEqual(result['status'], 'verification_required')
            self.assertIn('robot doğrulaması', result['message'])
            page.activate.assert_awaited_once()
