import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from orion.api.services import job_service
from orion.contracts.access import ChatAccessResult
from orion.kernel.chat_history import successful_history, visible_history, persist_failed_turn, failed_content


class HistoryTests(unittest.TestCase):
    def test_error_fences_close_cleanly_for_new_and_saved_failures(self):
        error = "⚠️ **Yönlendirici (Router) Hatası:**\n\n```text\nOpenRouter model 'gpt-oss-120b' does not support audio, image, video input. Supported input modalities: ['text'].\n```"
        formatted = failed_content(error, 'partial answer')
        self.assertEqual(formatted, 'partial answer\n\nHata: ' + error + '\n')
        old = [{'role': 'user', 'content': 'question'},
               {'role': 'assistant', 'content': f'partial answer\n\n[Hata: {error}]', 'generation_error': error}]
        visible = visible_history(old)
        self.assertEqual(visible[-1]['content'], formatted)
        self.assertTrue(old[-1]['content'].endswith('```]'), 'Stored history is not mutated on read')
        self.assertEqual(successful_history(visible), [])

    def test_visible_failed_question_and_error_do_not_enter_model_history(self):
        raw = [{'role': 'user', 'content': 'bad'},
               {'role': 'assistant', 'content': 'error', 'generation_error': 'offline'},
               {'role': 'user', 'content': 'next'}, {'role': 'assistant', 'content': 'ok'}]
        visible = visible_history(raw)
        self.assertEqual(len(visible), 4)
        self.assertTrue(all(m['failed_turn'] for m in visible[:2]))
        self.assertEqual(successful_history(visible), raw[2:])
    def test_failed_user_tools_and_assistant_are_removed_together(self):
        good = [{'role': 'user', 'content': 'ok'}, {'role': 'assistant', 'content': 'answer'}]
        failed = [{'role': 'user', 'content': 'bad', 'failed_turn': True},
                  {'role': 'assistant', 'tool_calls': [{}]}, {'role': 'tool', 'content': 'result'},
                  {'role': 'assistant', 'content': 'partial'}]
        self.assertEqual(successful_history(good + failed + good), good + good)

    def test_watchdog_failed_assistant_removes_its_user(self):
        self.assertEqual(successful_history([
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'timeout', 'failed_turn': True}]), [])


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = MagicMock()
        self.redis.hgetall = AsyncMock(return_value={'status': 'failed'})
        self.redis.lrange = AsyncMock(return_value=[])
        self.redis.delete = AsyncMock()
        self.redis.publish = AsyncMock()
        self.redis.pipeline.return_value.execute = AsyncMock()
        self.access = patch.object(job_service, 'check_chat_access', AsyncMock(
            return_value=ChatAccessResult(state='ok', meta={'user_id': 'alice'})))
        self.access.start()
        self.addCleanup(self.access.stop)

    async def test_empty_failed_chat_is_kept_without_mutation_or_broadcast(self):
        with patch('orion.kernel.registry.prune_failed_chat_db', AsyncMock(return_value=True)) as prune:
            result = await job_service.discard_failed_chat(self.redis, 'alice', 'chat')
        self.assertEqual(result['status'], 'kept')
        prune.assert_not_awaited()
        self.redis.delete.assert_not_awaited()
        self.redis.publish.assert_not_awaited()

    async def test_failure_is_saved_to_cache_and_db_with_attachments_and_exclusion(self):
        question = {'role': 'user', 'content': 'prompt', 'display_text': 'visible',
                    'attachments': [{'name': 'test.txt', 'data': 'hello', 'isText': True}]}
        with patch('orion.kernel.registry.insert_messages', AsyncMock()) as insert:
            await persist_failed_turn(self.redis, 'chat', 'turn', question, 'offline',
                                      {'partial_text': 'partial'}, {'total_ms': 100})
        messages = insert.call_args.kwargs['messages']
        self.assertEqual(messages[0]['attachments'], question['attachments'])
        self.assertEqual(messages[0]['display_text'], 'visible')
        self.assertTrue(all(m['failed_turn'] for m in messages))
        self.assertIn('offline', messages[1]['content'])
        self.assertIn('partial', messages[1]['content'])
        self.assertEqual(messages[1]['metrics'], {'total_ms': 100})
        self.assertEqual(successful_history(messages), [])
        self.assertEqual(self.redis.pipeline.return_value.rpush.call_count, 2)

    async def test_running_and_completed_chats_are_kept(self):
        for status in ('queued', 'processing', 'completed', 'stopped'):
            self.redis.hgetall.return_value = {'status': status}
            with patch('orion.kernel.registry.prune_failed_chat_db', AsyncMock()) as prune:
                result = await job_service.discard_failed_chat(self.redis, 'alice', 'chat')
            self.assertEqual(result['status'], 'kept')
            prune.assert_not_awaited()

    async def test_successful_cached_history_is_protected_even_without_db_write(self):
        self.redis.lrange.return_value = [json.dumps({'role': 'assistant', 'content': 'answer'})]
        with patch('orion.kernel.registry.prune_failed_chat_db', AsyncMock()) as prune:
            result = await job_service.discard_failed_chat(self.redis, 'alice', 'chat')
        self.assertEqual(result['status'], 'kept')
        prune.assert_not_awaited()

    async def test_successful_durable_history_prevents_empty_chat_deletion(self):
        with patch('orion.kernel.registry.prune_failed_chat_db', AsyncMock(return_value=False)):
            result = await job_service.discard_failed_chat(self.redis, 'alice', 'chat')
        self.assertEqual(result['status'], 'kept')
        self.redis.publish.assert_not_awaited()
