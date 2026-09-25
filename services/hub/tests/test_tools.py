import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from orion.api.services import tool_service, job_service
from orion.contracts.http import JobCreateRequest
from orion.contracts.settings import RuntimeSettings
from orion.contracts.tools import ToolSelection, default_tool_selection
from orion.kernel import config, registry as db
from orion.worker.services import router
from orion.worker.services.tool_history import model_history
from orion.worker.services.tool_display import DisplayTimeline
from orion.worker.services.tool_runner import ToolConversation, ToolCancelled, execute_tool
from orion.worker.services.tool_stream import ToolCallAccumulator
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import ToolRegistry, get_registry
from orion.worker.tools.datetime_tools import CATEGORY, TimeInput


def call(identifier="call_1", name="get_current_time", arguments='{"timezone":"UTC"}'):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}}


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_category_functions_and_default_off(self):
        registry = get_registry()
        self.assertIn('text', registry.categories)
        self.assertEqual([tool.id for tool in registry.categories['text'].tools],
                         ['analyze_text', 'find_in_text', 'replace_in_text'])
        self.assertEqual(registry.enabled(default_tool_selection()), ['get_current_time'])
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        analyze = await execute_tool(registry.tools['analyze_text'], json.dumps({'text': 'Bir iki\nüç'}), context)
        self.assertEqual(analyze, {'characters': 10, 'words': 3, 'lines': 2})
        found = await execute_tool(registry.tools['find_in_text'],
                                   '{"text":"İstanbul ve istanbul", "query":"istanbul"}', context)
        self.assertEqual(found['positions'], [0, 12])
        replaced = await execute_tool(registry.tools['replace_in_text'],
                                      '{"text":"a a a", "search":"a", "replacement":"b", "max_replacements":2}', context)
        self.assertEqual(replaced, {'text': 'b b a', 'replacements': 2})
        with self.assertRaises(ValidationError):
            await execute_tool(registry.tools['replace_in_text'],
                               '{"text":"hello", "search":"", "replacement":"x"}', context)

    def test_discovery_selection_and_duplicate_ids(self):
        registry = get_registry()
        selection = default_tool_selection()
        self.assertEqual(registry.enabled(selection), ['get_current_time'])
        selection.categories['datetime'] = False
        self.assertEqual(registry.enabled(selection), [])
        self.assertTrue(selection.functions['get_current_time'])
        selection.categories['datetime'] = True
        self.assertEqual(registry.enabled(selection), ['get_current_time'])
        with self.assertRaises(ValueError):
            ToolRegistry([CATEGORY, CATEGORY])
        with self.assertRaises(ValueError):
            ToolRegistry([CATEGORY, replace(CATEGORY, id='another')])
        registry = ToolRegistry([CATEGORY, replace(CATEGORY, id='another', tools=(replace(CATEGORY.tools[0], id='new_tool'),))])
        self.assertEqual(registry.enabled(selection), ['get_current_time'])
        self.assertNotIn('handler', registry.catalog()[0]['functions'][0])

    async def test_time_and_input_validation(self):
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        result = await execute_tool(CATEGORY.tools[0], '{}', context)
        self.assertEqual(result['timezone'], 'Europe/Istanbul')
        self.assertEqual(result['utc_offset'], '+0300')
        result = await execute_tool(CATEGORY.tools[0], '{"timezone":"UTC"}', context)
        self.assertEqual(result['utc_offset'], '+0000')
        with self.assertRaises(Exception):
            await execute_tool(CATEGORY.tools[0], '{"timezone":"Invalid/Zone"}', context)
        with self.assertRaises(ValidationError):
            TimeInput.model_validate({'timezone': 123})
        with self.assertRaises(ValidationError):
            TimeInput.model_validate({'extra': True})

    async def test_timeout_result_limit_and_cancellation(self):
        ended = asyncio.Event()
        async def waiting(args, context):
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        with self.assertRaises(TimeoutError):
            await execute_tool(replace(CATEGORY.tools[0], handler=waiting, timeout_seconds=.01), '{}', context)
        self.assertTrue(ended.is_set())
        context = replace(context, cancelled=AsyncMock(side_effect=[False, True]))
        with self.assertRaises(ToolCancelled):
            await execute_tool(replace(CATEGORY.tools[0], handler=waiting), '{}', context)
        async def large(args, context):
            return 'x' * 32768
        with self.assertRaises(ValueError):
            await execute_tool(replace(CATEGORY.tools[0], handler=large), '{}', replace(context, cancelled=AsyncMock(return_value=False)))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def test_fragmented_and_aggregate_calls(self):
        accumulator = ToolCallAccumulator()
        accumulator.feed({'delta': {'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'get_current_time', 'arguments': '{"time'}}]}})
        accumulator.feed({'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'zone":"UTC"}'}},
                                                   {'index': 1, **call('call_2')}]}, 'finish_reason': 'tool_calls'})
        accumulator.done = True
        self.assertEqual(accumulator.complete(), [call(), call('call_2')])
        accumulator.feed({'message': {'tool_calls': [call(), call('call_2')]}, 'finish_reason': 'tool_calls'})
        self.assertEqual(len(accumulator.complete()), 2)

    def test_incomplete_and_duplicate_calls_fail(self):
        accumulator = ToolCallAccumulator()
        accumulator.feed({'delta': {'tool_calls': [{'index': 0, **call()}]}})
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            accumulator.complete()
        accumulator.feed({'message': {'tool_calls': [call(), call()]}, 'finish_reason': 'tool_calls'})
        accumulator.done = True
        with self.assertRaisesRegex(RuntimeError, 'Malformed'):
            accumulator.complete()

    def test_router_stop_finish_requires_done_marker(self):
        accumulator = ToolCallAccumulator()
        accumulator.feed({'message': {'tool_calls': [call()]}, 'finish_reason': 'stop'})
        with self.assertRaises(RuntimeError):
            accumulator.complete()
        accumulator.done = True
        self.assertEqual(accumulator.complete(), [call()])
        accumulator.finish_reason = 'length'
        with self.assertRaises(RuntimeError):
            accumulator.complete()

    async def test_router_payload_and_truncated_stream(self):
        from test_stream_stop import Response
        async def chunks(complete):
            yield ('data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [{'index': 0, **call()}]}}]}) + '\n\n').encode()
            if complete:
                yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
        session = Mock()
        with patch.object(router, 'get_session', AsyncMock(return_value=session)), patch.object(router, '_router_urls', return_value=['http://test']):
            session.post.return_value = Response(chunks(True))
            events = [event async for event in router.llama_stream_chat_typed([], RuntimeSettings(), tools=[CATEGORY.tools[0].schema()])]
            self.assertEqual(session.post.call_args.kwargs['json']['tool_choice'], 'auto')
            self.assertEqual(json.loads(events[-1][1]), [call()])
            session.post.return_value = Response(chunks(False))
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                _ = [event async for event in router.llama_stream_chat_typed([], RuntimeSettings())]


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def test_thinking_segments_and_snapshot_keep_order(self):
        timeline = DisplayTimeline()
        for kind, value in [('thinking', 'first thought'), ('content', 'first answer'),
                            ('tool_call', 'call_1'), ('thinking', 'second thought'),
                            ('content', 'second answer'), ('snapshot', 'first answersecond final')]:
            timeline.add(kind, value)
        self.assertEqual(timeline.parts, [
            {'type': 'thinking', 'content': 'first thought'},
            {'type': 'content', 'content': 'first answer'},
            {'type': 'tool', 'call_id': 'call_1'},
            {'type': 'thinking', 'content': 'second thought'},
            {'type': 'content', 'content': 'second final'},
        ])

    def conversation(self, enabled=None):
        return ToolConversation(SimpleNamespace(user_id='u', chat_id='c', turn_id='t'),
                                ['get_current_time'] if enabled is None else enabled)

    async def test_multiple_calls_followup_and_snapshots(self):
        conversation = self.conversation()
        requests = []
        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) == 1:
                yield 'content', 'Checking. '
                yield 'tool_calls', json.dumps([call(), call('call_2')])
            else:
                yield 'content', 'Done'
                yield 'content_snapshot', 'Done!'
        events = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), stream)]
        self.assertEqual([message['role'] for message in requests[1]], ['assistant', 'tool', 'tool'])
        self.assertEqual(requests[1][1]['tool_call_id'], 'call_1')
        self.assertEqual(events[-1], ('content_snapshot', 'Checking. Done!'))
        self.assertEqual(conversation.final_content, 'Done!')
        self.assertEqual([item['status'] for item in conversation.activity], ['completed', 'completed'])

    async def test_bad_arguments_and_disabled_unknown_tools_are_results(self):
        for name, args, enabled in [('get_current_time', '{', ['get_current_time']),
                                    ('get_current_time', '{}', []), ('missing', '{}', [])]:
            conversation = self.conversation(enabled)
            requests = []
            async def stream(messages, settings, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    yield 'tool_calls', json.dumps([call(name=name, arguments=args)])
                else:
                    yield 'content', 'error reported'
            events = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), stream)]
            self.assertEqual(conversation.activity[0]['status'], 'error')
            self.assertEqual(events[-1], ('content', 'error reported'))
            if not enabled:
                self.assertNotIn('tools', requests[0])

    async def test_round_and_call_limits(self):
        for batch in (1, 3):
            conversation = self.conversation()
            count = 0
            async def stream(messages, settings, **kwargs):
                nonlocal count
                count += 1
                yield 'tool_calls', json.dumps([call(f'{count}_{n}') for n in range(batch)])
            with self.assertRaisesRegex(RuntimeError, 'limit exceeded'):
                _ = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), stream)]
            self.assertLessEqual(len(conversation.activity), 16)

    async def test_stop_before_followup(self):
        conversation = self.conversation()
        stop = False
        requests = 0
        async def checker():
            return stop
        async def stream(messages, settings, **kwargs):
            nonlocal requests
            requests += 1
            yield 'tool_calls', json.dumps([call(), call('call_2')])
        events = []
        async for event in conversation.stream([], RuntimeSettings(), checker, stream):
            events.append(event)
            if event[0] == 'tool_call':
                stop = True
        self.assertEqual(requests, 1)
        self.assertTrue(all(item['status'] == 'cancelled' for item in conversation.activity))
        self.assertEqual(len(conversation.transcript), 3)

    def test_history_preserves_whole_tool_groups_and_model_content(self):
        history = [{'role': 'user', 'content': 'old'}, {'role': 'assistant', 'content': 'old answer'},
                   {'role': 'user', 'content': 'now'}, {'role': 'assistant', 'content': None, 'tool_calls': [call()]},
                   {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{}'},
                   {'role': 'assistant', 'content': 'all text', 'model_content': 'final text', 'audio': {}}]
        result = model_history(history, 2)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[-1], {'role': 'assistant', 'content': 'final text'})
        self.assertEqual(result[1]['tool_calls'], [call()])
        self.assertEqual(model_history(history[:-2], 20), history[:2])


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inherit_override_reset_and_cache_loss(self):
        cache = {}
        durable = None
        settings = RuntimeSettings()
        redis = Mock()
        redis.get = AsyncMock(side_effect=lambda key: cache.get(key))
        async def put(key, value, **kwargs): cache[key] = value
        async def delete(key): cache.pop(key, None)
        redis.set = AsyncMock(side_effect=put)
        redis.delete = AsyncMock(side_effect=delete)
        async def db_get(*args): return durable
        async def db_set(chat, user, value):
            nonlocal durable
            durable = value
        with patch.object(db, 'get_chat_tool_selection', db_get), patch.object(db, 'set_chat_tool_selection', db_set), patch.object(tool_service, 'get_runtime_settings', AsyncMock(side_effect=lambda *args: settings)):
            self.assertEqual((await tool_service.read_selection(redis, 'u', 'c'))['enabled_tools'], ['get_current_time'])
            settings = RuntimeSettings(tool_selection=ToolSelection())
            self.assertEqual((await tool_service.read_selection(redis, 'u', 'c'))['enabled_tools'], [])
            result = await tool_service.save_selection(redis, 'u', 'c', default_tool_selection())
            self.assertFalse(result['inherited'])
            cache.clear()
            self.assertEqual((await tool_service.read_selection(redis, 'u', 'c'))['enabled_tools'], ['get_current_time'])
            result = await tool_service.save_selection(redis, 'u', 'c', None)
            self.assertTrue(result['inherited'])
            self.assertEqual(result['enabled_tools'], [])

    async def test_failed_save_and_unknown_selection(self):
        redis = AsyncMock()
        with patch.object(db, 'set_chat_tool_selection', AsyncMock(side_effect=RuntimeError('unavailable'))):
            with self.assertRaises(HTTPException) as error:
                await tool_service.save_selection(redis, 'u', 'c', ToolSelection())
            self.assertEqual(error.exception.status_code, 503)
            redis.delete.assert_not_awaited()
        with self.assertRaises(HTTPException):
            tool_service.validate_selection(ToolSelection(functions={'missing': True}))

    def test_settings_json_roundtrip_and_invalid_values(self):
        value = config.serialize_setting(default_tool_selection().model_dump())
        self.assertEqual(config.build_runtime_settings({'tool_selection': value}).tool_selection, default_tool_selection())
        with self.assertRaises(ValidationError):
            RuntimeSettings(tool_selection='{"categories":{"datetime":"false"}}')

    async def test_queue_snapshot_and_existing_chat_selection_rejection(self):
        redis = Mock()
        redis.pipeline.return_value.execute = AsyncMock()
        redis.exists = AsyncMock(return_value=False)
        with patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), patch.object(job_service, 'upsert_chat', AsyncMock()):
            await job_service.create_job(redis, JobCreateRequest(user_id='u', input={'text': 'hi'}))
        fields = redis.pipeline.return_value.xadd.call_args.kwargs['fields']
        self.assertEqual(json.loads(fields['enabled_tools']), ['get_current_time'])
        with self.assertRaises(HTTPException):
            await job_service.create_job(redis, JobCreateRequest(user_id='u', chat_id='c', input={'text': 'hi'}, tool_selection=ToolSelection()))


if __name__ == '__main__':
    unittest.main()
