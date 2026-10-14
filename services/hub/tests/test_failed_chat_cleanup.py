import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from orion.api.services import job_service
from orion.contracts.access import ChatAccessResult
from orion.kernel.chat_history import successful_history


class HistoryTests(unittest.TestCase):
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

    async def test_empty_failed_chat_is_removed_and_broadcast(self):
        with patch('orion.kernel.registry.prune_failed_chat_db', AsyncMock(return_value=True)) as prune:
            result = await job_service.discard_failed_chat(self.redis, 'alice', 'chat')
        self.assertEqual(result['status'], 'deleted')
        prune.assert_awaited_once_with('chat')
        self.redis.publish.assert_awaited_once()

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
