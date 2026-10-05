import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from orion.api import hub_directory
from orion.api.access_boundary import is_desktop_peer
from orion.api.main import create_app

ID = '12345678-1234-4321-8123-123456789abc'


class DirectoryTests(unittest.IsolatedAsyncioTestCase):
    def test_untrusted_discovery_records_cannot_supply_arbitrary_urls(self):
        info = SimpleNamespace(decoded_properties={'id': ID, 'name': 'Home', 'path': '/api/v1', 'scheme': 'https'}, port=8910,
                               parsed_addresses=lambda version: ['192.168.1.20'])
        self.assertEqual(hub_directory.service_record(info)['url'], 'https://192.168.1.20:8910/')
        for invalid in ['127.0.0.1', '8.8.8.8', 'not-an-ip']:
            info.parsed_addresses = lambda version: [invalid]
            self.assertIsNone(hub_directory.service_record(info))
        info.decoded_properties['id'] = 'not-a-uuid'
        self.assertIsNone(hub_directory.service_record(info))

    async def test_browser_self_peer_gate_rejects_lan_neighbors_and_forwarding(self):
        scope = {'client': ('192.168.1.20', 1234), 'server': ('192.168.1.20', 80), 'headers': [(b'host', b'192.168.1.20')]}
        with patch('orion.api.discovery.lan_addresses', return_value=['192.168.1.20']):
            self.assertTrue(is_desktop_peer(scope))
            self.assertFalse(is_desktop_peer({**scope, 'client': ('192.168.1.21', 1234)}))
            self.assertFalse(is_desktop_peer({**scope, 'headers': scope['headers'] + [(b'x-forwarded-for', b'192.168.1.20')]}))
            self.assertFalse(is_desktop_peer({**scope, 'headers': [(b'host', b'attacker.example')]}))

    async def test_public_directory_deduplicates_self_without_authentication(self):
        app = create_app(public=True)
        app.state.hub_directory.list = AsyncMock(return_value=[{'id': ID, 'name': 'Self', 'url': 'https://192.168.1.20:8910/'}])
        with patch.object(hub_directory, 'local_identity', return_value={'id': ID, 'name': 'Self'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('192.168.1.21', 1234)), base_url='https://192.168.1.20:8910') as client:
                response = await client.get('/api/v1/discovery/hubs')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['hubs'], [{'id': ID, 'name': 'Self', 'url': '/'}])
                self.assertEqual((await client.get('/api/v1/discovery/self')).json()['id'], ID)
                self.assertEqual((await client.get('/api/v1/browser')).status_code, 403)

    async def test_concurrent_scans_share_cached_result(self):
        directory = hub_directory.HubDirectory()
        directory.scan = AsyncMock(return_value=[])
        import asyncio
        await asyncio.gather(*(directory.list() for _ in range(8)))
        directory.scan.assert_awaited_once()

    async def test_internet_clients_do_not_trigger_lan_scan(self):
        app = create_app(public=True)
        app.state.hub_directory.list = AsyncMock(return_value=[])
        with patch.object(hub_directory, 'local_identity', return_value={'id': ID, 'name': 'Self'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('203.0.113.1', 1234)), base_url='https://orion.example') as client:
                response = await client.get('/api/v1/discovery/hubs')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()['hubs']), 1)
        app.state.hub_directory.list.assert_not_awaited()
