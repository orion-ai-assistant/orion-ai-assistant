import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from orion.contracts.tools import default_tool_selection
from orion.worker.services.tool_runner import execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.web_search.tools import SEARCH_WEB


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        self.client = Mock()
        self.constructor = Mock(return_value=self.client)
        self.modules = patch.dict(sys.modules, ddgs=SimpleNamespace(DDGS=self.constructor))
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_discovery_default_off_and_lazy_import(self):
        get_registry.cache_clear()
        registry = get_registry()
        self.assertIn('web_search', registry.categories)
        self.assertIn('search_web', registry.tools)
        self.assertNotIn('search_web', registry.enabled(default_tool_selection()))
        self.constructor.assert_not_called()

    async def test_provider_parameters_and_normalized_results(self):
        self.client.text.return_value = [{'title': 'Example', 'href': 'https://example.com', 'body': 'Summary'}]
        result = await execute_tool(SEARCH_WEB, '{"query":"  python  ","max_results":2}', self.context)
        self.constructor.assert_called_once_with(timeout=8)
        self.client.text.assert_called_once_with('python', max_results=2, backend='duckduckgo', safesearch='moderate')
        self.assertEqual(result, {'provider': 'duckduckgo', 'results': [
            {'title': 'Example', 'url': 'https://example.com', 'snippet': 'Summary'}]})

    async def test_invalid_inputs_do_not_call_provider(self):
        for args in ({'query': ' '}, {'query': 'q', 'max_results': 6},
                     {'query': 'q', 'max_results': '2'}, {'query': 'q', 'backend': 'auto'}):
            with self.assertRaises(ValidationError):
                await execute_tool(SEARCH_WEB, json.dumps(args), self.context)
        self.constructor.assert_not_called()

    async def test_filters_invalid_urls_and_bounds_unicode_output(self):
        self.client.text.return_value = [
            {'href': 'javascript:alert(1)'}, {'href': 'https://[broken'},
            {'href': 'https://example.com/\n'}, {'href': 'https://example.com/' + 'x' * 900},
            {'href': 'https://example.com', 'title': 'ok', 'body': 'fine'},
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
        self.assertEqual(len(result['results']), 1)
        self.client.text.return_value = [
            {'href': 'https://example.com/' + '\U0001f600' * 700,
             'title': '\U0001f600' * 500, 'body': '\U0001f600' * 10000} for _ in range(10)
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
        self.assertEqual(len(result['results']), 5)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 32768)

    async def test_empty_results_and_provider_failure(self):
        self.client.text.return_value = []
        result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
        self.assertEqual(result['results'], [])
        self.client.text.side_effect = TimeoutError('provider timed out')
        with self.assertRaises(TimeoutError):
            await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
