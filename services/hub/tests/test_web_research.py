import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.contracts.settings import RuntimeSettings
from orion.worker.services.tool_runner import ToolConversation, WEB_TOOLS, execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools.web_search import research


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_search_dedup_parallel_reads_compact_partial_evidence(self):
        context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))
        rows = [
            {'url': 'https://one.example/article?utm_source=a', 'title': 'First', 'snippet': 'Search excerpt'},
            {'url': 'https://one.example/article?utm_source=b#top', 'title': 'Duplicate', 'snippet': 'Duplicate'},
            {'url': 'https://one.example/another', 'title': 'Same domain', 'snippet': 'Another article'},
            *[{'url': f'https://site{i}.example/article', 'title': f'Source {i}', 'snippet': 'Search excerpt'} for i in range(2, 7)],
        ]
        urls, ready = [], asyncio.Event()

        async def read(args, ctx):
            urls.append(args.url)
            if len(urls) == 5:
                ready.set()
            await asyncio.wait_for(ready.wait(), .5)  # Fails if reads are sequential.
            if 'site5.' in args.url:
                raise TimeoutError('upstream timed out')
            return {'status': 'ok', 'url': args.url, 'title': 'Read title',
                    'date': '2026-09-27', 'text': '"' * 4000}

        search = AsyncMock(return_value={'status': 'ok', 'results': rows})
        with patch.object(research, 'search_web', search), patch.object(research, 'read_page', side_effect=read):
            result = await execute_tool(research.RESEARCH_WEB, '{"query":"  technology  ","recency":"day"}', context)
        search.assert_awaited_once()
        self.assertEqual(search.call_args.args[0].query, 'technology')
        self.assertEqual(search.call_args.args[0].recency, 'day')
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(len(urls), 5)
        self.assertNotIn('https://one.example/another', urls)
        self.assertFalse(any('utm_source=b' in url for url in urls))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([s['url'] for s in result['sources']], urls)
        self.assertEqual(result['sources'][0]['text'], '"' * 4000)
        self.assertEqual(result['sources'][-1]['evidence'], 'search_snippet')
        self.assertEqual(result['sources'][-1]['read_status'], 'timeout')
        self.assertEqual(result['sources'][0]['evidence'], 'page_excerpt')
        self.assertTrue(all('truncated' not in source for source in result['sources']))
        with patch.object(research, 'search_web', AsyncMock(return_value={'status': 'no_results', 'results': []})), \
                patch.object(research, 'read_page', AsyncMock()) as reader:
            empty = await execute_tool(research.RESEARCH_WEB, '{"query":"missing"}', context)
        self.assertEqual(empty['status'], 'no_results')
        reader.assert_not_called()

    async def test_research_then_final_two_model_calls_and_no_web_reentry(self):
        for fails in (False, True):
            conversation = ToolConversation(SimpleNamespace(user_id='u', chat_id='c', turn_id='t'),
                                            [*WEB_TOOLS, 'get_current_time'])
            requests = []

            async def stream(messages, settings, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    yield 'tool_calls', json.dumps([
                        {'id': f'call_{i}', 'type': 'function', 'function': {
                            'name': name, 'arguments': '{"query":"q"}' if name == 'research_web' else '{"url":"https://example.com"}',
                        }} for i, name in enumerate(['research_web', 'research_web', 'read_page'])
                    ])
                else:
                    yield 'content', 'Final answer from available evidence.'

            handler = AsyncMock(side_effect=TimeoutError('deadline') if fails else None,
                                return_value={'status': 'ok', 'sources': []})
            with patch('orion.worker.services.tool_runner.execute_tool', handler):
                events = [event async for event in conversation.stream(
                    [], RuntimeSettings(), AsyncMock(return_value=False), stream)]
            handler.assert_awaited_once()
            self.assertEqual(len(requests), 2)
            self.assertEqual([tool['function']['name'] for tool in requests[1]['tools']], ['get_current_time'])
            self.assertTrue(all(item['status'] == 'error' for item in conversation.activity[1:]))
            self.assertEqual(events[-1], ('content', 'Final answer from available evidence.'))
            self.assertTrue(conversation.research_used)
            fresh = ToolConversation(SimpleNamespace(user_id='u', chat_id='c', turn_id='new'), ['research_web'])
            self.assertFalse(fresh.research_used)
