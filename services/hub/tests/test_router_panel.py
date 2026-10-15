import unittest
from unittest.mock import AsyncMock, patch

from orion.kernel import router_panel

RECORD = {'id': 'router-a', 'fp': 'a' * 64, 'url': 'https://127.0.0.1:9443'}
LOCAL = {**RECORD, 'name': 'Local Router'}
DATA = {'dashboard_url': 'https://192.168.1.105:9443/dashboard',
        'local_dashboard_url': 'http://localhost:20128/dashboard'}


class RouterPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_same_pc_prefers_configured_local_port(self):
        with patch.object(router_panel, 'local_router_identity', AsyncMock(return_value=LOCAL)), patch.object(router_panel, 'verify_router', AsyncMock(return_value=LOCAL)) as verify:
            result = await router_panel.navigation(RECORD, {**DATA, 'local_dashboard_url': 'http://localhost:20555/dashboard'}, desktop=True)
        self.assertEqual(result, {'dashboard_url': 'http://localhost:20555/dashboard', 'dashboard_scope': 'local', 'dashboard_open_in_app': True})
        verify.assert_awaited_once_with(RECORD['url'], RECORD['id'], RECORD['fp'])

    async def test_phone_never_gets_loopback_or_triggers_local_probe(self):
        with patch.object(router_panel, 'local_router_identity', AsyncMock()) as local:
            result = await router_panel.navigation(RECORD, DATA, desktop=False)
        self.assertEqual(result['dashboard_url'], DATA['dashboard_url'])
        self.assertFalse(result['dashboard_open_in_app'])
        local.assert_not_awaited()

    async def test_missing_mismatched_and_stale_registration_use_https(self):
        for registration in [None, {**LOCAL, 'id': 'other'}, {**LOCAL, 'fp': 'b' * 64}]:
            with patch.object(router_panel, 'local_router_identity', AsyncMock(return_value=registration)), patch.object(router_panel, 'verify_router', AsyncMock()) as verify:
                result = await router_panel.navigation(RECORD, DATA, desktop=True)
            self.assertEqual(result['dashboard_url'], DATA['dashboard_url'])
            self.assertEqual(result['dashboard_scope'], 'network')
            verify.assert_not_awaited()
        with patch.object(router_panel, 'local_router_identity', AsyncMock(return_value=LOCAL)), patch.object(router_panel, 'verify_router', AsyncMock(side_effect=ValueError('identity mismatch'))):
            self.assertEqual((await router_panel.navigation(RECORD, DATA, desktop=True))['dashboard_scope'], 'network')

    async def test_current_https_vpn_address_precedes_advertised_lan(self):
        record = {**RECORD, 'url': 'https://router.vpn.example:9555'}
        result = await router_panel.navigation(record, DATA, desktop=False)
        self.assertEqual(result['dashboard_url'], 'https://router.vpn.example:9555/dashboard')

    async def test_old_router_without_local_metadata_still_opens_https(self):
        result = await router_panel.navigation(RECORD, {'dashboard_url': DATA['dashboard_url']}, desktop=True)
        self.assertEqual(result['dashboard_url'], DATA['dashboard_url'])

    def test_untrusted_navigation_cannot_include_credentials_tokens_or_plain_lan_http(self):
        for value in [None, 'http://192.168.1.5:20128/dashboard', 'https://localhost:9443/dashboard',
                      'https://127.0.0.2:9443/dashboard', 'https://0.0.0.0:9443/dashboard', 'https://[::ffff:127.0.0.1]:9443/dashboard',
                      'https://name:secret@router/dashboard', 'https://router/dashboard?token=secret',
                      'https://router/dashboard#key', 'https://router/other', 'https://router:bad/dashboard']:
            self.assertIsNone(router_panel.panel_url(value), value)
        self.assertIsNone(router_panel.panel_url('http://192.168.1.5:20128/dashboard', local=True))
        self.assertEqual(router_panel.panel_url('https://[fd00::1]:9443/dashboard'), 'https://[fd00::1]:9443/dashboard')
