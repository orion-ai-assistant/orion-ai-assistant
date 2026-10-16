"""Bootstrap contract tests: synthetic destinations, no clone/setup executions."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import bootstrap_installation as bootstrap
import generate_bootstrap_wrappers as generator


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='orion-bootstrap-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        if os.name == 'nt' and os.environ.get('ORION_BOOTSTRAP_PRIVATE_FIXTURE') == '1':
            # Only this newly created synthetic fixture gets a private DACL.
            # Never change the ACL of a user-selected installation directory.
            policy = bootstrap.paths_api()
            sid = policy['_windows_acl'](self.base)['current_sid']
            executable = Path(os.environ['SystemRoot']) / 'System32/icacls.exe'
            subprocess.run([str(executable), str(self.base), '/inheritance:r', '/grant:r', '*' + sid + ':(OI)(CI)F'], check=True, capture_output=True)
        else:
            # The sandbox temp grants its AppContainer write access. Unit tests
            # stub private policy; the standalone integration retains real ACLs.
            self.enterContext(patch.dict(bootstrap.paths_api(), {'validate_private_directory': lambda path: path}))
        self.registry = self.base / 'registry/install.json'
        self.api = runpy.run_path(str(ROOT / 'services/shared/installation_registry.py'))

    def plan(self, root=None, **kwargs):
        return bootstrap.plan_installation(str(root or self.base / 'Orion Ã–zel'), registry_path=str(self.registry), registry_api=self.api, **kwargs)

    def project(self, code):
        for relative in ('manager.py', 'installer/pyproject.toml', 'services/hub/pyproject.toml', 'services/shared/installation_registry.py'):
            path = code / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('[project]\nname = "orion-hub"\n' if relative.endswith('pyproject.toml') else '# sentinel\n', encoding='utf-8')

    def record(self, root, code=None, **kwargs):
        components = [self.api['component_record']('hub', code)] if code else []
        value = self.api['new_registry'](str(root), components)
        value.update(kwargs)
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps(value), encoding='utf-8')
        return value

    def test_custom_unicode_and_separate_models_do_not_create_targets(self):
        plan = self.plan(models_path=str(self.base / 'BÃ¼yÃ¼k Modeller'))
        self.assertFalse(plan['reuse'])
        self.assertEqual(Path(plan['code_path']).name, 'Hub')
        self.assertFalse(Path(plan['root']).exists())
        self.assertFalse(Path(plan['models_path']).exists())

    def test_default_root_is_selected_before_download(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.base), 'XDG_DATA_HOME': str(self.base)}):
            value = bootstrap.plan_installation(registry_path=str(self.registry), registry_api=self.api)
        self.assertEqual(value['root'], str(self.base / 'Orion'))

    def test_registered_source_and_models_are_authoritative_and_unchanged(self):
        code = self.base / 'legacy source'
        self.project(code)
        root = self.base / 'registered'
        models = self.base / 'models'
        self.record(root, code, locations={'models': str(models)})
        before = {str(item): item.read_bytes() for item in self.base.rglob('*') if item.is_file()}
        result = bootstrap.plan_installation(registry_path=str(self.registry), registry_api=self.api)
        self.assertTrue(result['reuse'])
        self.assertEqual(result['code_path'], str(code))
        self.assertEqual(result['models_path'], str(models))
        self.assertEqual(before, {str(item): item.read_bytes() for item in self.base.rglob('*') if item.is_file()})
        with self.assertRaisesRegex(ValueError, 'migration'):
            self.plan()
        with self.assertRaisesRegex(ValueError, 'migration'):
            bootstrap.plan_installation(models_path=str(self.base / 'other'), registry_path=str(self.registry), registry_api=self.api)

    def test_corrupt_registry_or_missing_registered_project_fails_closed(self):
        self.registry.parent.mkdir()
        self.registry.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.plan()
        self.record(self.base, self.base / 'missing')
        with self.assertRaisesRegex(ValueError, 'missing'):
            bootstrap.plan_installation(registry_path=str(self.registry), registry_api=self.api)

    def test_occupied_unregistered_target_preserves_content(self):
        code = self.base / 'root/Hub'
        code.mkdir(parents=True)
        sentinel = code / 'user.txt'
        sentinel.write_bytes(b'user content')
        with self.assertRaisesRegex(ValueError, 'occupied'):
            self.plan(self.base / 'root')
        self.assertEqual(sentinel.read_bytes(), b'user content')

    def test_relative_nested_models_and_full_disk_rejected(self):
        with self.assertRaisesRegex(ValueError, 'absolute'):
            self.plan(Path('relative'))
        with self.assertRaisesRegex(ValueError, 'separate'):
            self.plan(models_path=str(self.base / 'Orion Ã–zel/Hub/models'))
        with patch.object(bootstrap.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10, 9, 1)):
            with self.assertRaisesRegex(ValueError, 'Insufficient'):
                self.plan()

    def test_write_failure_prevents_subprocess(self):
        with patch.object(bootstrap.tempfile, 'TemporaryFile', side_effect=PermissionError('write denied')), patch.object(bootstrap, 'run_command') as child:
            result = bootstrap.main(['--root', str(self.base / 'root'), '--registry-path', str(self.registry), '--non-interactive'], self.api)
        self.assertEqual(result, 1)
        child.assert_not_called()

    def test_existing_source_has_no_git_operations_and_setup_error_stops_launch(self):
        code = self.base / 'source'
        self.project(code)
        self.record(self.base / 'root', code)
        with patch.object(bootstrap, 'run_command', side_effect=subprocess.CalledProcessError(1, 'fixture setup')) as child:
            result = bootstrap.main(['--registry-path', str(self.registry), '--non-interactive'], self.api)
        self.assertEqual(result, 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(child.call_args.args[0][2], 'setup')

    def test_clone_failure_prevents_setup_and_does_not_delete_partial_target(self):
        with patch.object(bootstrap, 'run_command', side_effect=subprocess.CalledProcessError(1, 'fixture clone')) as child:
            result = bootstrap.main(['--root', str(self.base / 'fresh'), '--registry-path', str(self.registry), '--non-interactive'], self.api)
        self.assertEqual(result, 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(child.call_args.args[0][:2], ['git', 'clone'])

    def test_generated_sources_are_current(self):
        for name, source in generator.generated().items():
            self.assertEqual((ROOT / name).read_text(encoding='utf-8'), source, name)

    def test_legacy_locator_blocks_duplicate_install_before_any_command(self):
        self.registry.parent.mkdir(parents=True)
        locator = self.registry.parent / 'router-installation.json'
        locator.write_bytes(b'{"legacy":"must not be replaced"}')
        with patch.object(bootstrap, 'run_command') as child:
            result = bootstrap.main(['--root', str(self.base / 'fresh'), '--registry-path',
                                     str(self.registry), '--non-interactive'], self.api)
        self.assertEqual(result, 1)
        child.assert_not_called()
        self.assertEqual(locator.read_bytes(), b'{"legacy":"must not be replaced"}')
        self.assertFalse((self.base / 'fresh').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows wrapper integration')
    def test_powershell_embedded_plan_works_without_companion_files(self):
        if os.environ.get('ORION_BOOTSTRAP_PRIVATE_FIXTURE') != '1':
            self.skipTest('Set ORION_BOOTSTRAP_PRIVATE_FIXTURE=1 outside AppContainer for real private fixture integration')
        wrapper = self.base / 'standalone.ps1'
        wrapper.write_bytes((ROOT / 'install.ps1').read_bytes())
        result = subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-File', str(wrapper), '-Root', str(self.base / 'Ã–zel root'), '-RegistryPath', str(self.registry), '-PlanOnly', '-NonInteractive'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['root'], str(self.base / 'Ã–zel root'))
        self.assertFalse(Path(plan['root']).exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_junction_ancestor_rejected(self):
        target = self.base / 'target'
        target.mkdir()
        junction = self.base / 'link'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
        if result.returncode:
            self.skipTest('Sandbox cannot create junction: ' + result.stderr.decode(errors='replace').strip())
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaisesRegex(ValueError, 'reparse|junction'):
            self.plan(junction / 'root')

    @unittest.skipUnless(os.name == 'nt', 'Git Bash wrapper integration on Windows')
    def test_bash_embedded_plan_works_without_companion_files(self):
        if os.environ.get('ORION_BOOTSTRAP_PRIVATE_FIXTURE') != '1':
            self.skipTest('Real Git Bash integration requires private fixtures outside AppContainer')
        executable = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
        if not executable.is_file():
            self.skipTest('Git Bash is unavailable')
        wrapper = self.base / 'standalone.sh'
        wrapper.write_bytes((ROOT / 'install.sh').read_bytes())
        result = subprocess.run([str(executable), str(wrapper), 'local', '--root', str(self.base / 'Bash root'), '--registry-path', str(self.registry), '--plan-only', '--non-interactive'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['root'], str(self.base / 'Bash root'))


if __name__ == '__main__':
    unittest.main()
