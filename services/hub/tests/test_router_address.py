import os
import unittest
from unittest.mock import patch

from services.shared.environment import get_router_base_urls
from orion.worker.services.router import _router_urls


class RouterAddressTests(unittest.TestCase):
    def setUp(self):
        # These legacy config unit tests must not read an actual owner's selection.
        selected = patch('services.shared.router_connection.selected_router_url', return_value=None)
        selected.start()
        self.addCleanup(selected.stop)
    def test_explicit_hostname_url_is_used_without_ip_configuration(self):
        with patch.dict(os.environ, {'ROUTER_BASE_URL': 'http://orionrouter.local:20128/v1/'}, clear=True):
            self.assertEqual(get_router_base_urls(), ['http://orionrouter.local:20128'])
            self.assertEqual(_router_urls('/v1/chat/completions'), ['http://orionrouter.local:20128/v1/chat/completions'])

    def test_explicit_url_overrides_legacy_host_and_port(self):
        with patch.dict(os.environ, {'ROUTER_BASE_URL': 'https://router.example/', 'ROUTER_HOST': '192.168.1.7', 'ROUTER_PORT': '1234'}, clear=True):
            self.assertEqual(get_router_base_urls(), ['https://router.example'])

    def test_existing_native_and_docker_defaults_remain_usable_before_mdns_setup(self):
        with patch.dict(os.environ, {'ROUTER_BASE_URL': '', 'ROUTER_HOST': 'router', 'ROUTER_PORT': '20128'}, clear=True):
            with patch('services.shared.environment.os.path.exists', return_value=False):
                self.assertEqual(get_router_base_urls(), ['http://127.0.0.1:20128'])
            with patch('services.shared.environment.os.path.exists', return_value=True):
                self.assertEqual(get_router_base_urls(), ['http://router:20128'])

    def test_custom_hostnames_are_already_supported_by_legacy_configuration(self):
        with patch.dict(os.environ, {'ROUTER_HOST': 'orionrouter.local', 'ROUTER_PORT': '20128'}, clear=True):
            self.assertEqual(get_router_base_urls(), ['http://orionrouter.local:20128'])

    def test_bad_or_sensitive_urls_are_rejected_without_printing_credentials(self):
        for value in ['file:///router', 'http://', 'http://user:password@router.local',
                      'http://router.local:bad', 'http://router.local:0',
                      'http://router.local?token=secret', 'http://router.local/#secret',
                      'http://router.local/some/path']:
            with patch.dict(os.environ, {'ROUTER_BASE_URL': value}, clear=True), self.assertRaises(RuntimeError) as failure:
                get_router_base_urls()
            self.assertNotIn(value, str(failure.exception))
