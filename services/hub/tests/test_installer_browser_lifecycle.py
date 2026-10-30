import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from installer.app import config, services
from services.shared.browser_shutdown import stop_hub_browsers


class InstallerBrowserLifecycleTests(unittest.TestCase):
    def test_browser_manifests_do_not_become_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, value in {
                'hub/manifest.json': {'id': 'orion-hub', 'category': 'hub', 'name': 'Hub'},
                'hub/data/browser-profiles/user/component/manifest.json': {'name': 'Brave component'},
                'hub/.venv/package/manifest.json': {'id': 'fake', 'category': 'fake'},
                'unknown/manifest.json': {'name': 'not a service'},
                'invalid/manifest.json': [],
                'tts/nested/manifest.json': {'id': 'tts', 'category': 'tts', 'name': 'TTS'},
            }.items():
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps(value))
            with patch.object(config, 'SERVICES_DIR', str(root)), patch.object(config, '_load_global_env', return_value={}), \
                    patch.dict(os.environ, {'ORION_INSTALL_MODE': 'local'}), \
                    patch.object(services.subprocess, 'run', return_value=Mock(stdout='')), \
                    patch.object(services.docker_utils, 'get_running_containers', return_value={}),                     patch('services.shared.native_services.component_status', return_value='unverified'):
                # The real checkout's Hub runtime record (if it runs from here) must not leak in.
                self.assertEqual({d['id'] for d in services.get_services()}, {'orion-hub', 'tts'})
                self.assertEqual(config.find_manifest('missing'), (None, None))

    def test_stop_delegates_to_verified_hub_supervisor(self):
        with patch.dict(os.environ, {'ORION_INSTALL_MODE': 'local'}), \
                patch.object(services, '_get_context', return_value=({'id': 'orion-hub'}, '/test/hub', {})), \
                patch('services.shared.native_services.stop_hub', return_value=True) as stop:
            self.assertTrue(services.stop_service('orion-hub'))
            stop.assert_called_once_with(config.PROJECT_ROOT)

    def test_helper_marks_stopping_and_runs_only_hub_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            hub = Path(temporary) / 'services/hub'
            root = hub / 'data/browser-profiles'
            root.mkdir(parents=True)
            python = hub / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            with patch('services.shared.browser_shutdown.subprocess.run', return_value=Mock(returncode=0)) as run:
                self.assertTrue(stop_hub_browsers(hub))
            self.assertTrue((root / '.stopping').exists())
            self.assertEqual(run.call_args.args[0], [str(python.resolve()), '-m', 'orion.browser.shutdown'])
