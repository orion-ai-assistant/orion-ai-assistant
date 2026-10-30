import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from orion.api import routes
from orion.api.services import assistant_chat, job_service
from orion.kernel import data_paths
from orion.contracts.access import ChatAccessResult
from orion.contracts.http import JobCreateRequest, JobCreateResponse
from orion.contracts.settings import RuntimeSettings
from orion.worker.services import job_processor as processor


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.published = []
        self.on_lock = {}  # Lock name -> what happens while waiting for it.

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
            if name in self.on_lock:
                self.on_lock.pop(name)()
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

    async def test_unreadable_database_does_not_open_a_second_chat(self):
        with patch.object(assistant_chat, 'get_assistant_chat_db', AsyncMock(side_effect=RuntimeError('down'))),                 self.assertLogs(level='ERROR'):
            with self.assertRaises(HTTPException) as error:
                await assistant_chat.create_assistant_message(self.redis, self.request('old'))
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.created, [])
        self.assertEqual(self.announced(), [])

    async def test_a_reset_while_waiting_for_the_chat_opens_a_new_one(self):
        self.db_chat = 'shared'

        def reset():
            self.chats.discard('shared')
            self.db_chat = None
            self.redis.values.pop('assistant:chat:alice', None)
        self.redis.on_lock['chat:lifecycle:shared'] = reset
        created = await assistant_chat.create_assistant_message(self.redis, self.request('shared'))
        self.assertEqual(created.chat_id, 'new')
        self.assertEqual([p.chat_id for p in self.created], [None])
        self.assertEqual(self.announced(), ['new'])


class ChatResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleting_a_chat_waits_for_a_send_into_it(self):
        redis = FakeRedis()
        order = []
        redis.on_lock['chat:lifecycle:chat'] = lambda: order.append('locked')

        async def delete(*_args):
            order.append('deleted')
            return {'status': 'deleted'}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
        with patch.object(routes, 'delete_chat', delete):
            await routes.delete_chat_endpoint('chat', request, current_user='alice')
        self.assertEqual(order, ['locked', 'deleted'])


class SupersededHistoryTests(unittest.IsolatedAsyncioTestCase):
    def redis(self, state, claimed=True):
        redis = MagicMock()
        redis.hgetall = AsyncMock(return_value=state)
        redis.hsetnx = AsyncMock(return_value=claimed)
        redis.lrange = AsyncMock(return_value=['{"role": "user", "content": "earlier"}'])
        redis.pipeline.return_value.execute = AsyncMock()
        return redis

    def pushed(self, redis):
        return [json.loads(call.args[1]) for call in redis.pipeline.return_value.rpush.call_args_list]

    async def test_a_replaced_turn_keeps_its_question_and_partial_answer(self):
        redis = self.redis({
            'status': 'processing', 'active_turn_id': 'old', 'partial_text': 'Yarım',
            'current_user_message': json.dumps({'role': 'user', 'content': 'ilk soru', 'turn_id': 'old'}),
            'partial_tools': '[]', 'partial_display': '[]',
        })
        with patch('orion.kernel.registry.insert_messages', AsyncMock()) as insert:
            await job_service._keep_superseded_turn(redis, 'chat', 3600)
        expected = [{'role': 'user', 'content': 'ilk soru', 'turn_id': 'old'},
                    {'role': 'assistant', 'content': 'Yarım', 'turn_id': 'old'}]
        self.assertEqual(self.pushed(redis), expected)
        self.assertEqual(insert.call_args.kwargs['messages'], expected)
        redis.hsetnx.assert_awaited_once_with('orion:job:old', 'history_saved', '1')

    async def test_a_turn_already_saved_or_finished_is_not_saved_again(self):
        running = {'status': 'queued', 'active_turn_id': 'old',
                   'current_user_message': json.dumps({'role': 'user', 'content': 'q'})}
        for redis in (self.redis(running, claimed=False), self.redis({**running, 'status': 'completed'})):
            with patch('orion.kernel.registry.insert_messages', AsyncMock()) as insert:
                await job_service._keep_superseded_turn(redis, 'chat', 3600)
            self.assertEqual(self.pushed(redis), [])
            insert.assert_not_awaited()

    async def test_the_replaced_worker_saves_nothing_after_the_new_turn_did(self):
        redis = self.redis({}, claimed=False)
        with patch('orion.kernel.registry.insert_messages', AsyncMock()) as insert:
            await processor.append_history(redis, 'chat', [{'role': 'user', 'content': 'q', 'turn_id': 'old'},
                                                          {'role': 'assistant', 'content': 'a', 'turn_id': 'old'}],
                                           20, RuntimeSettings())
        self.assertEqual(self.pushed(redis), [])
        insert.assert_not_awaited()


class ContainerPathTests(unittest.TestCase):
    def test_a_container_layout_has_a_data_folder(self):
        with patch.object(data_paths, '__file__', '/app/src/orion/kernel/data_paths.py'):
            self.assertIsNone(data_paths.install_root())
            self.assertEqual(data_paths.hub_data(), Path('/app/data').resolve())


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
