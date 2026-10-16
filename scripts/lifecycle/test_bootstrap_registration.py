"""Successful bootstrap registration preserves existing inventory and secrets."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.shared import bootstrap_registration as bootstrap
from services.shared.installation_registry import Registry, RegistryError, component_record, new_registry

ROOT = Path(__file__).resolve().parents[2]


class BootstrapRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='bootstrap-registration-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.code = self.root / 'main/Hub/source'
        project = self.code / 'services/hub/pyproject.toml'
        project.parent.mkdir(parents=True)
        project.write_text('[project]\nname="orion-hub"\n', encoding='utf-8')
        self.models = self.root / 'main/Models'
        self.store = Registry(self.root / 'registry/install.json')
        self.env = self.code / 'services/.env.global.local'

    def register(self):
        return bootstrap.register_bootstrap(self.code, root=self.root / 'main', models_path=self.models, registry_path=self.store.path)

    def test_new_bootstrap_records_defaults_and_preserves_env_secrets(self):
        self.env.write_text('# keep comment\nPOSTGRES_PASSWORD=synthetic-secret\nORION_MODELS_PATH=old\nHUB_PORT=12345\n', encoding='utf-8')
        record = self.register()
        self.assertEqual(str(self.root / 'main'), record['root'])
        self.assertEqual(str(self.models), record['locations']['models'])
        self.assertEqual(str(self.code), record['components'][0]['code_path'])
        text = self.env.read_text(encoding='utf-8')
        self.assertIn('POSTGRES_PASSWORD=synthetic-secret\n', text)
        self.assertIn('# keep comment\n', text)
        self.assertIn('HUB_PORT=12345\n', text)
        self.assertEqual(1, text.count('ORION_MODELS_PATH='))
        self.assertIn('ORION_MODELS_PATH=' + str(self.models), text)
        self.assertIn('ORION_INSTALLATION_REGISTRY=' + str(self.store.path), text)

    def test_repeat_registration_preserves_ids_preferences_and_disabled_updates(self):
        record = self.register()
        record['preferences']['automatic_updates'] = False
        self.store.write(record)
        self.assertEqual(record, self.register())
        self.assertEqual(record, self.store.read())

    def test_existing_legacy_layout_does_not_gain_central_models(self):
        record = new_registry(self.root / 'main', [component_record('hub', self.code)])
        self.store.write(record)
        self.assertEqual(record, self.register())
        self.assertNotIn('ORION_MODELS_PATH=', self.env.read_text(encoding='utf-8'))

    def test_existing_router_only_inventory_can_add_hub_without_replacing_router(self):
        router = component_record('router', self.root / 'standalone-router')
        existing = new_registry(self.root / 'main', [router])
        existing['preferences']['automatic_updates'] = False
        self.store.write(existing)
        record = self.register()
        self.assertEqual(existing['installation_id'], record['installation_id'])
        self.assertEqual(existing['preferences'], record['preferences'])
        self.assertEqual(router, record['components'][0])
        self.assertEqual('hub', record['components'][1]['kind'])

    def test_failed_env_write_does_not_commit_registry(self):
        self.env.write_text('POSTGRES_PASSWORD=synthetic-secret\n', encoding='utf-8')
        before = self.env.read_bytes()
        with patch.object(bootstrap, '_atomic_write', side_effect=PermissionError('Injected env failure')):
            with self.assertRaises((OSError, RegistryError)):
                self.register()
        self.assertIsNone(self.store.read())
        self.assertEqual(before, self.env.read_bytes())

    def test_wrong_product_and_conflicting_model_target_leave_registry_untouched(self):
        project = self.code / 'services/hub/pyproject.toml'
        project.write_text('[project]\nname="wrong-product"\n', encoding='utf-8')
        with self.assertRaises(RegistryError):
            self.register()
        self.assertIsNone(self.store.read())
        project.write_text('[project]\nname="orion-hub"\n', encoding='utf-8')
        self.register()
        before = self.store.path.read_bytes()
        with self.assertRaises(RegistryError):
            bootstrap.register_bootstrap(self.code, root=self.root / 'main', models_path=self.root / 'different', registry_path=self.store.path)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_corrupt_inventory_never_replaced_by_bootstrap(self):
        self.store.path.parent.mkdir()
        corrupt = b'{incomplete'
        self.store.path.write_bytes(corrupt)
        with self.assertRaises(RegistryError):
            self.register()
        self.assertEqual(corrupt, self.store.path.read_bytes())
        self.assertFalse(self.env.exists())

    def test_registry_failure_restores_only_our_configuration_write(self):
        for original in (None, b'POSTGRES_PASSWORD=synthetic-secret\n'):
            with self.subTest(existing_configuration=original is not None):
                if original is None:
                    self.env.unlink(missing_ok=True)
                else:
                    self.env.write_bytes(original)
                with patch.object(Registry, 'update', side_effect=PermissionError('Injected registry failure')):
                    with self.assertRaises(PermissionError):
                        self.register()
                self.assertIsNone(self.store.read())
                self.assertEqual(original, self.env.read_bytes() if self.env.exists() else None)


if __name__ == '__main__':
    unittest.main()
