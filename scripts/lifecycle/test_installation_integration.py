"""Regression boundaries between installed targets and explicit development roots."""
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from services.shared import native_services
from services.shared.installation_control import import_legacy, register_router
from services.shared.installation_registry import Registry, RegistryError
from scripts.lifecycle.test_environment import LifecycleLab, snapshot
from scripts.lifecycle.run_registry_acceptance import align_synthetic_certificate

ROOT = Path(__file__).resolve().parents[2]


class TargetIsolationTests(unittest.TestCase):
    def test_explicit_development_target_does_not_resolve_to_installed_router(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = root / 'Geliştirme Router'
            dev.mkdir()
            (dev / 'pyproject.toml').write_text('[project]\nname="orion-router"\n', encoding='utf-8')
            installed = root / 'installed'
            with patch('services.shared.router_installation.registered_router_root', return_value=str(installed)) as read:
                self.assertEqual(native_services.router_root(str(dev / 'cli.py')), dev.resolve())
                read.assert_not_called()

    def test_explicit_wrong_product_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pyproject.toml').write_text('[project]\nname="unrelated"\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'not Orion Router'):
                native_services.router_root(str(root / 'cli.py'))

    def test_missing_selection_does_not_create_an_installation(self):
        with patch('services.shared.router_installation.registered_router_root', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'No Router selected'):
                native_services.router_root(None)


class InventoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='installation-integration-')
        self.addCleanup(self.temp.cleanup)
        self.lab = LifecycleLab(self.temp.name)
        for item in self.lab.installations:
            router = item['router']
            (router / 'pyproject.toml').write_text('[project]\nname="orion-router"\nversion="0.1.2"\n', encoding='utf-8')
            for name in ('main.py', 'core/config.py', 'bin/prod.py', 'bin/stop.py', 'orionrouter.ps1', 'orionrouter'):
                path = router / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('raise RuntimeError("not executable during inspection")', encoding='utf-8')
            align_synthetic_certificate(router / 'persistent/tls/certificate.pem',
                                        'Orion Router ' + (router / 'persistent/mdns-id').read_text().strip())
            security = item['hub'] / 'files/security'
            align_synthetic_certificate(security / 'hub-cert.pem', (security / 'hub-id').read_text().strip())
        self.environment = patch.dict(os.environ, self.lab.environment())
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def http_client(self):
        # Mount only the routes: importing the executable installer would install
        # process signal handlers and other unrelated desktop lifecycle behavior.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from installer.app.routes import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_http_inspection_is_read_only_and_never_claims_runtime_ready(self):
        item = self.lab.installations[0]
        before = snapshot(item['router'])
        response = self.http_client().post('/api/installation/router/inspect', json={
            'root': str(item['router']), 'distribution': 'legacy-source'})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual('closed-unverified', response.json()['status'])
        self.assertEqual(before, snapshot(item['router']))
        self.assertIsNone(Registry().read())

    def test_http_registration_records_pinned_identity_and_real_paths(self):
        item = self.lab.installations[0]
        before = snapshot(item['router'])
        client = self.http_client()
        response = client.post('/api/installation/router/register', json={'root': str(item['router'])})
        self.assertEqual(200, response.status_code, response.text)
        record = response.json()
        component = record['components'][0]
        self.assertEqual((item['router'] / 'persistent/mdns-id').read_text().strip(), component['router_uuid'])
        self.assertEqual(str(item['router'] / 'persistent'), component['data_path'])
        self.assertEqual(str(item['router'] / '.orion-lifecycle'), component['state_path'])
        self.assertEqual(64, len(component['tls_spki_sha256']))
        status = client.get('/api/installation')
        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual({'status': 'registered', 'record': record}, status.json())
        self.assertEqual(before, snapshot(item['router']))

    def test_http_second_router_conflict_preserves_existing_registry(self):
        first, second = self.lab.installations
        client = self.http_client()
        selected = client.post('/api/installation/router/register', json={'root': str(first['router'])})
        self.assertEqual(200, selected.status_code, selected.text)
        before = Registry().path.read_bytes()
        other_before = snapshot(second['router'])
        conflict = client.post('/api/installation/router/register', json={'root': str(second['router'])})
        self.assertEqual(409, conflict.status_code, conflict.text)
        self.assertIn('replacement', conflict.json()['detail'])
        self.assertEqual(before, Registry().path.read_bytes())
        self.assertEqual(other_before, snapshot(second['router']))

    def test_http_corrupt_inventory_is_conflict_without_legacy_fallback(self):
        store = Registry()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        corrupt = b'{incomplete registry'
        store.path.write_bytes(corrupt)
        self.assertTrue((self.lab.installations[0]['profile'] / 'Orion/router-installation.json').is_file())
        response = self.http_client().get('/api/installation')
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn('repair required', response.json()['detail'])
        self.assertEqual(corrupt, store.path.read_bytes())

    def test_direct_selection_is_idempotent_and_refuses_identity_or_router_replacement(self):
        store = Registry()
        first, other = self.lab.installations
        before = snapshot(first['router'])
        selected = register_router(store, first['router'])
        self.assertEqual(selected, register_router(store, first['router']))
        self.assertEqual(19443, selected['components'][0]['tls_port'])
        self.assertEqual(before, snapshot(first['router']))
        with self.assertRaisesRegex(RegistryError, 'replacement'):
            register_router(store, other['router'])
        self.assertEqual(selected, store.read())

    def test_import_both_preserves_ids_preferences_and_legacy_files(self):
        store = Registry()
        item = self.lab.installations[0]
        source = item['profile'] / 'Orion'
        before = snapshot(item['hub']), snapshot(item['router'])
        record = import_legacy(store, hub_manifest=source / 'hub-installation.json', router_manifest=source / 'router-installation.json')
        record['preferences']['automatic_updates'] = False
        store.write(record)
        self.assertEqual(record, import_legacy(store, hub_manifest=source / 'hub-installation.json', router_manifest=source / 'router-installation.json'))
        self.assertEqual(before, (snapshot(item['hub']), snapshot(item['router'])))

    def test_hub_bootstrap_uses_canonical_pin_and_never_falls_back_to_legacy(self):
        with patch.object(sys, 'path', [str(ROOT / 'services/hub/src'), *sys.path]):
            from orion.kernel.router_bootstrap import read_local_router_candidate
        store = Registry()
        item = self.lab.installations[0]
        record = register_router(store, item['router'])
        self.assertEqual(str(item['router']), read_local_router_candidate()['root'])
        # A changed cert has the same UUID but a different key. A legacy locator
        # remains valid on disk, and must not bypass the canonical pinned choice.
        identity = record['components'][0]['router_uuid']
        align_synthetic_certificate(item['router'] / 'persistent/tls/certificate.pem', 'Orion Router ' + identity)
        with self.assertRaisesRegex(ValueError, 'identity-mismatch'):
            read_local_router_candidate()
        record['components'] = []
        store.write(record)
        self.assertIsNone(read_local_router_candidate())

    def test_broken_registration_propagates_without_fallback(self):
        with patch('services.shared.router_installation.registered_router_root', side_effect=ValueError('repair required')):
            with self.assertRaisesRegex(ValueError, 'repair required'):
                native_services.router_root(None)


if __name__ == '__main__':
    unittest.main()
