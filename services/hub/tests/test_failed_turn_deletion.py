import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from orion.api.services import job_service
from orion.kernel.chat_history import failed_turn_key, remove_failed_turn, visible_history


class FailedTurnDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.good = [{'role': 'user', 'content': 'keep'}, {'role': 'assistant', 'content': 'answer'}]
        self.failed = [{'role': 'user', 'content': 'bad', 'turn_id': 'bad', 'attachments': [{'media_id': 'media'}]},
                       {'role': 'assistant', 'content': 'error', 'failed_turn': True, 'turn_id': 'bad'}]
        self.redis = MagicMock()
        self.redis.hgetall = AsyncMock(return_value={'status': 'failed'})
        self.redis.publish = AsyncMock()
        self.redis.pipeline.return_value.execute = AsyncMock()
        self.access = AsyncMock(return_value={'user_id': 'alice'})
        self.load = AsyncMock(return_value=self.good + self.failed)
        self.durable = AsyncMock(return_value=False)
        for guard in (
            patch.object(job_service, 'ensure_chat_access', self.access),
            patch.object(job_service, '_load_history_with_hydration', self.load),
            patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=SimpleNamespace(redis_cache_ttl_seconds=3600))),
            patch('orion.kernel.registry.delete_failed_turn_db', self.durable),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    async def test_selected_failure_deleted_from_cache_and_published_to_owner(self):
        result = await job_service.delete_failed_turn(self.redis, 'alice', 'chat', 'bad')
        self.assertEqual(result['status'], 'updated')
        self.durable.assert_awaited_once_with('chat', 'bad', delete_empty=False,
                                             removed_media={'media'}, retained_media=set())
        pipe = self.redis.pipeline.return_value
        self.assertEqual([json.loads(m) for m in pipe.rpush.call_args.args[1:]], self.good)
        event = json.loads(self.redis.publish.call_args.args[1])
        self.assertEqual(event['type'], 'chat_history_changed')
        self.assertEqual(event['chat_id'], 'chat')

    async def test_empty_chat_is_deleted_and_broadcast(self):
        self.load.return_value = self.failed
        self.durable.return_value = True
        result = await job_service.delete_failed_turn(self.redis, 'alice', 'chat', 'bad')
        self.assertEqual(result['status'], 'deleted')
        self.redis.pipeline.return_value.zrem.assert_called_once()
        self.redis.pipeline.return_value.rpush.assert_not_called()
        self.assertEqual(json.loads(self.redis.publish.call_args.args[1])['type'], 'chat_delete')

    async def test_running_successful_missing_and_forbidden_requests_cannot_mutate(self):
        self.redis.hgetall.return_value = {'status': 'processing'}
        with self.assertRaises(HTTPException):
            await job_service.delete_failed_turn(self.redis, 'alice', 'chat', 'bad')
        self.redis.hgetall.return_value = {}
        for key in ('missing', failed_turn_key(self.good)):
            with self.assertRaises(HTTPException):
                await job_service.delete_failed_turn(self.redis, 'alice', 'chat', key)
        self.access.side_effect = HTTPException(403)
        with self.assertRaises(HTTPException):
            await job_service.delete_failed_turn(self.redis, 'bob', 'chat', 'bad')
        self.durable.assert_not_awaited()
        self.redis.pipeline.assert_not_called()
        self.redis.publish.assert_not_awaited()

    async def test_database_failure_leaves_cache_and_other_devices_unchanged(self):
        self.durable.side_effect = RuntimeError('offline')
        with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException) as error:
            await job_service.delete_failed_turn(self.redis, 'alice', 'chat', 'bad')
        self.assertEqual(error.exception.status_code, 503)
        self.redis.pipeline.assert_not_called()
        self.redis.publish.assert_not_awaited()

    def test_legacy_key_survives_preceding_deletions_and_removes_tools(self):
        first = [{'role': 'user', 'content': 'old'}, {'role': 'assistant', 'generation_error': 'old'}]
        second = [{'role': 'user', 'content': 'new'}, {'role': 'assistant', 'tool_calls': [{}]},
                  {'role': 'tool', 'content': 'tool output'}, {'role': 'assistant', 'failed_turn': True}]
        key = visible_history(first + second)[-1]['failed_turn_key']
        remaining, removed = remove_failed_turn(second, key)
        self.assertEqual(remaining, [])
        self.assertEqual(removed, second)
