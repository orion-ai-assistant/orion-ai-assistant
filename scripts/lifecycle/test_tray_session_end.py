"""Windows signing out: the tray stops the services itself, without starting any process.

Windows refuses new processes while it signs out (0xc0000142): the tray's earlier "start the launcher's
stop" never ran, and PostgreSQL was killed instead of shut down. Real tray script, real supervisor; the
sign-out is the same WM_QUERYENDSESSION message Windows sends to every top-level window."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from services.shared import launcher, process_lifecycle as lifecycle
from services.shared.installation_registry import Registry, component_record, new_registry

ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name('probe_supervisor.py')


def windows_of(pid):
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32')
    found = []
    callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def collect(hwnd, _):
        owner = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
        return True
    user.EnumWindows(callback(collect), 0)
    return found


def query_end_session(hwnd, timeout=40):
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32')
    user.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                         ctypes.POINTER(ctypes.c_size_t)]
    answer = ctypes.c_size_t()
    # WM_QUERYENDSESSION, ENDSESSION_LOGOFF; SMTO_ABORTIFHUNG. Returns once the window has answered.
    user.SendMessageTimeoutW(hwnd, 0x11, 0, 0x80000000, 0x2, int(timeout * 1000), ctypes.byref(answer))


@unittest.skipUnless(os.name == 'nt', 'Windows sign-out')
class TraySessionEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='tray-session-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / 'Hub'
        (self.code / 'services/hub').mkdir(parents=True)
        (self.code / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        (self.code / 'child.py').write_text(
            'import time\nfrom pathlib import Path\n'
            'try:\n    while True: time.sleep(.05)\n'
            'finally:\n    Path("graceful-marker").touch()\n', encoding='utf-8')
        self.store = Registry(self.root / 'Orion/install.json')
        self.store.write(new_registry(self.root, [component_record('hub', self.code, ownership='owned', management='local')]))

    def test_sign_out_stops_every_supervisor_without_starting_a_process(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        lifecycle.ensure_started(self.code, 'hub', [sys.executable, str(PROBE), str(self.code), 'hub'], env=env, timeout=30)
        self.addCleanup(lambda: lifecycle.stop_component(self.code, 'hub', timeout=10))
        child = lifecycle.Runtime(self.code, 'hub').read()['children']['first']
        self.assertTrue(launcher.spawn_tray(self.store, popen=subprocess.Popen))
        self.addCleanup(launcher.stop_tray, self.store)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            trays = launcher.tray_processes(self.store)
            hwnds = windows_of(trays[0].pid) if trays else []
            if hwnds:
                break
            time.sleep(.2)
        self.assertTrue(hwnds, 'tray window did not appear')
        time.sleep(2)  # The icon's message loop is running and its session window exists.
        for hwnd in windows_of(trays[0].pid):
            query_end_session(hwnd)
        record = lifecycle.Runtime(self.code, 'hub').read()
        self.assertEqual('stopped', record['status'])
        self.assertFalse(lifecycle.child_tree_alive(child))
        self.assertTrue((self.code / 'graceful-marker').exists())  # Graceful, not killed.
        self.assertFalse(lifecycle.living(record['supervisor']))


if __name__ == '__main__':
    unittest.main()
