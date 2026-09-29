import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from orion.contracts.tools import ToolSelection
from orion.worker.tools._registry import get_registry
from orion.worker.tools.media import tools as media


class UnifiedMediaTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, playing=()):
        targets = [
            {'type': 'page', 'targetId': 'video', 'title': 'Video',
             'url': 'https://www.youtube.com/watch?v=abcdefghijk'},
            {'type': 'page', 'targetId': 'music', 'title': 'Music',
             'url': 'https://music.youtube.com/watch?v=abcdefghijk'}]
        async def send(method, params=None, *, session_id=None, timeout=2):
            if method == 'Target.getTargets':
                return {'targetInfos': targets}
            if method == 'Target.attachToTarget':
                return {'sessionId': params['targetId']}
            if method == 'Runtime.evaluate':
                return {'result': {'value': {'title': 'Remembrance' if session_id == 'music' else 'Video',
                    'paused': session_id not in playing, 'ended': False,
                    'ready': 4, 'muted': False}}}
            return {}
        connection = MagicMock(closed=False)
        connection.send = AsyncMock(side_effect=send)
        manager = MagicMock()
        manager.connected.return_value.__aenter__ = AsyncMock(return_value=connection)
        manager.connected.return_value.__aexit__ = AsyncMock(return_value=False)
        return manager

    async def test_now_playing_checks_both_services_in_one_scan(self):
        with patch.object(media, 'BrowserManager', return_value=self.connection(('music',))):
            result = await media.media_control(media.MediaInput(action='now_playing'),
                                               SimpleNamespace(user_id='u'))
        self.assertEqual(result['playing'], [
            {'tab_id': 'music', 'service': 'ytmusic', 'title': 'Remembrance', 'muted': False}])

    async def test_auto_seek_targets_playing_music(self):
        handler = AsyncMock(return_value={'status': 'ok', 'tab_id': 'music', 'current_time': 120})
        with patch.object(media, 'BrowserManager', return_value=self.connection(('music',))), \
                patch.object(media, 'ytmusic_control', handler):
            result = await media.media_control(media.MediaInput(action='seek', seconds=120),
                                               SimpleNamespace(user_id='u'))
        self.assertEqual(result['service'], 'ytmusic')
        self.assertEqual(handler.await_args.args[0].target, 'music')
        self.assertEqual(handler.await_args.args[0].seconds, 120)

    async def test_two_playing_tabs_are_ambiguous(self):
        with patch.object(media, 'BrowserManager', return_value=self.connection(('video', 'music'))), \
                patch.object(media, 'youtube_control', new_callable=AsyncMock) as youtube, \
                patch.object(media, 'ytmusic_control', new_callable=AsyncMock) as music:
            result = await media.media_control(media.MediaInput(action='seek', seconds=120),
                                               SimpleNamespace(user_id='u'))
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(len(result['playing']), 2)
        youtube.assert_not_awaited()
        music.assert_not_awaited()

    async def test_new_song_defaults_to_music_without_tab_scan(self):
        with patch.object(media, 'BrowserManager') as manager, \
                patch.object(media, 'ytmusic_control', AsyncMock(return_value={'status': 'ok'})) as music:
            result = await media.media_control(media.MediaInput(action='play', query='Song'),
                                               SimpleNamespace(user_id='u'))
        self.assertEqual(result['service'], 'ytmusic')
        manager.assert_not_called()
        music.assert_awaited_once()

    def test_old_selection_exposes_only_unified_tool(self):
        registry = get_registry()
        old = ToolSelection(categories={'youtube': True}, functions={'youtube_control': True})
        self.assertEqual(registry.enabled(old), ['media_control'])
        self.assertIn('media', [cat['id'] for cat in registry.catalog()])
        self.assertNotIn('youtube', [cat['id'] for cat in registry.catalog()])
        self.assertNotIn('ytmusic', [cat['id'] for cat in registry.catalog()])
        disabled = ToolSelection(categories={'youtube': True, 'media': False},
                                 functions={'youtube_control': True, 'media_control': False})
        self.assertEqual(registry.enabled(disabled), [])
