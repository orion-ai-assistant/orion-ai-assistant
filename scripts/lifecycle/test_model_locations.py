"""Native model consumers follow only their own registered installation."""
from contextlib import ExitStack
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.shared.installation_registry import Registry, RegistryError, component_record, new_registry
from services.shared.model_locations import models_directory

ROOT = Path(__file__).resolve().parents[2]


class ModelLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='model-locations-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.code = self.root / 'installed'
        self.service = self.code / 'services/llm/model-api'
        self.service.mkdir(parents=True)
        self.store = Registry(self.root / 'registry/install.json')
        self.environment = patch.dict(os.environ, ORION_INSTALLATION_REGISTRY=str(self.store.path), ORION_INSTALL_MODE='local', ORION_MODELS_PATH=str(self.root / 'unrelated-env-models'))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manifest = {'models_path': 'legacy-models'}

    def record(self, *, central=True, development=False):
        record = new_registry(self.root / 'main', [component_record('hub', self.code, distribution='development' if development else 'legacy-source')])
        if central:
            record['locations'] = {'models': str(self.root / 'central-models')}
        self.store.write(record)
        return record

    def test_registered_models_root_used_for_installed_service(self):
        self.record()
        self.assertEqual(self.root / 'central-models' / 'model-api', models_directory('model-api', self.service, self.manifest, mode='local'))

    def test_legacy_import_does_not_relocate_existing_models(self):
        self.record(central=False)
        self.assertEqual(self.service / 'legacy-models', models_directory('model-api', self.service, self.manifest, mode='local'))

    def test_unregistered_development_tree_ignores_installed_models_and_env(self):
        self.record()
        service = self.root / 'development/services/model-api'
        self.assertEqual(service / 'legacy-models', models_directory('model-api', service, self.manifest, mode='local'))

    def test_registered_development_tree_does_not_share_installed_model_pool(self):
        record = self.record()
        development = self.root / 'development'
        record['components'].append(component_record('hub', development, distribution='development'))
        self.store.write(record)
        service = development / 'services/model-api'
        self.assertEqual(service / 'legacy-models', models_directory('model-api', service, self.manifest, mode='local'))

    def test_docker_retains_manifest_path_and_ignores_corrupt_native_inventory(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.service / 'legacy-models', models_directory('model-api', self.service, self.manifest, mode='docker'))

    def test_corrupt_local_inventory_never_falls_back_to_env_models(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(RegistryError):
            models_directory('model-api', self.service, self.manifest, mode='local')

    def test_explicit_registry_setting_and_location_change_are_followed(self):
        record = self.record()
        settings = {'ORION_INSTALLATION_REGISTRY': str(self.store.path)}
        with patch.dict(os.environ, ORION_INSTALLATION_REGISTRY=str(self.root / 'absent.json')):
            self.assertEqual(self.root / 'central-models/model-api', models_directory('model-api', self.service, self.manifest, mode='local', settings=settings))
            record['locations']['models'] = str(self.root / 'migrated-models')
            self.store.write(record)
            self.assertEqual(self.root / 'migrated-models/model-api', models_directory('model-api', self.service, self.manifest, mode='local', settings=settings))

    def test_no_inventory_explicit_models_root_is_scoped_by_service_id(self):
        explicit = self.root / 'models'
        self.assertEqual(explicit / 'model-api', models_directory('model-api', self.service, self.manifest, mode='local', settings={'ORION_MODELS_PATH': str(explicit)}))
        for identity in ('../escape', 'model/api', '', 'service:stream'):
            with self.subTest(identity=identity), self.assertRaises(RegistryError):
                models_directory(identity, self.service, self.manifest, mode='local')

    def test_installer_download_list_and_delete_use_one_registered_root(self):
        from installer.app import config
        from installer.app.core import models
        self.record()
        payload = b'synthetic model payload; never loaded'
        model = {'id': 'fixture', 'name': 'Synthetic model', 'file_name': 'fixture.gguf',
                 'download_url': 'https://invalid.example/fixture.gguf',
                 'sha256': {'fixture.gguf': hashlib.sha256(payload).hexdigest()}}
        manifest = {**self.manifest, 'models_catalog': [model]}
        settings = {'ORION_INSTALL_MODE': 'local', 'ORION_INSTALLATION_REGISTRY': str(self.store.path)}
        with ExitStack() as stack:
            stack.enter_context(patch.object(config, '_load_global_env', return_value=settings))
            stack.enter_context(patch.object(config, 'find_manifest', return_value=(manifest, str(self.service / 'manifest.json'))))
            stack.enter_context(patch.object(config, 'SHOULD_EXIT', False))
            stack.enter_context(patch.object(models, '_remote_size', return_value=len(payload)))
            request = stack.enter_context(patch.object(models.urllib.request, 'urlopen', return_value=io.BytesIO(payload)))
            stack.enter_context(patch.object(models.i18n, 't', side_effect=lambda key, *args: key))
            expected = self.root / 'central-models/model-api/fixture.gguf'
            models.run_model_download('model-api', str(self.service), model)
            request.assert_called_once()
            self.assertEqual(payload, expected.read_bytes())
            entries = models.check_models('model-api')
            entry = next(item for item in entries if item['id'] == 'fixture')
            self.assertTrue(entry['is_installed'])
            self.assertEqual(expected, Path(entry['path']))
            self.assertEqual('success', models.delete_model('model-api', 'fixture')['status'])
            self.assertFalse(expected.exists())
            self.assertFalse((self.service / 'legacy-models').exists())


if __name__ == '__main__':
    unittest.main()
