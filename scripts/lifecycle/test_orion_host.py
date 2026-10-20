"""The resident Orion.exe: one root process per installation for every Orion process.

Real Orion.exe (built with the Windows .NET Framework compiler) in a temporary installation folder; its
Orion.ps1 is a stand-in that records how it was run. Supervisors started through process_lifecycle become the
host's children inside its job, a second start is handed to the running host, Quit/the launcher's stop end it,
ending it ends everything under it, and a Windows sign-out stops the supervisors gracefully."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/release'))

from services.shared import orion_host, process_lifecycle as lifecycle  # noqa: E402
from services.shared.installation_registry import Registry, component_record, new_registry  # noqa: E402

PROBE = Path(__file__).with_name('probe_supervisor.py')
ENTRY = r'''param([switch]$Startup, [switch]$Background, [switch]$Stop)
$line = "$(if ($Stop) { 'stop' } else { 'start' }) $Startup $Background $env:ORION_HOST_PID"
$line | Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.txt') -Encoding ascii
$state = Join-Path '{state}' 'processes.json'
if ($Stop) { '{"status":"stopped"}' | Set-Content -LiteralPath $state -Encoding ascii; exit 0 }
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'nothing'))) {
    '{"status":"ready","run_id":"{run}"}' | Set-Content -LiteralPath $state -Encoding ascii
}
'''


def wait_until(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('condition not met in time')


@unittest.skipUnless(os.name == 'nt', 'Windows host')
class OrionHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build_launcher import build_launcher
        cls.build = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='host-exe-')
        cls.exe_source = build_launcher(Path(cls.build.name) / 'Orion.exe', '0.1.1')

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='host-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.code = self.root / 'Hub'
        (self.code / 'services/hub').mkdir(parents=True)
        (self.code / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        (self.code / 'child.py').write_text(
            'import time\nfrom pathlib import Path\n'
            'try:\n    while True: time.sleep(.05)\n'
            'finally:\n    Path("graceful-marker").touch()\n', encoding='utf-8')
        self.store = Registry(self.root / 'Orion/install.json')
        self.store.write(new_registry(self.root, [component_record('hub', self.code, ownership='owned', management='local')]))
        self.state = lifecycle.state_dir(self.code, 'hub')
        self.state.mkdir(parents=True, exist_ok=True)
        self.bootstrap = self.store.path.parent / 'bootstrap'
        self.bootstrap.mkdir()
        self.exe = self.bootstrap / 'Orion.exe'
        self.exe.write_bytes(self.exe_source.read_bytes())
        (self.bootstrap / 'Orion.ps1').write_text(
            ENTRY.replace('{state}', str(self.state)).replace('{run}', '00000000-0000-0000-0000-000000000001'),
            encoding='ascii')
        self.addCleanup(self.end_host)

    def end_host(self):
        host = orion_host.find(self.store)
        if host is not None:
            host.kill()
            host.wait(10)

    def calls(self):
        path = self.bootstrap / 'calls.txt'
        return path.read_text(encoding='ascii').split('\n')[:-1] if path.exists() else []

    def start_host(self, *switches):
        subprocess.Popen([str(self.exe), *switches])
        host = wait_until(lambda: orion_host.find(self.store))
        wait_until(lambda: self.calls())
        return host

    def test_processes_start_under_the_host_inside_its_job_and_end_with_it(self):
        host = self.start_host('-Background')
        self.assertEqual([f'start False True {host.pid}'], self.calls())
        probe = ('import sys; from services.shared import orion_host; '
                 'print(orion_host.inside(sys.argv[1]), flush=True); import time; time.sleep(300)')
        output = self.root / 'child.txt'
        with output.open('wb') as log:
            child = orion_host.popen([sys.executable, '-c', probe, str(self.store.path)], registry=self.store,
                                     component_root=self.code, stdout=log, stderr=subprocess.STDOUT,
                                     env=dict(os.environ, PYTHONPATH=str(ROOT)), creationflags=0x08000000)
        self.assertIsInstance(child, orion_host.HostedProcess)
        import psutil
        process = psutil.Process(child.pid)
        self.assertEqual(host.pid, process.ppid())
        wait_until(lambda: output.read_bytes().strip())
        self.assertEqual(b'True', output.read_bytes().strip())  # Inherited the host's job.
        host.kill()  # Task Manager "End task".
        wait_until(lambda: not process.is_running())

    def test_a_tree_outside_the_installation_never_goes_under_its_host(self):
        self.start_host('-Background')
        child = orion_host.popen([sys.executable, '-c', 'pass'], registry=self.store, component_root=self.root / 'other',
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertIsInstance(child, subprocess.Popen)
        child.wait(10)

    def test_require_host_refuses_without_a_host(self):
        with self.assertRaises(OSError):
            orion_host.popen([sys.executable, '-c', 'pass'], registry=self.store, require_host=True)

    def test_a_second_start_is_handed_to_the_running_host_and_stop_ends_it(self):
        host = self.start_host()
        self.assertEqual(0, subprocess.run([str(self.exe), '-Background'], timeout=30).returncode)
        wait_until(lambda: len(self.calls()) == 2)
        self.assertEqual(f'start False True {host.pid}', self.calls()[1])
        self.assertEqual(1, sum(1 for _ in [orion_host.find(self.store)] if _))
        self.assertEqual(0, subprocess.run([str(self.exe), '-Stop'], timeout=30).returncode)
        host.wait(20)  # Quit: the launcher's stop ran, then the host ended.
        self.assertEqual(f'stop False False {host.pid}', self.calls()[2])

    def test_the_launchers_stop_ends_the_host(self):
        host = self.start_host('-Background')
        self.assertEqual(host.pid, orion_host.release(self.store).pid)  # Not inside its job: waited for.
        self.assertFalse(host.is_running())
        self.assertFalse((self.bootstrap / 'host.exit').exists())

    def test_a_start_that_leaves_nothing_running_leaves_no_host(self):
        (self.bootstrap / 'nothing').touch()
        subprocess.Popen([str(self.exe)])
        host = wait_until(lambda: orion_host.find(self.store))
        host.wait(20)
        self.assertEqual(1, len(self.calls()))

    def test_disabled_start_with_windows_starts_nothing(self):
        record = self.store.read()
        record['preferences']['start_with_windows'] = False
        self.store.write(record)
        self.assertEqual(0, subprocess.run([str(self.exe), '-Startup'], timeout=30).returncode)
        time.sleep(2)
        self.assertIsNone(orion_host.find(self.store))
        self.assertEqual([], self.calls())

    def test_sign_out_stops_every_supervisor_gracefully_without_starting_a_process(self):
        host = self.start_host('-Background')
        # The stand-in entry claimed a running supervisor; the real one replaces that record once the entry has
        # ended (the host looks for running services when a start it ran ends).
        wait_until(lambda: not host.children())
        time.sleep(1)
        (self.state / 'processes.json').unlink()
        env = dict(os.environ, PYTHONPATH=str(ROOT), ORION_INSTALLATION_REGISTRY=str(self.store.path))
        lifecycle.ensure_started(self.code, 'hub', [sys.executable, str(PROBE), str(self.code), 'hub'], env=env, timeout=30)
        self.addCleanup(lambda: lifecycle.stop_component(self.code, 'hub', timeout=10))
        record = lifecycle.Runtime(self.code, 'hub').read()
        import psutil
        self.assertIn(host.pid, [p.pid for p in psutil.Process(record['supervisor']['pid']).parents()])
        child = record['children']['first']
        from test_tray_session_end import query_end_session, windows_of
        for hwnd in windows_of(host.pid):
            query_end_session(hwnd)
        record = lifecycle.Runtime(self.code, 'hub').read()
        self.assertEqual('stopped', record['status'])
        self.assertFalse(lifecycle.child_tree_alive(child))
        self.assertTrue((self.code / 'graceful-marker').exists())  # Graceful, not killed.
        host.wait(10)  # Then the host ends.


if __name__ == '__main__':
    unittest.main()
