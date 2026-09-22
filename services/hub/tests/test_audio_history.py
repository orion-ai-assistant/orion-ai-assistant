import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from orion.api.services import job_service
from orion.contracts.settings import RuntimeSettings
from orion.kernel import registry
from orion.worker.infra.context import JobContext
from orion.worker.services import job_processor


class AudioHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_is_stored_in_database_and_restored_after_cache_loss(self):
        redis = Mock()
        redis.publish = AsyncMock()
        redis.lrange = AsyncMock(return_value=[])
        pipe = redis.pipeline.return_value
        pipe.execute = AsyncMock()
        context = JobContext(SimpleNamespace(chat_id='chat', turn_id='turn', channel='events'), None, redis)
        audio = await context.emit_audio(b'wave bytes', format='wav', sample_rate=24000, arrival_ms=2100)
        event = json.loads(redis.publish.call_args.args[1])
        self.assertEqual(event['data'], audio)
        messages = [
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'hi', 'audio': audio},
        ]
        conn = Mock()
        conn.executemany = AsyncMock()
        conn.close = AsyncMock()
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), patch.object(registry, '_ensure_tables', AsyncMock()):
            await job_processor.append_history(redis, 'chat', messages, 20, RuntimeSettings())
            rows = conn.executemany.call_args.args[1]
            self.assertEqual(json.loads(rows[1][2])['audio'], audio)
            conn.fetch = AsyncMock(return_value=[{'content_json': row[2]} for row in rows])
            restored = await job_service._load_history_with_hydration(redis, 'chat', 3600)
        self.assertEqual(restored[1]['audio'], audio)
        self.assertEqual(restored[1]['audio']['arrival_ms'], 2100)

    async def test_audio_is_not_sent_to_llm_after_database_hydration(self):
        redis = Mock()
        redis.lrange = AsyncMock(return_value=[])
        redis.pipeline.return_value.execute = AsyncMock()
        stored = [{'role': 'assistant', 'content': 'hi', 'audio': {'audio': 'large-base64'}}]
        with patch.object(job_processor, 'get_chat_history_db', AsyncMock(return_value=stored)):
            prompt = await job_processor.load_history(redis, 'chat', 20, 3600)
        self.assertEqual(prompt, [{'role': 'assistant', 'content': 'hi'}])
