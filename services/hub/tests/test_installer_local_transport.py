"""Installer browser entry stays local without certificate trust configuration."""
import importlib
import unittest
from unittest.mock import patch

import httpx

with patch('signal.signal'):
    installer = importlib.import_module('installer.app.__main__')


class InstallerLocalTransportTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, peer='127.0.0.1', url='http://localhost:7171/api/ping', headers=None):
        transport = httpx.ASGITransport(app=installer.app, client=(peer, 12345))
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get(url, headers=headers)

    async def test_local_http_ping_and_html_need_no_certificate(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'app': 'orion-installer'})
        self.assertIn('no-store', response.headers['cache-control'])
        response = await self.request(url='http://127.0.0.1:7171/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])

    async def test_lan_peer_host_rebinding_and_forwarding_fail(self):
        for peer, url, headers in [
            ('192.168.1.100', 'http://localhost:7171/api/ping', {}),
            ('127.0.0.1', 'http://attacker.example:7171/api/ping', {}),
            ('127.0.0.1', 'http://localhost:7171/api/ping', {'X-Forwarded-For': '127.0.0.1'}),
            ('127.0.0.1', 'http://localhost:7171/api/ping', {'Host': 'localhost:9999'}),
        ]:
            with self.subTest(peer=peer, url=url, headers=headers):
                self.assertEqual((await self.request(peer, url, headers)).status_code, 403)

    async def test_only_same_origin_browser_requests_allowed(self):
        self.assertEqual((await self.request(headers={'Origin': 'http://localhost:7171'})).status_code, 200)
        for headers in [{'Origin': 'http://localhost:8765'}, {'Origin': 'https://attacker.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual((await self.request(headers=headers)).status_code, 403)
