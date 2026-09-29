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
from orion.localization import language_of, translate
from orion.worker.services import router
from orion.worker.services.tool_history import model_history
from orion.worker.services.tool_display import DisplayTimeline
from orion.worker.services.tool_runner import ToolConversation, ToolCancelled, execute_tool
from orion.worker.services.tool_stream import ToolCallAccumulator
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import ToolRegistry, get_registry
from orion.worker.tools.datetime import CATEGORY
from orion.worker.tools.datetime.tools import TimeInput


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

    def test_catalog_exposes_only_localized_names(self):
        registry = get_registry()
        english = {cat['id']: cat for cat in registry.catalog('en-US')}
        turkish = {cat['id']: cat for cat in registry.catalog('tr-TR')}
        self.assertEqual(english['browser']['name'], 'Browser')
        self.assertEqual(english['browser']['functions'][0]['name'], 'List open tabs')
        self.assertEqual(turkish['browser']['functions'][0]['name'], 'Açık sekmeleri listele')
        self.assertNotIn('description', english['browser'])
        self.assertNotIn('description', english['browser']['functions'][0])
        self.assertIn('List connected browser tabs', registry.tools['browser_tabs'].schema()['function']['description'])
        self.assertEqual(registry.catalog('de-DE')[0]['name'], registry.catalog('en')[0]['name'])

    def test_locale_negotiation_and_per_key_fallback(self):
        self.assertEqual(language_of('de-DE, tr-TR;q=0.9, en;q=0.8'), 'tr')
        self.assertEqual(translate('tools.browser_tabs.name', 'tr-TR'), 'Açık sekmeleri listele')
        self.assertEqual(translate('tools.browser_tabs.name', 'fr-FR'), 'List open tabs')
        self.assertEqual(translate('tools.new_tool.name', 'tr-TR', 'New tool'), 'New tool')

    def test_existing_browser_selection_enables_tabs(self):
        selection = ToolSelection(categories={'browser': True},
                                  functions={'browser_control': True})
        self.assertEqual(get_registry().enabled(selection), ['browser_tabs', 'browser_control'])
        disabled = ToolSelection(categories={'browser': True},
                                 functions={'browser_control': True, 'browser_tabs': False})
        self.assertEqual(get_registry().enabled(disabled), ['browser_control'])

    def test_youtube_schema_omits_null_placeholders(self):
        schema = get_registry().tools['youtube_control'].schema()['function']['parameters']
        self.assertEqual(schema['required'], ['action'])
        self.assertEqual(schema['properties']['query']['type'], 'string')
        self.assertNotIn('default', schema['properties']['query'])
        self.assertEqual(set(schema['properties']), {'action', 'query', 'target', 'seconds'})

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

    async def test_find_returns_all_matching_positions(self):
        from orion.worker.tools.text.tools import find_in_text, FindInTextInput
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        result = await find_in_text(FindInTextInput(text='a' * 10000, query='a'), context)
        self.assertEqual(result['positions'], list(range(10000)))
        self.assertNotIn('truncated', result)

    async def test_timeout_full_result_and_cancellation(self):
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
        result = await execute_tool(replace(CATEGORY.tools[0], handler=large), '{}', replace(context, cancelled=AsyncMock(return_value=False)))
        self.assertEqual(result, 'x' * 32768)


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
    async def test_ambiguous_media_seek_returns_to_model_for_targeted_retry(self):
        conversation = self.conversation(['media_control'])
        requests = []
        result = {'status': 'ambiguous', 'playing': [
            {'title': 'Eylül Sonu - Yansımalar', 'service': 'ytmusic', 'tab_id': 'music'},
            {'title': 'Karıncalar Faz Değiştiriyor', 'service': 'youtube', 'tab_id': 'video'}]}
        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) == 1:
                yield 'tool_calls', json.dumps([call(name='media_control',
                    arguments='{"action":"seek","seconds":60}')])
            elif len(requests) == 2:
                yield 'tool_calls', json.dumps([call('retry', name='media_control',
                    arguments='{"action":"seek","seconds":60,"target":"music"}')])
            else:
                yield 'content', 'Şarkıyı 1:00 konumuna aldım.'
        with patch('orion.worker.services.tool_runner.execute_tool',
                   AsyncMock(side_effect=[result, {'status': 'ok', 'current_time': 60}])) as execute:
            events = [event async for event in conversation.stream(
                [{'role': 'user', 'content': 'şarkıyı 1. dakikaya alsana'}], RuntimeSettings(),
                AsyncMock(return_value=False), stream)]
        self.assertEqual(len(requests), 3)
        self.assertEqual(json.loads(requests[1][-1]['content']), result)
        self.assertEqual(requests[1][0]['content'], 'şarkıyı 1. dakikaya alsana')
        self.assertEqual(json.loads(execute.call_args_list[1].args[1])['target'], 'music')
        self.assertEqual(events[-1], ('content', 'Şarkıyı 1:00 konumuna aldım.'))
        self.assertEqual([item['status'] for item in conversation.activity], ['completed', 'completed'])

    async def test_ambiguous_media_seek_allows_model_clarification(self):
        conversation = self.conversation(['media_control'])
        requests = []
        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) == 1:
                yield 'tool_calls', json.dumps([call(name='media_control',
                    arguments='{"action":"seek","seconds":60}')])
            else:
                yield 'content', 'İki şarkı çalıyor, hangisini kastediyorsun?'
        result = {'status': 'ambiguous', 'playing': [
            {'title': 'Şarkı A', 'tab_id': 'a'}, {'title': 'Şarkı B', 'tab_id': 'b'}]}
        with patch('orion.worker.services.tool_runner.execute_tool', AsyncMock(return_value=result)):
            _ = [event async for event in conversation.stream([], RuntimeSettings(),
                                      AsyncMock(return_value=False), stream)]
        self.assertEqual(len(requests), 2)
        self.assertEqual(conversation.final_content, 'İki şarkı çalıyor, hangisini kastediyorsun?')

    async def test_failed_media_seek_returns_to_model_for_explanation(self):
        conversation = self.conversation(['media_control'])
        requests = []
        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) == 1:
                yield 'tool_calls', json.dumps([call(name='media_control',
                    arguments='{"action":"seek","seconds":60}')])
            else:
                yield 'content', 'The player position could not be verified.'
        failure = {'status': 'seek_unconfirmed', 'current_time': 60,
                   'message': 'Konum doğrulanamadı: oynatma ilerlemiyor.'}
        with patch('orion.worker.services.tool_runner.execute_tool', AsyncMock(return_value=failure)):
            events = [event async for event in conversation.stream([], RuntimeSettings(),
                                        AsyncMock(return_value=False), stream)]
        self.assertEqual(len(requests), 2)
        self.assertEqual(json.loads(requests[1][-1]["content"]), failure)
        self.assertEqual(conversation.final_content, 'The player position could not be verified.')
        self.assertEqual(conversation.activity[0]['status'], 'error')
        self.assertNotIn('aldım', str(events))

    async def test_google_failure_stops_without_model_invention_or_web_fallback(self):
        for result in ({'status': 'timeout', 'recovery': 'google_session'},
                       {'status': 'blocked'}, {'status': 'ok', 'answer': ''}):
            conversation = self.conversation(['google_ai_mode', 'research_web'])
            requests = []
            async def stream(messages, settings, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    yield 'tool_calls', json.dumps([
                        call(name='google_ai_mode', arguments='{"query":"test"}'),
                        call('call_2', name='research_web', arguments='{"query":"test"}'),
                    ])
                else:
                    yield 'content', 'Invented researched answer'
            with patch('orion.worker.services.tool_runner.execute_tool', AsyncMock(return_value=result)) as execute:
                events = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), stream)]
            execute.assert_awaited_once()
            self.assertEqual(len(requests), 1)
            self.assertEqual(events[-1][0], 'content_snapshot')
            self.assertIn('normal web aramasına geçilmedi', conversation.final_content)
            self.assertNotIn('Invented', conversation.final_content)
            self.assertEqual([item['status'] for item in conversation.activity], ['error', 'error'])

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

    async def test_reused_tool_call_id_in_followup_is_remapped(self):
        conversation = self.conversation()
        requests = []
        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) <= 2:
                yield 'tool_calls', json.dumps([call('reused')])
            else:
                yield 'content', 'Done'
        events = [event async for event in conversation.stream(
            [], RuntimeSettings(), AsyncMock(return_value=False), stream)]
        self.assertEqual([item['status'] for item in conversation.activity], ['completed', 'completed'])
        self.assertEqual(conversation.activity[0]['call_id'], 'reused')
        self.assertNotEqual(conversation.activity[1]['call_id'], 'reused')
        self.assertEqual(requests[2][-1]['tool_call_id'], conversation.activity[1]['call_id'])
        self.assertEqual(events[-1], ('content', 'Done'))

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
            self.assertLessEqual(len(conversation.activity), 64)

    def test_web_catalog_and_legacy_selection_migration(self):
        registry = get_registry()
        self.assertEqual([t.id for t in registry.categories['web_search'].tools], ['research_web'])
        for functions, expected in (
            ({'search_web': True, 'read_pages': True}, True),
            ({'read_page': True}, True),
            ({'search_web': False}, False),
            ({'search_web': True, 'research_web': False}, False),
        ):
            selection = ToolSelection(categories={'web_search': True}, functions=functions)
            registry.validate_selection(selection)
            self.assertEqual(selection.functions, {'research_web': expected})
            self.assertEqual(registry.enabled(selection), ['research_web'] if expected else [])
        disabled = ToolSelection(categories={'web_search': False}, functions={'search_web': True})
        self.assertEqual(registry.enabled(disabled), [])
        self.assertEqual(self.conversation(['search_web', 'read_page']).enabled, {'research_web'})

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
        history[4]['content'] = json.dumps({'status': 'ok', 'page': {
            'url': 'https://example.com', 'title': 'Example', 'text': 'x' * 19000,
            'elements': [{'tag': 'BUTTON', 'label': 'Play'}] * 100}})
        compact = json.loads(model_history(history, 20)[4]['content'])['page']
        self.assertEqual(len(compact['text']), 500)
        self.assertEqual(len(compact['elements']), 10)


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
