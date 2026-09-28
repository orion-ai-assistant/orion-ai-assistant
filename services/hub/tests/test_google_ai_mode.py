import json
from contextlib import nullcontext
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from orion.contracts.tools import default_tool_selection
from orion.worker.services.tool_runner import execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.google_ai import tools


class GoogleAIModeTests(unittest.IsolatedAsyncioTestCase):
    def test_long_query_is_preserved_and_empty_query_rejected(self):
        from pydantic import ValidationError
        from orion.api.google_ai_routes import OpenSession
        query = 'A detailed question ' * 1000
        self.assertEqual(len(query), 20000)
        self.assertEqual(tools.GoogleAIInput(query=query).query, query.strip())
        self.assertEqual(OpenSession(query=query).query, query)
        for model in (tools.GoogleAIInput, OpenSession):
            self.assertNotIn('maxLength', model.model_json_schema()['properties']['query'])
            with self.assertRaises(ValidationError):
                model(query='')

    async def test_separate_category_safe_source_mapping_and_explicit_failures(self):
        registry = get_registry()
        self.assertEqual([t.id for t in registry.categories['google_ai'].tools], ['google_ai_mode'])
        self.assertNotIn('google_ai_mode', registry.enabled(default_tool_selection()))
        self.assertEqual([t.id for t in registry.categories['web_search'].tools], ['research_web'])
        sources = tools._sources([
            {'url': 'https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Farticle', 'title': 'Source'},
            {'url': 'https://example.com/article', 'title': 'Duplicate'},
            {'url': 'https://accounts.google.com/login'}, {'url': 'javascript:alert(1)'},
        ])
        self.assertEqual(sources, [{'url': 'https://example.com/article', 'title': 'Source'}])
        self.assertEqual(tools._blocked_state('https://www.google.com/sorry/index', '')[0], 'blocked')
        self.assertEqual(tools._blocked_state('https://consent.google.com/m', '')[0], 'interaction_required')
        self.assertEqual(tools._blocked_state('https://www.google.com/search',
            'AI Modu şu anda cihazınızda veya hesabınızda kullanılamıyor')[0], 'unavailable')
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        for side_effect, status in ((ImportError(), 'not_configured'), (TimeoutError(), 'timeout')):
            with patch.object(tools, '_read_browser', AsyncMock(side_effect=side_effect)):
                result = await execute_tool(tools.GOOGLE_AI_MODE, '{"query":"largest organism"}', context)
            self.assertEqual(result['status'], status)
            self.assertNotIn('answer', result)
        with patch.object(tools, '_read_browser', AsyncMock(return_value={'status': 'ok', 'answer': 'Google answer', 'sources': sources})) as reader:
            result = await execute_tool(tools.GOOGLE_AI_MODE, '{"query":"a&b"}', context)
        self.assertIn('q=a%26b&udm=50', reader.call_args.args[0])
        self.assertEqual(result['answer'], 'Google answer')
        self.assertEqual(result['provider'], 'google_ai_mode')
        self.assertLess(len(json.dumps(result).encode()), 32768)

    async def test_long_browser_answer_is_streamed_and_returned_in_full(self):
        answer = '\U0001f600' * 20000 + ' END'
        page = AsyncMock()
        page.evaluate.side_effect = [
            {'answer': answer, 'links': [], 'completed': True}, 'https://www.google.com/search',
            {'answer': answer, 'links': [], 'completed': True}, 'https://www.google.com/search']
        lease = MagicMock()
        lease.__aenter__ = AsyncMock(return_value=page)
        lease.__aexit__ = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.tab.return_value = lease
        emit = AsyncMock()
        with patch.object(tools, 'BrowserManager', return_value=manager), \
                patch.object(tools, 'monotonic', side_effect=[0, 0, 0, 0, 5, 5]), \
                patch.object(tools.asyncio, 'sleep', AsyncMock()):
            result = await tools._read_browser('https://www.google.com/search', 'alice', emit)
        emit.assert_awaited_once_with(answer)
        manager.tab.assert_called_once_with('google_ai', background=True)
        self.assertEqual(result['answer'], answer)
        self.assertNotIn('truncated', result)
        page.navigate.assert_awaited_once()
