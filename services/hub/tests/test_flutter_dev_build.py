import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from services.shared import flutter_dev_build as build


class FlutterDevBuildTests(unittest.TestCase):
    def checkout(self, root):
        app = Path(root) / 'main_app' / 'app'
        (app / 'lib').mkdir(parents=True)
        (app / 'lib' / 'main.dart').write_text('void main() {}')
        (app / 'pubspec.yaml').write_text('name: app')
        (app / 'build' / 'web').mkdir(parents=True)
        (app / 'build' / 'web' / 'index.html').write_text('<html>')
        return app

    def run_refresh(self, app, calls):
        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'')
        with mock.patch.object(build.shutil, 'which', return_value='flutter'), \
                mock.patch.object(build.subprocess, 'run', side_effect=fake_run):  # No windows_host: no OrionUi.exe step
            return build.refresh(app, 'python', lambda message: None)

    def test_packaged_client_without_sources_is_never_built(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'build' / 'web').mkdir(parents=True)
            calls = []
            self.assertEqual(self.run_refresh(Path(root), calls), [])
            self.assertEqual(calls, [])

    def test_builds_once_then_only_after_a_source_change(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.checkout(root)
            calls = []
            self.assertEqual(self.run_refresh(app, calls), ['Flutter web'])  # No record yet.
            self.assertEqual(self.run_refresh(app, calls), [])               # Unchanged.
            source = app / 'lib' / 'main.dart'
            source.write_text('void main() { print(1); }')
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
            self.assertEqual(self.run_refresh(app, calls), ['Flutter web'])
            self.assertEqual([argv[1:] for argv in calls], [['build', 'web'], ['build', 'web']])

    def test_failed_build_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.checkout(root)
            failed = subprocess.CompletedProcess([], 1, b'error')
            with mock.patch.object(build.shutil, 'which', return_value='flutter'), \
                    mock.patch.object(build.subprocess, 'run', return_value=failed):
                self.assertEqual(build.refresh(app, 'python', lambda message: None), [])
            self.assertTrue(build.needs_build(app, build.WEB_MANIFEST, build.web_inputs(app),
                                              app / 'build' / 'web' / 'index.html'))


if __name__ == '__main__':
    unittest.main()
