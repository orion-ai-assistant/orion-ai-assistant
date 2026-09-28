import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.contracts.settings import RuntimeSettings
from orion.kernel.config import build_runtime_settings
from orion.worker.services.tool_runner import ToolConversation, stream_tool_answer, ToolCancelled, google_direct_text
from orion.worker.tools._base import ToolContext
from orion.worker.tools.google_ai.tools import GOOGLE_AI_MODE


def call():
    return {'id': 'google-1', 'type': 'function', 'function': {
        'name': 'google_ai_mode', 'arguments': '{"query":"test"}'}}


class GoogleDirectStreamTests(unittest.IsolatedAsyncioTestCase):
    def conversation(self):
        return ToolConversation(SimpleNamespace(user_id='u', chat_id='c', turn_id='t'), ['google_ai_mode'])

    def test_default_on_and_persisted_boolean(self):
        self.assertTrue(RuntimeSettings().google_ai_direct_stream)
        self.assertTrue(build_runtime_settings({'google_ai_direct_stream': 'true'}).google_ai_direct_stream)
        self.assertFalse(build_runtime_settings({'google_ai_direct_stream': 'false'}).google_ai_direct_stream)

    def test_markdown_preserved_and_old_plain_text_still_escaped(self):
        answer = '## Başlık\n\n**Kalın**\n\n- Liste'
        self.assertIn(answer, google_direct_text(answer, {'answer_format': 'markdown'}))
        self.assertIn(answer, google_direct_text(answer, answer_format='markdown'))
        self.assertIn('\\*\\*Kalın\\*\\*', google_direct_text('**Kalın**'))

    async def test_live_snapshots_before_result_revisions_and_no_second_model(self):
        conversation = self.conversation()
        advance = asyncio.Event()
        model_calls = []
        async def model(*args, **kwargs):
            model_calls.append(kwargs)
            yield 'content', 'Araştırıyorum.\n\n'
            yield 'tool_calls', json.dumps([call()])
        async def execute(tool, arguments, context):
            await context.emit_answer('First longer draft')
            await advance.wait()
            await context.emit_answer('Corrected')
            await asyncio.sleep(.1)
            return {'status': 'ok', 'answer': 'Corrected', 'sources': [
                {'url': 'https://example.org/source', 'title': 'Actual source'}]}
        events = []
        with patch('orion.worker.services.tool_runner.execute_tool', execute):
            async for event in conversation.stream([], RuntimeSettings(google_ai_direct_stream=True), AsyncMock(return_value=False), model):
                events.append(event)
                if event[0] == 'content_snapshot' and 'First longer draft' in event[1]:
                    self.assertFalse(any(kind == 'tool_result' for kind, _ in events))
                    advance.set()
        self.assertEqual(len(model_calls), 1)
        self.assertEqual(conversation.direct_tool_call_id, 'google-1')
        self.assertIn('Actual source', conversation.final_content)
        self.assertNotIn('First longer draft', conversation.final_content)
        self.assertIn('Corrected', conversation.final_content)
        self.assertTrue(events[-1][1].startswith('Araştırıyorum.\n\n**Google AI Mode**'))
        self.assertEqual(conversation.transcript[-1]['role'], 'tool')
        self.assertEqual(json.loads(conversation.transcript[-1]['content'])['answer'], 'Corrected')

    async def test_disabled_setting_keeps_normal_model_followup(self):
        conversation = self.conversation()
        model_calls = []
        async def model(*args, **kwargs):
            model_calls.append(kwargs)
            if len(model_calls) == 1:
                yield 'tool_calls', json.dumps([call()])
            else:
                yield 'content', 'Orion summary'
        async def execute(tool, arguments, context):
            self.assertIsNone(context.emit_answer)
            return {'status': 'ok', 'answer': 'Google answer', 'sources': []}
        with patch('orion.worker.services.tool_runner.execute_tool', execute):
            events = [event async for event in conversation.stream([], RuntimeSettings(google_ai_direct_stream=False), AsyncMock(return_value=False), model)]
        self.assertEqual(len(model_calls), 2)
        self.assertEqual(events[-1], ('content', 'Orion summary'))

    async def test_failed_stream_replaces_preview_with_error(self):
        conversation = self.conversation()
        requests = []
        async def model(*args, **kwargs):
            requests.append(kwargs)
            yield 'tool_calls', json.dumps([call()])
        async def execute(tool, arguments, context):
            await context.emit_answer('Incomplete draft')
            await asyncio.sleep(.1)
            return {'status': 'timeout', 'recovery': 'google_session'}
        with patch('orion.worker.services.tool_runner.execute_tool', execute):
            events = [event async for event in conversation.stream([], RuntimeSettings(google_ai_direct_stream=True), AsyncMock(return_value=False), model)]
        self.assertTrue(any('Incomplete draft' in value for kind, value in events if kind == 'content_snapshot'))
        self.assertNotIn('Incomplete draft', conversation.final_content)
        self.assertIn('alınamadı', conversation.final_content)
        self.assertEqual(len(requests), 1)

    async def test_stop_and_consumer_close_cancel_underlying_handler(self):
        for close_consumer in (False, True):
            ended = asyncio.Event()
            stopped = False
            async def cancelled():
                return stopped
            async def handler(args, context):
                try:
                    await context.emit_answer('Visible draft')
                    await asyncio.Event().wait()
                finally:
                    ended.set()
            context = ToolContext('u', 'c', 't', cancelled)
            stream = stream_tool_answer(replace(GOOGLE_AI_MODE, handler=handler), '{"query":"test"}', context)
            self.assertEqual(await anext(stream), ('answer', 'Visible draft'))
            if close_consumer:
                await stream.aclose()
            else:
                stopped = True
                with self.assertRaises(ToolCancelled):
                    await anext(stream)
            await asyncio.wait_for(ended.wait(), 1)

    async def test_stop_preserves_visible_google_draft_as_tool_result(self):
        conversation = self.conversation()
        stopped = False
        ended = asyncio.Event()

        async def cancelled():
            return stopped

        async def model(*args, **kwargs):
            yield 'tool_calls', json.dumps([call()])

        async def execute(tool, arguments, context):
            try:
                await context.emit_answer('Visible Google draft')
                while not stopped:
                    await asyncio.sleep(.01)
                raise ToolCancelled()
            finally:
                ended.set()

        events = []
        with patch('orion.worker.services.tool_runner.execute_tool', execute):
            async for event in conversation.stream([], RuntimeSettings(google_ai_direct_stream=True), cancelled, model):
                events.append(event)
                if event[0] == 'content_snapshot':
                    stopped = True

        await asyncio.wait_for(ended.wait(), 1)
        result = json.loads(conversation.transcript[-1]['content'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['answer'], 'Visible Google draft')
        self.assertEqual(conversation.direct_tool_call_id, 'google-1')
        self.assertEqual(conversation.activity[-1]['status'], 'cancelled')
        self.assertEqual([kind for kind, _ in events][-1], 'tool_result')

    async def test_late_google_text_after_stop_reaches_display_and_partial_tool_result(self):
        conversation = self.conversation()
        stopped = False

        async def cancelled():
            return stopped

        async def model(*args, **kwargs):
            yield 'tool_calls', json.dumps([call()])

        async def handler(args, context):
            await context.emit_answer('First draft')
            while not await context.cancelled():
                await asyncio.sleep(.005)
            await context.emit_answer('First draft, late text')
            await asyncio.Event().wait()

        tool = replace(GOOGLE_AI_MODE, handler=handler)
        events = []
        with patch('orion.worker.services.tool_runner.get_registry', return_value=SimpleNamespace(tools={'google_ai_mode': tool})), \
             patch('orion.worker.services.tool_runner.GOOGLE_STOP_GRACE_SECONDS', .15):
            async for event in conversation.stream([], RuntimeSettings(), cancelled, model):
                events.append(event)
                if event[0] == 'content_snapshot' and 'First draft' in event[1]:
                    stopped = True

        previews = [value for kind, value in events if kind == 'content_snapshot']
        self.assertTrue(any('First draft, late text' in value for value in previews))
        result = json.loads(conversation.transcript[-1]['content'])
        self.assertEqual(result['answer'], 'First draft, late text')
        self.assertTrue(result['cancelled'])

    async def test_google_completion_during_stop_grace_reaches_display(self):
        conversation = self.conversation()
        stopped = False

        async def cancelled():
            return stopped

        async def model(*args, **kwargs):
            yield 'tool_calls', json.dumps([call()])

        async def handler(args, context):
            await context.emit_answer('First draft')
            while not await context.cancelled():
                await asyncio.sleep(.005)
            await context.emit_answer('Completed after stop')
            return {'status': 'ok', 'answer': 'Completed after stop', 'answer_format': 'markdown', 'sources': []}

        tool = replace(GOOGLE_AI_MODE, handler=handler)
        events = []
        with patch('orion.worker.services.tool_runner.get_registry', return_value=SimpleNamespace(tools={'google_ai_mode': tool})), \
             patch('orion.worker.services.tool_runner.GOOGLE_STOP_GRACE_SECONDS', .15):
            async for event in conversation.stream([], RuntimeSettings(), cancelled, model):
                events.append(event)
                if event[0] == 'content_snapshot' and 'First draft' in event[1]:
                    stopped = True

        self.assertEqual(conversation.activity[-1]['status'], 'completed')
        self.assertEqual(json.loads(conversation.transcript[-1]['content'])['answer'], 'Completed after stop')
        self.assertIn('Completed after stop', [value for kind, value in events if kind == 'content_snapshot'][-1])
