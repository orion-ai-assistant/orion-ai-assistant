import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from services.shared.flutter_launcher import start_flutter, open_window


class FlutterLauncherTests(unittest.TestCase):
    def test_strict_app_window_never_falls_back_to_normal_browser_tab(self):
        with patch('services.shared.flutter_launcher.shutil.which', return_value=None), patch('services.shared.flutter_launcher.Path.is_file', return_value=False), patch('services.shared.flutter_launcher.webbrowser.open') as tab:
            self.assertFalse(open_window('http://localhost:8910/panel', allow_tab_fallback=False))
            tab.assert_not_called()

    def env(self, values):
        return lambda key, default=None, required=False: values.get(key, default)

    def test_disabled_ui_never_spawns(self):
        with patch('services.shared.flutter_launcher.subprocess.Popen') as spawn:
            self.assertIsNone(start_flutter('.', 'python', self.env({'ORION_FLUTTER_AUTOSTART': '0'}), set(), None, threading.Event()))
            spawn.assert_not_called()

    def test_service_port_collision_never_spawns(self):
        with self.assertRaises(ValueError):
            start_flutter('.', 'python', self.env({}), {8090}, None, threading.Event())

    def test_missing_build_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, 'flutter build web'):
                start_flutter('.', 'python', self.env({'ORION_FLUTTER_APP_DIR': directory}), set(), None, threading.Event())

    def test_occupied_ui_port_is_not_reused_or_killed(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            build = Path(directory) / 'build' / 'web'
            build.mkdir(parents=True)
            (build / 'index.html').write_text('Flutter')
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            config = self.env({'ORION_FLUTTER_APP_DIR': directory, 'ORION_FLUTTER_PORT': str(listener.getsockname()[1])})
            with patch('services.shared.flutter_launcher.subprocess.Popen') as spawn, self.assertRaisesRegex(OSError, 'already occupied'):
                start_flutter('.', 'python', config, set(), None, threading.Event())
            spawn.assert_not_called()

    def test_child_uses_configured_port_and_hub_address(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as probe:
            build = Path(directory) / 'build' / 'web'
            build.mkdir(parents=True)
            (build / 'index.html').write_text('Flutter')
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
            probe.close()
            config = self.env({'ORION_FLUTTER_APP_DIR': directory, 'ORION_FLUTTER_PORT': str(port), 'ORION_LOCAL_PORT': '9321'})
            with patch('services.shared.flutter_launcher.subprocess.Popen') as spawn, patch('services.shared.flutter_launcher.threading.Thread'):
                child = start_flutter('.', 'python', config, set(), None, threading.Event())
            self.assertIs(child, spawn.return_value)
            args, options = spawn.call_args
            self.assertEqual(args[0][-4:], ['--host', '127.0.0.1', '--port', str(port)])
            self.assertEqual(options['env']['ORION_UI_HUB_URL'], 'http://localhost:9321')
