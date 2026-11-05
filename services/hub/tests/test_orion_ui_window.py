"""Orion's own window (OrionUi.exe): started once, then only shown; run from a copy, never the Client folder."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.shared import flutter_launcher
from services.shared.flutter_launcher import _runnable_copy, open_ui, ui_host_exe


def client_with_ui(root, content=b'exe'):
    folder = Path(root) / 'client' / 'ui_host'
    folder.mkdir(parents=True)
    (folder / 'OrionUi.exe').write_bytes(content)
    (folder / 'WebView2Loader.dll').write_bytes(b'loader')
    return folder.parent


@unittest.skipUnless(os.name == 'nt', 'Windows window')
class OrionUiWindowTests(unittest.TestCase):
    def test_a_client_without_the_window_keeps_the_browser_window(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(ui_host_exe(root))
            self.assertFalse(open_ui('http://127.0.0.1:8090/', app_dir=root, ident='x', data_dir=Path(root) / 'd'))

    def test_installed_package_and_development_build_are_both_found(self):
        with tempfile.TemporaryDirectory() as root:
            client = client_with_ui(root)
            self.assertEqual(ui_host_exe(client), client / 'ui_host' / 'OrionUi.exe')
            dev = Path(root) / 'dev'
            (dev / 'build' / 'ui_host').mkdir(parents=True)
            (dev / 'build' / 'ui_host' / 'OrionUi.exe').write_bytes(b'exe')
            self.assertEqual(ui_host_exe(dev), dev / 'build' / 'ui_host' / 'OrionUi.exe')

    def test_it_runs_from_a_copy_named_by_content_and_old_copies_go(self):
        with tempfile.TemporaryDirectory() as root:
            client = client_with_ui(root)
            first = _runnable_copy(client / 'ui_host' / 'OrionUi.exe', Path(root) / 'ui-host')
            self.assertNotEqual(first.parent, client / 'ui_host')
            self.assertEqual(first.read_bytes(), b'exe')
            self.assertEqual((first.parent / 'WebView2Loader.dll').read_bytes(), b'loader')
            self.assertEqual(_runnable_copy(client / 'ui_host' / 'OrionUi.exe', Path(root) / 'ui-host'), first)
            (client / 'ui_host' / 'OrionUi.exe').write_bytes(b'new exe')  # an update
            second = _runnable_copy(client / 'ui_host' / 'OrionUi.exe', Path(root) / 'ui-host')
            self.assertNotEqual(second, first)
            self.assertFalse(first.parent.exists())

    def test_a_running_window_is_only_shown_never_started_again(self):
        with tempfile.TemporaryDirectory() as root:
            client = client_with_ui(root)
            with patch.object(flutter_launcher, 'ui_running', return_value=True), \
                    patch.object(flutter_launcher, 'ui_command', return_value=True) as command, \
                    patch('services.shared.orion_host.popen') as spawn:
                self.assertTrue(open_ui('http://127.0.0.1:8090/', app_dir=client, ident='id', data_dir=Path(root) / 'd'))
                self.assertTrue(open_ui('http://127.0.0.1:8090/', app_dir=client, ident='id', data_dir=Path(root) / 'd',
                                        hidden=True))
            command.assert_called_once_with('id', 'show')  # the hidden start leaves a running window as it is
            spawn.assert_not_called()

    def test_a_first_start_launches_the_copy_with_its_page_and_data_folder(self):
        with tempfile.TemporaryDirectory() as root:
            client = client_with_ui(root)
            with patch.object(flutter_launcher, 'ui_running', return_value=False), \
                    patch('services.shared.orion_host.popen') as spawn:
                self.assertTrue(open_ui('http://127.0.0.1:8090/', app_dir=client, ident='id',
                                        data_dir=Path(root) / 'ui-webview2', hidden=True))
            argv = spawn.call_args.args[0]
            self.assertEqual(Path(argv[0]).parent.parent, Path(root) / 'ui-host')
            self.assertEqual(argv[1:], ['--url', 'http://127.0.0.1:8090/', '--id', 'id', '--data',
                                        str(Path(root) / 'ui-webview2'), '--hidden'])
            self.assertEqual(spawn.call_args.kwargs['component_root'], client)

    def test_commands_reach_no_window_when_none_runs(self):
        self.assertFalse(flutter_launcher.ui_running('no-such-installation'))
        self.assertFalse(flutter_launcher.ui_command('no-such-installation', 'show'))


if __name__ == '__main__':
    unittest.main()
