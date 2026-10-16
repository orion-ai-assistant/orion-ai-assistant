"""Actual host locks and processes, without starting a user installation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.shared.installation_registry import Registry, component_record, new_registry
from services.shared.content_migration import MigrationError, registry_revision
from services.shared.migration_host import StoppedInstallation, move_content, recover_content
from services.shared.process_lifecycle import Runtime

ROOT = Path(__file__).resolve().parents[2]


class MigrationHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='migration-host-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / 'Hub'
        self.code.mkdir()
        self.models = self.root / 'Models'
        self.models.mkdir()
        (self.models / 'sample.gguf').write_bytes(b'not a real model')
        self.registry = Registry(self.root / 'profile/install.json')
        record = new_registry(self.root, [component_record('hub', self.code, ownership='owned', management='local')])
        record['locations'] = {'models': str(self.models)}
        self.registry.write(record)

    def test_stopped_installation_moves_with_real_gate_and_keeps_source(self):
        destination = self.root / 'New Models'
        result = move_content(self.registry, 'models', destination,
                              expected_revision=registry_revision(self.registry.read()))
        self.assertEqual('completed', result['phase'])
        self.assertEqual(b'not a real model', (self.models / 'sample.gguf').read_bytes())
        self.assertEqual(b'not a real model', (destination / 'sample.gguf').read_bytes())

    def test_live_supervisor_is_refused_without_stop_or_registry_changes(self):
        before = self.registry.read()
        with Runtime(self.code, 'hub'):
            with self.assertRaises((MigrationError, RuntimeError)):
                with StoppedInstallation(self.registry):
                    self.fail('Live supervisor admitted')
        self.assertEqual(before, self.registry.read())

    def test_unmanaged_same_installation_process_is_refused(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=self.code)
        try:
            with self.assertRaisesRegex(MigrationError, 'uncoordinated'):
                with StoppedInstallation(self.registry):
                    self.fail('Unmanaged process admitted')
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_normal_native_start_operation_is_blocked_until_gate_releases(self):
        script = ('from services.shared.process_lifecycle import InstanceLock; from pathlib import Path; '
                  'import sys; '
                  'lock=InstanceLock(Path(sys.argv[1]), "operation", timeout=0); '
                  'lock.__enter__(); lock.__exit__()')
        command = [sys.executable, '-c', script, str(self.code / '.orion-lifecycle/hub')]
        with StoppedInstallation(self.registry):
            blocked = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
            self.assertNotEqual(0, blocked.returncode)
        released = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(0, released.returncode, released.stderr.decode(errors='replace'))

    def test_external_ownership_and_custom_state_fail_closed(self):
        for change in ({'ownership': 'external'}, {'state_path': str(self.root / 'external-state')}):
            record = self.registry.read()
            record['components'][0].update(change)
            self.registry.write(record)
            with self.assertRaises(MigrationError):
                with StoppedInstallation(self.registry):
                    self.fail('Unsupported ownership/state admitted')

    def test_local_http_move_checks_revision_and_live_ownership(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from installer.app import config, routes
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(config, 'installation_registry', return_value=self.registry), TestClient(app) as client:
            status = client.get('/api/installation/locations').json()
            self.assertEqual(str(self.models), status['locations']['models']['path'])
            request = {'name': 'models', 'destination': str(self.root / 'Moved Models'), 'expected_revision': 'stale'}
            self.assertEqual(409, client.post('/api/installation/locations/move', json=request).status_code)
            request['expected_revision'] = status['revision']
            with Runtime(self.code, 'hub'):
                self.assertEqual(409, client.post('/api/installation/locations/move', json=request).status_code)
            moved = client.post('/api/installation/locations/move', json=request)
            self.assertEqual(200, moved.status_code, moved.text)
            self.assertEqual('completed', moved.json()['phase'])

    def test_real_gate_releases_after_crash_and_recovers_without_nested_operation_lock(self):
        class Crash(BaseException):
            pass
        before = self.registry.read()
        def fault(phase, journal):
            if phase == 'verified':
                raise Crash()
        with patch('services.shared.content_migration.operation_lock', side_effect=AssertionError('nested lock')):
            with self.assertRaises(Crash):
                move_content(self.registry, 'models', self.root / 'Moved Models',
                             expected_revision=registry_revision(before), fault=fault)
            self.assertEqual(before, self.registry.read())
            journal = next((self.registry.path.parent / 'migrations').glob('*.json'))
            self.assertEqual('completed', recover_content(self.registry, journal)['phase'])


if __name__ == '__main__':
    unittest.main()
