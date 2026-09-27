import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from orion.worker.services.tool_runner import execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools.web_search import page


class Response:
    def __init__(self, body=b'', status=200, headers=None, content_type='text/html'):
        self.body, self.status = body, status
        self.headers = headers or {}
        self.content_type, self.charset = content_type, 'utf-8'
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(Mock(real_url='https://example.com'), (), status=self.status)

    async def iter_chunked(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class ReadPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = ToolContext('u', 'c', 't', AsyncMock(return_value=False))

    async def test_batch_reads_concurrently_preserves_order_and_full_text(self):
        started = 0
        all_started = asyncio.Event()
        urls = [f'https://example.com/{i}/' + 'a' * 1000 for i in range(5)]
        async def fetch(session, url):
            nonlocal started
            started += 1
            if started == 5:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), 1)
            parser = page.PageText()
            parser.feed('<article>' + '&quot;' * 10000 + '</article>')
            return {'status': 'ok', 'url': url, **parser.extract()}
        with patch.object(page, '_fetch_page', side_effect=fetch):
            result = await execute_tool(page.READ_PAGES, json.dumps({'urls': urls}), self.context)
        self.assertEqual([p['url'] for p in result['pages']], urls)
        self.assertTrue(all(p['status'] == 'ok' and 'truncated' not in p for p in result['pages']))
        self.assertTrue(all(p['text'] == '"' * 10000 for p in result['pages']))

    async def test_batch_keeps_success_when_other_pages_fail(self):
        async def fetch(session, url):
            if url.endswith('/slow'):
                raise TimeoutError('page timed out')
            return {'status': 'ok', 'url': url, 'text': 'Useful article'}
        with patch.object(page, '_fetch_page', side_effect=fetch) as fetch_mock:
            result = await execute_tool(page.READ_PAGES, json.dumps({'urls': [
                'https://example.com/article', 'https://example.com/slow', 'http://127.0.0.1',
            ]}), self.context)
        self.assertEqual([p['status'] for p in result['pages']], ['ok', 'timeout', 'provider_error'])
        self.assertEqual(result['pages'][0]['text'], 'Useful article')
        self.assertEqual(result['pages'][2]['error']['type'], 'UnsafeURL')
        self.assertEqual(fetch_mock.await_count, 2)

    def test_url_guards(self):
        for url in ('file:///etc/passwd', 'http://localhost', 'http://127.0.0.1',
                    'http://10.0.0.1', 'http://169.254.169.254', 'http://100.64.0.1',
                    'http://[::1]', 'http://[::ffff:127.0.0.1]', 'http://[fe80::1%25eth0]',
                    'https://example.com:8080', 'https://user:secret@example.com',
                    'https://example.com/\n', 'http://224.0.0.1'):
            with self.subTest(url=url), self.assertRaises(page.UnsafeURL):
                page._validate_url(url)
        self.assertEqual(str(page._validate_url('https://example.com/article#part')), 'https://example.com/article')

    async def test_dns_rejects_private_mixed_and_obfuscated_hosts(self):
        resolver = page.PublicResolver()
        try:
            for host in ('public.example', '2130706433', '127.1'):
                with patch.object(resolver.resolver, 'resolve', AsyncMock(return_value=[
                    {'host': '93.184.216.34'}, {'host': '127.0.0.1'},
                ])):
                    with self.assertRaises(page.UnsafeURL):
                        await resolver.resolve(host, 443)
            with patch.object(resolver.resolver, 'resolve', AsyncMock(return_value=[{'host': '93.184.216.34'}])):
                self.assertEqual(await resolver.resolve('example.com', 443), [{'host': '93.184.216.34'}])
        finally:
            await resolver.close()

    async def test_article_extraction_and_relative_redirect(self):
        html = b'''<html><head><title>Example &amp; news</title>
        <meta property="article:published_time" content="2026-09-27T10:00:00Z"></head>
        <body><nav>Navigation</nav><article><h1>Headline</h1><p>Real content.</p>
        <script>evil()</script><div hidden>hidden</div></article><footer>Footer</footer></body></html>'''
        session = Mock()
        session.get.side_effect = [Response(status=302, headers={'Location': '/article'}), Response(html)]
        result = await page._fetch_page(session, 'https://example.com/start')
        self.assertEqual(result['url'], 'https://example.com/article')
        self.assertEqual(result['title'], 'Example & news')
        self.assertEqual(result['text'], 'Headline Real content.')
        self.assertEqual(result['date'], '2026-09-27T10:00:00Z')
        self.assertNotIn('truncated', result)
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in session.get.call_args_list))

    async def test_redirect_cannot_reach_private_target_and_loops_are_bounded(self):
        for target in ('http://127.0.0.1', '//169.254.169.254/latest/meta-data', 'file:///etc/passwd'):
            session = Mock()
            session.get.return_value = Response(status=302, headers={'Location': target})
            with self.assertRaises(page.UnsafeURL):
                await page._fetch_page(session, 'https://example.com')
            session.get.assert_called_once()
        session = Mock()
        session.get.return_value = Response(status=302, headers={'Location': '/again'})
        with self.assertRaisesRegex(ValueError, 'too many redirects'):
            await page._fetch_page(session, 'https://example.com')
        self.assertEqual(session.get.call_count, 6)

    async def test_response_and_output_limits(self):
        session = Mock()
        session.get.return_value = Response(b'x' * (page.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            await page._fetch_page(session, 'https://example.com')
        session.get.return_value = Response(content_type='application/pdf')
        with self.assertRaisesRegex(ValueError, 'Only HTML'):
            await page._fetch_page(session, 'https://example.com')
        session.get.return_value = Response(('<article>' + '\U0001f600' * 10000 + '</article>').encode())
        result = await page._fetch_page(session, 'https://example.com')
        self.assertNotIn('truncated', result)
        self.assertEqual(result['text'], '\U0001f600' * 10000)
        session.get.return_value = Response(b'"' * 25000, content_type='text/plain')
        result = await page._fetch_page(session, 'https://example.com')
        self.assertNotIn('truncated', result)
        self.assertEqual(result['text'], '"' * 25000)

    async def test_deadline_covers_all_redirects_and_connection_cleanup(self):
        async def slow_fetch(*args):
            await asyncio.sleep(1)
        with patch.object(page, 'PAGE_TIMEOUT', .01), patch.object(page, '_fetch_page', side_effect=slow_fetch):
            result = await execute_tool(page.READ_PAGE, '{"url":"https://example.com"}', self.context)
        self.assertEqual(result['status'], 'timeout')

    async def test_status_timeout_http_error_and_cancel(self):
        for exc, status, http_status in (
            (TimeoutError(), 'timeout', None),
            (aiohttp.ClientResponseError(Mock(real_url='https://example.com'), (), status=403), 'provider_error', 403),
        ):
            with patch.object(page, '_fetch_page', AsyncMock(side_effect=exc)):
                result = await execute_tool(page.READ_PAGE, '{"url":"https://example.com"}', self.context)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['http_status'], http_status)
        with patch.object(page, '_fetch_page', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await page.read_page(page.ReadPageInput(url='https://example.com'), self.context)
        with patch.object(page, 'PublicResolver') as resolver:
            result = await execute_tool(page.READ_PAGE, '{"url":"http://127.0.0.1"}', self.context)
            self.assertEqual(result['error']['type'], 'UnsafeURL')
            resolver.assert_not_called()
