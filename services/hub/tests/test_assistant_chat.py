import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.api.services import assistant_chat, job_service
from orion.contracts.access import ChatAccessResult
from orion.contracts.http import JobCreateRequest, JobCreateResponse
from orion.contracts.settings import RuntimeSettings
from orion.worker.services import job_processor as processor


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.published = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)

    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))

    def lock(self, name, **_):
        @asynccontextmanager
        async def held():
            yield
        return held()


class AssistantChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.chats = {'old', 'shared'}  # Chats that still exist.
        self.db_chat = None
        self.created = []

        async def access(_redis, user_id, chat_id):
            return ChatAccessResult(state='ok' if chat_id in self.chats else 'not_found', meta={})

        async def create(_redis, payload):
            self.created.append(payload.model_copy())
            chat_id = payload.chat_id or 'new'
            self.chats.add(chat_id)
            return JobCreateResponse(chat_id=chat_id, status='queued', created_at='now', turn_id='t')

        async def stored(_user_id):
            return self.db_chat

        async def persist(chat_id, _user_id):
            self.db_chat = chat_id

        for target, name, value in [
            (job_service, 'check_chat_access', access),
            (job_service, 'create_job', create),
            (assistant_chat, 'get_assistant_chat_db', stored),
            (assistant_chat, 'set_assistant_chat_db', persist),
            (assistant_chat, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())),
        ]:
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, chat_id=None, **extra):
        return JobCreateRequest(user_id='alice', chat_id=chat_id, assistant=True, input={'text': 'merhaba'}, **extra)

    def announced(self):
        return [event['chat_id'] for _, event in self.redis.published if event['type'] == 'assistant_chat']

    async def test_first_message_opens_the_shared_chat_and_announces_it(self):
        created = await assistant_chat.create_assistant_message(self.redis, self.request())
        self.assertEqual(created.chat_id, 'new')
        self.assertEqual(self.db_chat, 'new')
        self.assertEqual(await assistant_chat.current_assistant_chat(self.redis, 'alice'), 'new')
        self.assertEqual(self.announced(), ['new'])
        self.assertEqual(self.redis.published[0][0], 'room:user:alice')

    async def test_every_device_posts_into_the_shared_chat(self):
        self.db_chat = 'shared'
        created = await assistant_chat.create_assistant_message(self.redis, self.request('old'))
        self.assertEqual(created.chat_id, 'shared')
        self.assertEqual(self.created[0].chat_id, 'shared')
        self.assertEqual(self.announced(), [])

    async def test_a_device_chat_becomes_shared_when_there_is_none(self):
        created = await assistant_chat.create_assistant_message(self.redis, self.request('old'))
        self.assertEqual(created.chat_id, 'old')
        self.assertEqual(self.db_chat, 'old')
        self.assertEqual(self.announced(), ['old'])

    async def test_a_chat_reset_elsewhere_is_replaced(self):
        self.chats.discard('old')
        created = await assistant_chat.create_assistant_message(self.redis, self.request('old'))
        self.assertEqual(created.chat_id, 'new')
        self.assertIsNone(self.created[0].chat_id)

    async def test_deleted_shared_chat_is_forgotten(self):
        self.db_chat = 'shared'
        self.assertEqual(await assistant_chat.current_assistant_chat(self.redis, 'alice'), 'shared')
        await assistant_chat.forget_assistant_chat(self.redis, 'alice', 'other')
        self.assertEqual(self.redis.values['assistant:chat:alice'], 'shared')
        await assistant_chat.forget_assistant_chat(self.redis, 'alice', 'shared')
        self.chats.discard('shared')
        self.db_chat = None
        self.assertIsNone(await assistant_chat.current_assistant_chat(self.redis, 'alice'))

    async def test_claim_keeps_an_existing_shared_chat(self):
        with patch.object(job_service, 'ensure_chat_access', AsyncMock()):
            self.assertEqual(await assistant_chat.claim_assistant_chat(self.redis, 'alice', 'old'), 'old')
            self.assertEqual(await assistant_chat.claim_assistant_chat(self.redis, 'alice', 'shared'), 'old')
        self.assertEqual(self.announced(), ['old'])

    async def test_unreadable_database_falls_back_to_a_new_chat(self):
        with patch.object(assistant_chat, 'get_assistant_chat_db', AsyncMock(side_effect=RuntimeError('down'))), \
                self.assertLogs(level='ERROR'):
            self.assertIsNone(await assistant_chat.current_assistant_chat(self.redis, 'alice'))


class SupersededTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_superseded_queued_turn_is_closed_on_every_device(self):
        context = SimpleNamespace(chat_id='chat', turn_id='old', user_id='alice', channel='room:user:alice',
                                  prompt='hello', emit_done=AsyncMock())
        with patch.object(processor.JobContext, 'from_stream_fields', return_value=context), \
                patch.object(processor, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), \
                patch.object(processor, 'is_active_turn', AsyncMock(return_value=False)):
            await processor.process_message(AsyncMock(), 'stream', {}, 'worker')
        context.emit_done.assert_awaited_once_with('stopped')


if __name__ == '__main__':
    unittest.main()
