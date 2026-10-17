"""Launcher policy and real Windows link tests in disposable directories."""
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.shared import launcher
from services.shared.installation_registry import Registry, component_record, new_registry

ROOT = Path(__file__).resolve().parents[2]
SHOW_WINDOW = launcher.show_window


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='launcher-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / 'Türkçe İıŞşĞğ 中文 Hub'
        (self.code / 'services/hub').mkdir(parents=True)
        (self.code / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        self.store = Registry(self.root / 'Orion/install.json')
        self.store.write(new_registry(self.root, [component_record('hub', self.code, ownership='owned', management='local')]))
        self.start = self.enterContext(patch('services.shared.native_services.start_hub'))
        self.window = self.enterContext(patch('services.shared.launcher.show_window'))
        self.tray = self.enterContext(patch('services.shared.launcher.spawn_tray'))
        self.tray_stop = self.enterContext(patch('services.shared.launcher.stop_tray'))

    def test_disabled_stale_startup_link_does_not_start_anything(self):
        self.assertEqual({'status': 'disabled'}, launcher.launch(self.store, startup=True))
        self.start.assert_not_called()
        self.window.assert_not_called()

    def test_manual_open_and_background_have_separate_window_policy(self):
        launcher.launch(self.store, background=True)
        self.window.assert_not_called()
        launcher.launch(self.store)
        self.window.assert_called_once()
        self.assertEqual(2, self.start.call_count)

    def test_startup_window_preference_and_explicit_stop(self):
        self.store.update(lambda r: {**r, 'preferences': {**r['preferences'], 'start_with_windows': True, 'show_client': False}})
        launcher.launch(self.store, startup=True)
        self.window.assert_not_called()
        launcher.launch(self.store)
        self.window.assert_called_once()
        with patch('services.shared.native_services.stop_hub') as stop:
            launcher.launch(self.store, stop=True)
            stop.assert_called_once_with(str(self.code))

    def test_startup_can_show_window_independently_of_enabled_services(self):
        self.store.update(lambda r: {**r, 'preferences': {**r['preferences'], 'start_with_windows': True, 'show_client': True}})
        launcher.launch(self.store, startup=True)
        self.start.assert_called_once()
        self.window.assert_called_once()

    def test_missing_disk_and_wrong_product_fail_before_start(self):
        (self.code / 'services/hub/pyproject.toml').write_text('[project]\nname="other"')
        with self.assertRaisesRegex(ValueError, 'expected Orion'):
            launcher.launch(self.store)
        missing = self.code / 'unavailable'
        self.store.update(lambda r: {**r, 'components': [component_record('hub', missing, ownership='owned', management='local')]})
        with self.assertRaises((ValueError, OSError)):
            launcher.launch(self.store)
        self.assertFalse(missing.exists())
        self.start.assert_not_called()

    def test_broken_registry_is_preserved(self):
        self.store.path.write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            launcher.launch(self.store)
        self.assertEqual(b'{broken', self.store.path.read_bytes())
        self.start.assert_not_called()

    def test_registered_lifecycle_identity_cannot_be_replaced(self):
        import uuid
        expected = str(uuid.uuid4())
        self.store.update(lambda r: {**r, 'components': [{**r['components'][0], 'lifecycle_ids': {'hub': expected}}]})
        identity = self.code / '.orion-lifecycle/hub/identity.json'
        identity.parent.mkdir(parents=True)
        identity.write_text(json.dumps({'v': 1, 'id': str(uuid.uuid4()), 'root': os.path.normcase(str(self.code)), 'component': 'hub'}))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            launcher.launch(self.store)
        self.start.assert_not_called()

    def test_connect_only_router_never_starts_or_stops(self):
        self.store.update(lambda r: {**r, 'components': r['components'] + [component_record('router', self.root / 'missing-router')]})
        with patch('services.shared.native_services.start_router') as router:
            launcher.launch(self.store, background=True)
            router.assert_not_called()

    def test_window_failure_keeps_started_services_and_registry(self):
        before = self.store.path.read_bytes()
        self.window.side_effect = RuntimeError('UI unavailable')
        with patch('services.shared.native_services.stop_hub') as stop, self.assertRaisesRegex(RuntimeError, 'UI unavailable'):
            launcher.launch(self.store)
        stop.assert_not_called()
        self.assertEqual(before, self.store.path.read_bytes())

    def test_window_requires_owned_live_server_and_verified_health(self):
        from services.shared.process_lifecycle import Runtime
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        script = self.code / 'ui-fixture.py'
        script.write_text('''import http.server, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'orion-flutter-web')
    def log_message(self, *args): pass
server = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler)
server.timeout = .1
while True: server.handle_request()
''', encoding='utf-8')
        with patch.dict(os.environ, ORION_FLUTTER_PORT=str(port)), patch('services.shared.flutter_launcher.open_window', return_value=True) as browser:
            with Runtime(self.code, 'hub') as runtime:
                with self.assertRaisesRegex(RuntimeError, 'Flutter UI unavailable'):
                    SHOW_WINDOW(self.store, self.store.read()['components'][0], timeout=0)
                browser.assert_not_called()
                child = runtime.launch('flutter', [sys.executable, str(script), str(port)], python=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                SHOW_WINDOW(self.store, self.store.read()['components'][0], timeout=15)
                browser.assert_called_once_with(f'http://127.0.0.1:{port}/', allow_tab_fallback=False,
                                                profile=self.store.path.parent / 'flutter-ui-profile', reuse_existing=True)
                self.assertIsNone(child.poll())

    def test_environment_uses_registered_client_and_restores_process(self):
        client = self.root / 'Client 中文'
        client.mkdir()
        self.store.update(lambda r: {**r, 'components': r['components'] + [component_record('client', client)]})
        previous = dict(os.environ)
        with launcher.launch_environment(self.store):
            self.assertEqual(str(client), os.environ['ORION_FLUTTER_APP_DIR'])
            self.assertEqual('0', os.environ['ORION_FLUTTER_OPEN_WINDOW'])
        self.assertEqual(previous, dict(os.environ))

    def test_preference_write_failure_leaves_startup_disabled(self):
        with patch('services.shared.windows_autostart.shortcut'), patch.object(self.store, 'update', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                launcher.preferences(self.store, start_with_windows=True, show_client=False, startup_directory=self.root)
        self.assertFalse(self.store.read()['preferences']['start_with_windows'])

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_real_isolated_startup_toggle_and_foreign_link_preservation(self):
        entry = launcher.install_entry(self.store, desktop=False)
        before = self.store.read()['components']
        launcher.preferences(self.store, start_with_windows=True, show_client=False, startup_directory=self.root)
        link = self.root / 'Orion.lnk'
        self.assertTrue(link.is_file())
        # Inspect actual COM output, including the Unicode path and startup argument.
        cmd = '$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:ORION_TEST_LINK); @($l.TargetPath,$l.Arguments,$l.Description)|ConvertTo-Json'
        out = subprocess.check_output(['powershell', '-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ' + cmd],
                                     env=dict(os.environ, ORION_TEST_LINK=str(link)), encoding='utf-8-sig')
        target, args, description = json.loads(out)
        self.assertIn(str(entry), args)
        self.assertIn('-Startup', args)
        self.assertTrue(target.lower().endswith('powershell.exe'))
        self.assertIn(self.store.read()['installation_id'], description)
        launcher.preferences(self.store, start_with_windows=False, show_client=True, startup_directory=self.root)
        self.assertFalse(link.exists())
        self.assertEqual(before, self.store.read()['components'])
        link.write_bytes(b'foreign shortcut, preserve')
        with self.assertRaisesRegex(RuntimeError, 'shortcut'):
            launcher.preferences(self.store, start_with_windows=True, show_client=True, startup_directory=self.root)
        self.assertEqual(b'foreign shortcut, preserve', link.read_bytes())
        self.assertFalse(self.store.read()['preferences']['start_with_windows'])

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_desktop_uses_same_entry_without_enabling_startup(self):
        desktop = self.root / 'isolated Desktop'
        desktop.mkdir()
        with patch('services.shared.windows_autostart.known_folder', return_value=desktop):
            entry = launcher.install_entry(self.store)
        command = '$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:ORION_TEST_LINK); $l.Arguments'
        args = subprocess.check_output(['powershell', '-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ' + command],
                                      env=dict(os.environ, ORION_TEST_LINK=str(desktop / 'Orion.lnk')), encoding='utf-8-sig')
        self.assertIn(str(entry), args)
        self.assertNotIn('-Startup', args)
        self.assertFalse(self.store.read()['preferences']['start_with_windows'])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell entry')
    def test_stale_entry_runs_without_hub_or_python_when_disabled(self):
        entry = launcher.install_entry(self.store, desktop=False)
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(entry), '-Startup'],
                                capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell entry')
    def test_missing_runtime_and_disk_have_local_repair_without_hub(self):
        entry = launcher.install_entry(self.store, desktop=False)
        command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(entry), '-NonInteractive']
        result = subprocess.run(command, capture_output=True, timeout=15)
        self.assertEqual(1, result.returncode)
        self.assertIn(b'Python runtime is missing', result.stdout)
        self.assertTrue((entry.parent / 'launcher.log').is_file())
        missing = self.root / 'absent-disk/Hub'
        self.store.update(lambda r: {**r, 'components': [component_record('hub', missing, ownership='owned', management='local')]})
        result = subprocess.run(command, capture_output=True, timeout=15)
        self.assertEqual(1, result.returncode)
        self.assertIn(b'Registered disk or Hub folder is missing', result.stdout)
        self.assertFalse(missing.exists())
        result = subprocess.run(command + ['-Repair'], capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode)
        self.assertIn(b'available locally', result.stdout)

    def test_unrelated_local_router_failure_preflights_before_hub(self):
        self.store.update(lambda r: {**r, 'components': r['components'] + [component_record(
            'router', self.root / 'absent-router', ownership='owned', management='local')]})
        with self.assertRaises((ValueError, OSError)):
            launcher.launch(self.store, background=True)
        self.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
