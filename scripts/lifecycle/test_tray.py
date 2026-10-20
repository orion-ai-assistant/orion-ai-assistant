"""Tray icon: launcher supervision (spawn once, Hub installs only, clean exit) and script syntax."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from services.shared import launcher
from services.shared.installation_registry import Registry, component_record, new_registry

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'services/shared/tray.ps1'
UI = ROOT / 'services/shared/orion_ui.ps1'


def process(*command, name='powershell.exe'):
    item = MagicMock()
    item.info = {'name': name, 'cmdline': list(command)}
    return item


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='tray-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / 'Hub'
        (self.code / 'services/hub').mkdir(parents=True)
        (self.code / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        self.store = Registry(self.root / 'Orion/install.json')
        self.store.write(new_registry(self.root, [component_record('hub', self.code, ownership='owned', management='local')]))
        self.script = self.store.path.parent / 'bootstrap' / launcher.TRAY_SCRIPT

    def test_script_is_valid_powershell_with_the_required_menu(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertTrue(text.isascii(), 'Windows PowerShell 5.1 reads BOM-less files as ANSI')
        self.assertNotIn("Orion'u kald", text)  # Two entries: the title (open) and Quit.
        for needle in ('NotifyIcon', "Add_Click({ Open-Orion })", '0x00C7', "'-Stop'", 'Local\\Orion-Tray-', 'tray.exit', 'Visible = $false',
                       'Get-OrionIcon', 'Orion-Ui.ps1', 'WindowsShutDown', 'Request-SupervisorStop'):
            self.assertIn(needle, text)
        self.assertTrue(UI.read_text(encoding='utf-8').isascii())
        powershell = shutil.which('powershell')
        if os.name != 'nt' or not powershell:
            self.skipTest('Windows PowerShell required')
        for script in (SCRIPT, UI):
            check = ('$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('
                     f"'{script}',[ref]$null,[ref]$e);if($e.Count){{$e|%{{$_.Message}};exit 1}}")
            result = subprocess.run([powershell, '-NoProfile', '-Command', check], capture_output=True, timeout=60)
            self.assertEqual(0, result.returncode, result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_spawn_installs_script_and_starts_exactly_one_hidden_icon(self):
        popen = MagicMock()
        with patch.object(launcher, 'tray_processes', return_value=[]):
            self.assertTrue(launcher.spawn_tray(self.store, popen=popen))
        self.assertEqual(SCRIPT.read_bytes(), self.script.read_bytes())
        self.assertEqual(UI.read_bytes(), (self.script.parent / 'Orion-Ui.ps1').read_bytes())  # Dot-sourced by it.
        command = popen.call_args.args[0]
        self.assertEqual(['-File', str(self.script), '-Registry', str(self.store.path)], command[-4:])
        self.assertIn('Hidden', command)
        with patch.object(launcher, 'tray_processes', return_value=[object()]):
            self.assertFalse(launcher.spawn_tray(self.store, popen=popen))
        popen.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_only_this_installations_tray_script_is_recognised(self):
        mine = process('powershell.exe', '-File', str(self.script), '-Registry', 'x')
        quoted = process('powershell.exe', '-File', '"' + str(self.script) + '"')
        other = process('powershell.exe', '-File', str(self.root / 'other' / launcher.TRAY_SCRIPT))
        chrome = process('chrome.exe', str(self.script), name='chrome.exe')
        with patch('psutil.process_iter', return_value=[mine, quoted, other, chrome]):
            self.assertEqual([mine, quoted], launcher.tray_processes(self.store))

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_stop_asks_politely_then_terminates_stragglers_and_leaves_no_marker(self):
        stuck, gone = MagicMock(), MagicMock()
        with patch.object(launcher, 'tray_processes', return_value=[stuck, gone]), \
                patch('psutil.wait_procs', side_effect=[([gone], [stuck]), ([stuck], [])]) as wait:
            (self.script.parent).mkdir(parents=True)
            self.assertEqual(2, launcher.stop_tray(self.store))
        stuck.terminate.assert_called_once()
        gone.terminate.assert_not_called()
        self.assertEqual(2, wait.call_count)
        self.assertFalse((self.script.parent / 'tray.exit').exists())

    def test_stop_without_a_tray_does_nothing(self):
        with patch.object(launcher, 'tray_processes', return_value=[]):
            self.assertEqual(0, launcher.stop_tray(self.store))

    def test_hub_install_gets_a_tray_and_explicit_stop_removes_it(self):
        with patch('services.shared.native_services.start_hub'), patch('services.shared.native_services.stop_hub'), \
                patch.object(launcher, 'show_window'), patch.object(launcher, 'spawn_tray') as spawn, \
                patch.object(launcher, 'stop_tray') as stop, patch.object(launcher, 'close_window') as close:
            launcher.launch(self.store, background=True)
            spawn.assert_called_once_with(self.store)
            launcher.launch(self.store, stop=True)
            stop.assert_called_once_with(self.store)
            close.assert_called_once()
            # "Update now" restarts services but keeps the app window.
            launcher.launch(self.store, stop=True, keep_window=True)
            close.assert_called_once()
            self.assertEqual(2, stop.call_count)

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_the_resident_orion_exe_replaces_the_powershell_icon(self):
        popen = MagicMock()
        with patch('services.shared.orion_host.find', return_value=MagicMock(pid=1)), \
                patch.object(launcher, 'stop_tray') as stop:
            self.assertFalse(launcher.spawn_tray(self.store, popen=popen))
        stop.assert_called_once_with(self.store)  # An icon an earlier version started is closed.
        popen.assert_not_called()

    def test_stop_ends_the_resident_orion_exe_but_an_updates_stop_keeps_it(self):
        with patch('services.shared.native_services.stop_hub'), patch.object(launcher, 'stop_tray'), \
                patch.object(launcher, 'close_window'), patch.object(launcher, 'request_watcher_exit'), \
                patch('services.shared.orion_host.release') as release:
            launcher.launch(self.store, stop=True, keep_window=True, apply_owner=True)
            release.assert_not_called()  # "Update now" restarts the services under it.
            launcher.launch(self.store, stop=True)
            release.assert_called_once_with(self.store)

    def test_failing_tray_never_breaks_startup(self):
        with patch('services.shared.native_services.start_hub'), patch.object(launcher, 'show_window'), \
                patch.object(launcher, 'spawn_tray', side_effect=OSError('no powershell')):
            self.assertEqual({'status': 'ready'}, launcher.launch(self.store, background=True))

    def test_router_only_install_has_no_tray(self):
        router = self.root / 'Router'
        router.mkdir()
        (router / 'cli.py').write_text('')
        (router / 'pyproject.toml').write_text('[project]\nname="orion-router"', encoding='utf-8')
        self.store.write(new_registry(self.root, [component_record('router', router, ownership='owned', management='local')]))
        with patch('services.shared.native_services.start_router'), patch.object(launcher, 'managed_components',
                                                                                  wraps=launcher.managed_components), \
                patch('services.shared.flutter_launcher.open_window'), patch.object(launcher, 'spawn_tray') as spawn:
            launcher.launch(self.store, background=True)
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
