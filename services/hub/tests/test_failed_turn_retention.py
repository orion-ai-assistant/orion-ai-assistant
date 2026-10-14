import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from orion.api.services import job_service
from orion.contracts.http import JobCreateRequest, JobInput
from orion.contracts.settings import RuntimeSettings
from orion.kernel import job_safety
from orion.worker.infra.context import JobContext
from orion.worker.services import job_processor as processor


class FailedTurnRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = MagicMock()
        self.redis.hset = AsyncMock()
        self.redis.hgetall = AsyncMock(return_value={'partial_text': 'partial'})
        self.redis.publish = AsyncMock()
        self.redis.pipeline.return_value.execute = AsyncMock()
        self.insert = patch('orion.kernel.registry.insert_messages', AsyncMock())
        self.persist = self.insert.start()
        self.addCleanup(self.insert.stop)

    async def test_worker_persists_failure_before_publishing_and_model_loader_excludes_it(self):
        context = JobContext(SimpleNamespace(chat_id='chat', user_id='alice', channel='events',
            turn_id='turn', created_at='now', enabled_tools='[]'),
            JobCreateRequest(user_id='alice', input=JobInput(text='question', audio=False)), self.redis)

        async def stream(*args):
            yield 'content', 'partial'
            raise RuntimeError('Router offline')

        async def publish(channel, payload):
            if json.loads(payload)['type'] == 'error':
                self.persist.assert_awaited_once()

        self.redis.publish.side_effect = publish
        conversation = SimpleNamespace(stream=stream, activity=[], transcript=[], rounds=[])
        with ExitStack() as stack:
            stack.enter_context(patch.object(processor.JobContext, 'from_stream_fields', return_value=context))
            stack.enter_context(patch.object(processor, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())))
            for name, value in [('is_active_turn', True), ('should_stop', False),
                                ('load_history', []), ('hset_if_active', True), ('finalize_state_if_active', True)]:
                stack.enter_context(patch.object(processor, name, AsyncMock(return_value=value)))
            stack.enter_context(patch.object(processor, 'ToolConversation', return_value=conversation))
            await processor.process_message(self.redis, '1-0', {}, 'test')
        saved = self.persist.call_args.kwargs['messages']
        self.assertEqual(saved[0]['content'], 'question')
        self.assertTrue(all(m['failed_turn'] for m in saved))
        self.assertIn('Router offline', saved[1]['generation_error'])
        good = [{'role': 'user', 'content': 'next'}, {'role': 'assistant', 'content': 'answer'}]
        self.redis.lrange = AsyncMock(return_value=[json.dumps(m) for m in saved + good])

        async def hydrate(chat, messages):
            return messages

        with patch.object(processor, 'hydrate_model_media', hydrate):
            model = await processor.load_history(self.redis, 'chat', 20, 3600)
        self.assertEqual(model, good)

    async def test_watchdog_persists_failed_question_and_error(self):
        question = {'role': 'user', 'content': 'waiting', 'turn_id': 'turn'}
        self.redis.hgetall.return_value = {'current_user_message': json.dumps(question)}
        self.redis.eval = AsyncMock(return_value=2)
        await job_safety.fail_job(self.redis, {'chat_id': 'chat', 'turn_id': 'turn', 'channel': 'events'})
        saved = self.persist.call_args.kwargs['messages']
        self.assertEqual(saved[0]['content'], 'waiting')
        self.assertTrue(saved[0]['failed_turn'])
        self.assertEqual(saved[1]['generation_error'], job_safety.INTERRUPTED)

    async def test_history_api_returns_failure_and_question_after_reopening(self):
        raw = [{'role': 'user', 'content': 'question', 'failed_turn': True},
               {'role': 'assistant', 'content': 'offline', 'generation_error': 'offline'}]
        with patch.object(job_service, 'ensure_chat_access', AsyncMock()), patch.object(
                job_service, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), patch.object(
                job_service, '_load_history_with_hydration', AsyncMock(return_value=raw)):
            history = await job_service.get_chat_history(self.redis, 'alice', 'chat')
        self.assertEqual(len(history), 2)
        self.assertTrue(all(m['failed_turn'] for m in history))


if __name__ == '__main__':
    unittest.main()
