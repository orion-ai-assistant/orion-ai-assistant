import base64
import io
import json
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException
from orion.api.services import job_service
from orion.kernel.audio_metadata import duration_ms

class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def audio(self):
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\x00\x00' * 36000)
        return {'audio': base64.b64encode(output.getvalue()).decode(), 'format': 'wav'}

    async def test_lightweight_history_preserves_duration_and_full_recording(self):
        original = {'role': 'assistant', 'turn_id': 'turn', 'content': 'hello', 'audio': self.audio()}
        redis = Mock(hgetall=AsyncMock(return_value={}))
        with patch.object(job_service, 'ensure_chat_access', AsyncMock()), \
             patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=SimpleNamespace(redis_cache_ttl_seconds=3600))), \
             patch.object(job_service, '_load_history_with_hydration', AsyncMock(return_value=[original])):
            light = await job_service.get_chat_history(redis, 'user', 'chat', include_audio=False)
            self.assertNotIn('audio', light[0]['audio'])
            self.assertEqual(light[0]['audio']['duration_ms'], 1500)
            self.assertEqual(light[0]['audio']['audio_path'], '/api/v1/chats/chat/turns/turn/audio')
            self.assertLess(len(json.dumps(light)), len(json.dumps([original])) / 30)
            self.assertIn('audio', original['audio'], 'Projection cannot mutate the shared full recording')
            full = await job_service.get_turn_audio(redis, 'user', 'chat', 'turn')
            self.assertEqual(full, original['audio'])

    async def test_audio_access_is_checked_before_loading_any_recording(self):
        redis = Mock()
        with patch.object(job_service, 'ensure_chat_access', AsyncMock(side_effect=HTTPException(403))), \
             patch.object(job_service, '_load_history_with_hydration', AsyncMock()) as read, \
             patch.object(job_service, 'get_chat_history_db', AsyncMock()) as db:
            with self.assertRaises(HTTPException):
                await job_service.get_turn_audio(redis, 'other-user', 'chat', 'turn')
            read.assert_not_called()
            db.assert_not_called()

    async def test_trimmed_recording_can_be_retrieved_from_database(self):
        message = {'role': 'assistant', 'turn_id': 'turn', 'audio': self.audio()}
        with patch.object(job_service, 'get_chat_history', AsyncMock(return_value=[])), \
             patch.object(job_service, 'get_chat_history_db', AsyncMock(return_value=[message])):
            self.assertEqual(await job_service.get_turn_audio(Mock(), 'user', 'chat', 'turn'), message['audio'])

    async def test_missing_recording_does_not_return_a_different_turn(self):
        with patch.object(job_service, 'get_chat_history', AsyncMock(return_value=[])), \
             patch.object(job_service, 'get_chat_history_db', AsyncMock(return_value=[])):
            with self.assertRaises(HTTPException) as error:
                await job_service.get_turn_audio(Mock(), 'user', 'chat', 'missing')
            self.assertEqual(error.exception.status_code, 404)

    def test_duration_parser_ignores_invalid_and_handles_explicit_duration(self):
        self.assertEqual(duration_ms(self.audio()), 1500)
        self.assertIsNone(duration_ms({'audio': 'invalid', 'format': 'wav'}))
        self.assertEqual(duration_ms({'duration_ms': 1500}), 1500)
