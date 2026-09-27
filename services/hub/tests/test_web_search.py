import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from pydantic import ValidationError

from orion.contracts.tools import default_tool_selection
from orion.worker.services.tool_runner import execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.web_search import tools
from orion.worker.tools.web_search.tools import SEARCH_WEB


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        self.client = Mock()
        self.constructor = Mock(return_value=self.client)
        self.modules = patch('ddgs.DDGS', self.constructor)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_discovery_default_off_and_lazy_import(self):
        get_registry.cache_clear()
        registry = get_registry()
        self.assertIn('web_search', registry.categories)
        self.assertIn('research_web', registry.tools)
        self.assertNotIn('research_web', registry.enabled(default_tool_selection()))
        for name in ('search_web', 'read_page', 'read_pages'):
            self.assertNotIn(name, registry.tools)
        self.constructor.assert_not_called()

    async def test_simple_contract_and_optional_recency(self):
        self.assertEqual(set(SEARCH_WEB.schema()['function']['parameters']['properties']),
                         {'query', 'recency', 'max_results'})
        for recency, timelimit in ((None, None), ('day', 'd'), ('week', 'w'), ('month', 'm'), ('year', 'y')):
            self.client.reset_mock()
            self.client.text.return_value = [{
                'title': 'Example', 'href': 'https://example.com', 'body': 'Summary',
            }]
            result = await execute_tool(SEARCH_WEB, json.dumps({
                'query': '  python  ', **({'recency': recency} if recency else {}),
            }), self.context)
            self.client.text.assert_called_once_with('python', max_results=10, region='wt-wt', timelimit=timelimit,
                                                    backend='auto', safesearch='moderate')
            self.client.news.assert_not_called()
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['provider'], 'ddgs')
            self.assertEqual(result['results'][0]['url'], 'https://example.com')
            self.assertIsNone(result['results'][0]['date'])
            self.assertIsNone(result['results'][0]['source'])

    async def test_thirty_links_in_one_call_with_full_snippets(self):
        self.client.text.return_value = [
            {'href': f'https://example.com/article/{i}', 'title': 'Example', 'body': '\U0001f600' * 1000}
            for i in range(30)
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"python","max_results":30}', self.context)
        self.assertEqual(len(result['results']), 30)
        self.assertEqual(self.client.text.call_count, 1)
        self.assertEqual(self.client.text.call_args.kwargs['max_results'], 30)
        self.assertTrue(all(row['snippet'] == '\U0001f600' * 1000 for row in result['results']))

    async def test_invalid_inputs_do_not_call_provider(self):
        for args in ({'query': ' '}, {'query': 'q', 'max_results': 31},
                     {'query': 'q', 'max_results': '2'}, {'query': 'q', 'backend': 'auto'},
                     {'query': 'q', 'region': 'TR'}, {'query': 'q', 'timelimit': 'day'},
                     {'query': 'q', 'search_type': 'news'}, {'query': 'q', 'recency': 'd'}):
            with self.assertRaises(ValidationError):
                await execute_tool(SEARCH_WEB, json.dumps(args), self.context)
        self.constructor.assert_not_called()

    async def test_filters_deduplicates_and_preserves_unicode_output(self):
        self.client.text.return_value = [
            {'href': 'javascript:alert(1)'}, {'href': 'https://[broken'},
            {'href': 'https://example.com/\n'}, {'href': 'https://example.com/' + 'x' * 1300},
            {'href': 'https://example.com', 'title': 'ok', 'body': 'fine'},
            {'href': 'https://example.com/#same'}, {'href': 'https://example.org'},
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
        self.assertEqual(len(result['results']), 2)
        self.client.text.return_value = [
            {'href': 'https://example.com/' + str(i) + '\U0001f600' * 200,
             'title': '\U0001f600' * 500, 'body': '\U0001f600' * 10000,
             'source': '\U0001f600' * 1000, 'date': 'x' * 1000} for i in range(10)
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"q","max_results":10}', self.context)
        self.assertEqual(len(result['results']), 10)
        self.assertEqual(result['results'][0]['snippet'], '\U0001f600' * 10000)
        self.assertEqual(result['results'][0]['title'], '\U0001f600' * 500)

    async def test_status_classification_and_structured_log(self):
        wrapped = RuntimeError('upstream failed')
        wrapped.response = SimpleNamespace(status_code=429)
        for exc, status, code in (
            (None, 'no_results', None),
            (DDGSException('No results found.'), 'no_results', None),
            (TimeoutException('timed out'), 'timeout', None),
            (TimeoutError('timed out'), 'timeout', None),
            (RatelimitException('rate limited'), 'provider_error', None),
            (DDGSException(wrapped), 'provider_error', 429),
        ):
            self.client.text.return_value = []
            self.client.text.side_effect = exc
            with self.assertLogs(tools.logger, 'INFO') as logs:
                result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
            self.assertEqual(result['status'], status)
            record = json.loads(logs.records[-1].getMessage().removeprefix('web_search '))
            self.assertEqual(record['http_status'], code)
            self.assertEqual(record['query'], 'q')
            self.assertEqual(record['result_count'], 0)
            self.assertIn('duration_ms', record)
            self.assertEqual(record['exception_type'], type(exc).__name__ if exc else None)

    async def test_total_timeout_and_cancellation(self):
        # A cooperative stand-in verifies the deadline without leaving a worker thread.
        async def wait(*args):
            await asyncio.sleep(1)
        with patch.object(tools, 'SEARCH_TIMEOUT', .01), patch.object(tools.asyncio, 'to_thread', side_effect=wait):
            result = await execute_tool(SEARCH_WEB, '{"query":"q"}', self.context)
            self.assertEqual(result['status'], 'timeout')
        with patch.object(tools.asyncio, 'to_thread', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await tools.search_web(tools.WebSearchInput(query='q'), self.context)

    async def test_json_escaping_preserves_all_results(self):
        self.client.text.return_value = [
            {'href': f'https://example.com/{i}/' + '"' * 1100,
             'title': '"' * 200, 'body': '"' * 800, 'source': '"' * 160, 'date': '"' * 80}
            for i in range(10)
        ]
        result = await execute_tool(SEARCH_WEB, '{"query":"q","max_results":10}', self.context)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['results']), 10)
        self.assertEqual(result['results'][-1]['snippet'], '"' * 800)
