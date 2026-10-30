import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from orion.api.router_connection import Selection
from orion.kernel.local_security import SecurityStore
from services.shared import router_connection as store
from services.shared.environment import get_router_base_urls
from orion.worker.services.router import _router_urls

ID = '00000000-0000-4000-8000-000000000001'
OTHER = '12345678-1234-4321-8123-123456789abc'
FP = 'a' * 64


class RouterAddressTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {
            'ORION_SECURITY_DIR': str(self.root / 'security'),
            'ORION_ROUTER_CONNECTION_FILE': str(self.root / 'selection.json'),
            'LOCALAPPDATA': str(self.root / 'local'),
            'ROUTER_HOST': 'router', 'ORION_ROUTER_TLS_PORT': '9443',
        })
        env.start()
        self.addCleanup(env.stop)
        self.security = SecurityStore()

    def trust(self, identity=ID, url='https://orionrouter.local:9443', fp=FP):
        record = {'id': identity, 'name': 'Router', 'fp': fp, 'url': url}
        self.security.trust_router(record, url)
        return record

    def select(self, record):
        store.write_connection({**record, 'mode': 'manual'})

    def test_trusted_hostname_is_used_without_ip_configuration(self):
        self.select(self.trust())
        self.assertEqual(get_router_base_urls(), ['https://orionrouter.local:9443'])
        self.assertEqual(_router_urls('/v1/chat/completions'),
                         ['https://orionrouter.local:9443/v1/chat/completions'])

    def test_selection_overrides_legacy_environment(self):
        self.select(self.trust())
        with patch.dict(os.environ, {'ROUTER_BASE_URL': 'http://legacy', 'ROUTER_HOST': '192.0.2.7', 'ROUTER_PORT': '1234'}):
            self.assertEqual(get_router_base_urls(), ['https://orionrouter.local:9443'])

    def test_native_and_docker_https_defaults_and_tls_port(self):
        exists = os.path.exists
        def docker(present):
            # Only the Docker marker is faked; every other path (e.g. a packaged
            # release marker) keeps its real answer.
            return patch('services.shared.environment.os.path.exists',
                         side_effect=lambda path: present if path == '/.dockerenv' else exists(path))
        with docker(False):
            self.assertEqual(get_router_base_urls(), ['https://127.0.0.1:9443'])
        with docker(True):
            self.assertEqual(get_router_base_urls(), ['https://router:9443'])
        with patch.dict(os.environ, {'ORION_ROUTER_TLS_PORT': '10443'}):
            with docker(False):
                self.assertEqual(get_router_base_urls(), ['https://127.0.0.1:10443'])
        self.assertEqual(self.security.routers(), [])

    def test_custom_hostname_fallback_does_not_create_trust(self):
        with patch.dict(os.environ, {'ROUTER_HOST': 'orionrouter.local'}):
            self.assertEqual(get_router_base_urls(), ['https://orionrouter.local:9443'])
        self.assertEqual(self.security.routers(), [])

    def test_bad_urls_are_rejected_with_safe_error_messages(self):
        for value in ['http://router', 'file:///router', 'https://',
                      'https://user:password@router.local', 'https://router.local:bad',
                      'https://router.local:0', 'https://router.local:65536',
                      'https://router.local?token=secret', 'https://router.local/#secret',
                      'https://router.local/some/path']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as failure:
                    store.validate_url(value)
                for sensitive in ('password', 'secret', value):
                    self.assertNotIn(sensitive, str(failure.exception))
                with self.assertRaises(ValueError) as validation:
                    Selection(manual_url=value, manual_id=ID, fp=FP)
                # API sanitizes input separately; inspect validator messages, not raw input repr.
                for error in validation.exception.errors(include_input=False, include_context=False):
                    self.assertNotIn('password', error['msg'])
                    self.assertNotIn('secret', error['msg'])

    def test_localhost_and_v1_are_normalized(self):
        self.assertEqual(store.validate_url('https://localhost:9443/v1/'), 'https://127.0.0.1:9443')

    def test_selected_uuid_missing_from_trust_fails_closed(self):
        self.select(self.trust())
        self.security.remove_router(ID)
        self.trust(OTHER)
        with self.assertRaises(RuntimeError):
            get_router_base_urls()

    def test_selected_pin_mismatch_fails_closed(self):
        self.select({**self.trust(), 'fp': 'b' * 64})
        with self.assertRaises(RuntimeError):
            get_router_base_urls()

    def test_one_trusted_router_can_be_used_without_selection(self):
        self.trust()
        self.assertEqual(get_router_base_urls(), ['https://orionrouter.local:9443'])
        self.assertFalse(store.connection_path().exists())

    def test_multiple_trusted_routers_require_selection(self):
        self.trust()
        self.trust(OTHER, 'https://other.test:9443')
        with self.assertRaises(RuntimeError):
            get_router_base_urls()
