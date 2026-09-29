import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
from orion.worker.tools import _media
from orion.worker.tools.youtube import tools as youtube
from orion.worker.tools.ytmusic import tools as music
from orion.worker.tools.browser import tools as browser
from orion.worker.services.tool_history import model_history


class MediaEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def state(self, **changes):
        return dict(title='Song', url='https://music.youtube.com/watch?v=abcdefghijk',
                    paused=True, current_time=120.123456, duration=240.987654,
                    volume=1, muted=False, ad_showing=False, error=None, ready_state=4, **changes)

    def test_seek_validation(self):
        for model in (youtube.YouTubeInput, music.YTMusicInput):
            for seconds in (None, -1, float('inf'), float('nan')):
                with self.assertRaises(ValidationError):
                    model(action='seek', seconds=seconds)
            self.assertEqual(model(action='seek', seconds=0).seconds, 0)
            with self.assertRaises(ValidationError):
                model(action='seek', seconds=120, query='another song')

    async def test_seek_uses_existing_tab_without_navigation_or_snapshot(self):
        for module, model, handler, url in (
            (youtube, youtube.YouTubeInput, youtube.youtube_control, 'https://www.youtube.com/watch?v=abcdefghijk'),
            (music, music.YTMusicInput, music.ytmusic_control, 'https://music.youtube.com/watch?v=abcdefghijk')):
            page = AsyncMock(target_id='tab')
            page.evaluate.side_effect = [url, url, True, self.state(),
                                         {'status': 'seeking', 'target': 120, 'mode': 'player_bar', 'x': 300, 'y': 50} if module is music
                                         else {'status': 'seeking', 'target': 120}, self.state(), self.state()]
            manager = MagicMock()
            manager.tab.return_value.__aenter__ = AsyncMock(return_value=page)
            manager.tab.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch.object(module, 'BrowserManager', return_value=manager):
                result = await handler(model(action='seek', seconds=120, target='tab'), SimpleNamespace(user_id='u'))
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['current_time'], 120.1)
            self.assertTrue(result['paused'])
            page.navigate.assert_not_awaited()
            page.snapshot.assert_not_awaited()
            if module is music:
                self.assertEqual([call.args[0] for call in page.send.await_args_list],
                                 ['Input.dispatchMouseEvent', 'Input.dispatchMouseEvent'])

    async def test_music_seek_uses_player_even_when_media_duration_differs(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 60, 'mode': 'player_api'}
        state = {**self.state(), 'ui_current_time': 9, 'ui_duration': 386,
                 'duration': 399.1, 'current_time': 9}
        reached = {**state, 'ui_current_time': 60, 'current_time': 60}
        advanced = {**reached, 'ui_current_time': 61, 'current_time': 60.5}
        later = {**advanced, 'current_time': 61}
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 60,
                AsyncMock(side_effect=[state, reached, advanced, later]), music=True)
        self.assertEqual(result['status'], 'ok')
        page.send.assert_not_awaited()

    async def test_music_seek_uses_player_timeline_when_video_has_offset(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 60, 'mode': 'player_api'}
        before = {**self.state(), 'current_time': 95, 'player_time': 95,
                  'ui_current_time': 95, 'duration': 386, 'ui_duration': 386,
                  'media_time': 434, 'paused': False, 'player_state': 1}
        reached = {**before, 'current_time': 60, 'player_time': 60,
                   'ui_current_time': 60, 'media_time': 399}
        advanced = {**reached, 'current_time': 60.5, 'player_time': 60.5,
                    'ui_current_time': 60, 'media_time': 399.5}
        later = {**advanced, 'current_time': 61, 'player_time': 61,
                 'ui_current_time': 61, 'media_time': 400}
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 60,
                AsyncMock(side_effect=[before, reached, advanced, later]), music=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['current_time'], 61)

    async def test_music_seek_checks_displayed_clock_not_only_video_time(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 60, 'mode': 'player_bar', 'x': 150, 'y': 40}
        before = {**self.state(), 'current_time': 9, 'ui_current_time': 9,
                  'ui_duration': 241}
        reached = {**before, 'current_time': 60, 'ui_current_time': 9}
        with patch.object(_media, 'monotonic', side_effect=[0, 1, 6]), \
                patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 60, AsyncMock(side_effect=[before, reached]), music=True)
        self.assertEqual(result['status'], 'seek_unconfirmed')
        self.assertEqual(page.send.await_count, 2)

    async def test_seek_failure_is_not_success(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 120}
        with patch.object(_media, 'monotonic', side_effect=[0, 1, 5]):
            result = await _media.seek(page, 120, AsyncMock(return_value={'current_time': 5}))
        self.assertEqual(result['status'], 'seek_unconfirmed')
        page.evaluate.assert_awaited_once()

    async def test_seek_reset_to_zero_after_initial_120_is_unconfirmed(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 120}
        before = self.state()
        reached = {**before, 'paused': False, 'current_time': 120}
        reset = {**reached, 'current_time': 0}
        read = AsyncMock(side_effect=[before, reached, reset])
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 120, read)
        self.assertEqual(result['status'], 'seek_unconfirmed')
        self.assertEqual(result['current_time'], 0)

    async def test_seek_requires_progress_for_playing_media(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 120}
        state = {**self.state(), 'paused': False, 'current_time': 120}
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 120, AsyncMock(return_value=state))
        self.assertEqual(result['status'], 'seek_unconfirmed')

    async def test_seek_confirms_progress_and_same_track(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 120}
        before = self.state()
        reached = {**before, 'paused': False, 'current_time': 120}
        advanced = {**reached, 'current_time': 120.5}
        later = {**reached, 'current_time': 121}
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.seek(page, 120, AsyncMock(side_effect=[before, reached, advanced, later]))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['current_time'], 121)

    async def test_seek_changed_track_is_unconfirmed(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = {'status': 'seeking', 'target': 120}
        before = self.state()
        after = {**before, 'title': 'Other song', 'current_time': 120}
        result = await _media.seek(page, 120, AsyncMock(side_effect=[before, after]))
        self.assertEqual(result['status'], 'seek_unconfirmed')

    async def test_skip_waits_for_change_and_clicks_only_once(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = True
        before = self.state()
        after = {**before, 'title': 'Next song'}
        read = AsyncMock(side_effect=[before, before, after])
        with patch.object(_media.asyncio, 'sleep', new_callable=AsyncMock):
            result = await _media.skip(page, 'next', read, music._playback_result, music=True)
        self.assertEqual(result['title'], 'Next song')
        self.assertEqual(result['status'], 'ok')
        page.evaluate.assert_awaited_once()

    async def test_unchanged_track_never_reports_success(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = True
        with patch.object(_media, 'monotonic', side_effect=[0, 1, 6]):
            result = await _media.skip(page, 'next', AsyncMock(return_value=self.state()), music._playback_result)
        self.assertEqual(result['status'], 'transition_unconfirmed')
        page.evaluate.assert_awaited_once()

    async def test_missing_skip_button_does_not_fake_shortcut_success(self):
        page = AsyncMock(target_id='tab')
        page.evaluate.return_value = False
        result = await _media.skip(page, 'next', AsyncMock(return_value=self.state()), music._playback_result)
        self.assertEqual(result['status'], 'control_unavailable')

    def test_old_media_history_is_compact_without_mutating_display(self):
        result = dict(status='muted', tab_id='tab', **self.state())
        result['volume'] = 0
        result['message'] = 'Muted playback'
        history = [{'role': 'user', 'content': 'play'},
                   {'role': 'assistant', 'content': None, 'tool_calls': [
                       {'id': 'one', 'function': {'name': 'ytmusic_control', 'arguments': '{}'}}]},
                   {'role': 'tool', 'tool_call_id': 'one', 'content': json.dumps(result)}]
        original = copy.deepcopy(history)
        compact = json.loads(model_history(history, 20)[2]['content'])
        self.assertEqual(compact['status'], 'muted')
        self.assertTrue(compact['muted'])
        self.assertEqual(compact['message'], 'Muted playback')
        self.assertNotIn('url', compact)
        self.assertNotIn('volume', compact)
        self.assertEqual(history, original)

    async def test_browser_read_is_compact_with_explicit_detail_option(self):
        page = AsyncMock()
        manager = MagicMock()
        manager.tab.return_value.__aenter__ = AsyncMock(return_value=page)
        for detailed in (False, True):
            with patch.object(browser, 'BrowserManager', return_value=manager):
                await browser.browser_control(browser.BrowserInput(action='read', detailed=detailed),
                                              SimpleNamespace(user_id='u', chat_id='c'))
            page.snapshot.assert_awaited_with(compact=not detailed)

    async def test_resume_missing_music_tab_does_not_open_empty_page(self):
        with patch.object(music, '_find_existing_ytmusic_tab', AsyncMock(return_value=None)), \
                patch.object(music, 'BrowserManager') as manager:
            result = await music.ytmusic_control(music.YTMusicInput(action='play'), SimpleNamespace(user_id='u'))
        self.assertEqual(result['status'], 'no_tab')
        manager.assert_not_called()

    async def test_playing_music_blocks_implicit_seek_on_paused_video(self):
        connection = MagicMock(closed=False)
        async def send(method, params=None, **kwargs):
            if method == 'Target.getTargets':
                return {'targetInfos': [
                    {'type': 'page', 'targetId': 'video', 'url': 'https://www.youtube.com/watch?v=abcdefghijk'},
                    {'type': 'page', 'targetId': 'music', 'url': 'https://music.youtube.com/'}]}
            if method == 'Target.attachToTarget':
                return {'sessionId': params['targetId']}
            if method == 'Runtime.evaluate':
                return {'result': {'value': kwargs['session_id'] == 'music'}}
            return {}
        connection.send = AsyncMock(side_effect=send)
        manager = MagicMock()
        manager.connected.return_value.__aenter__ = AsyncMock(return_value=connection)
        manager.connected.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch.object(youtube, 'BrowserManager', return_value=manager):
            result = await youtube.youtube_control(youtube.YouTubeInput(action='seek', seconds=120),
                                                   SimpleNamespace(user_id='u'))
        self.assertEqual(result['status'], 'wrong_media_tool')
        self.assertEqual(result['tool'], 'ytmusic_control')
        manager.tab.assert_not_called()
