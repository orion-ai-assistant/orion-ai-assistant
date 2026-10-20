"""Faster open/quit: Router and Hub start together, the window opens during the start, idle launches skip the
update helper process, and a supervisor that died during its own stop is not waited for until the timeout."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from services.shared import automatic_updates, launcher, process_lifecycle
from services.shared.installation_registry import Registry

ROOT = Path(__file__).resolve().parents[2]
HUB = {'kind': 'hub', 'code_path': 'H'}
ROUTER = {'kind': 'router', 'code_path': 'R'}


class LaunchSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='speed-')
        self.addCleanup(self.temp.cleanup)
        self.registry = Registry(Path(self.temp.name) / 'Orion/install.json')

    def test_router_and_hub_start_at_the_same_time(self):
        hub_started = threading.Event()
        def router(_, env=None):
            # One after the other, the Router would wait here for a Hub start that never begins.
            self.assertTrue(hub_started.wait(5), 'Hub start waited for the Router')
        launcher._start_all(self.registry, [ROUTER, HUB], lambda _: hub_started.set(), router)
        log = (self.registry.path.parent / 'updates/watcher.log').read_text(encoding='utf-8')
        self.assertIn('launch.timing step=start.router', log)
        self.assertIn('launch.timing step=start.hub', log)

    def test_the_router_is_started_without_the_hubs_configuration(self):
        # Hub and Router are separate applications: the Hub's .env (Docker-era STT_HOST=stt, ...) must not
        # replace the Router's own settings ("getaddrinfo failed" for every local voice request).
        hub = self.registry.path.parent / 'Hub'
        (hub / 'services').mkdir(parents=True)
        (hub / 'services/.env.global').write_text('STT_HOST=stt' + chr(10) + 'PATH=hub-only' + chr(10), encoding='utf-8')
        record = {'components': [{'kind': 'hub', 'code_path': str(hub), 'data_path': str(hub)}]}
        seen = {}
        with patch.object(Registry, 'read', return_value=record), \
                patch.dict('os.environ', {'ORION_KEEP': 'yes'}, clear=False):
            import os
            before_path = os.environ['PATH']
            with launcher.launch_environment(self.registry):
                self.assertEqual('stt', os.environ['STT_HOST'])  # The Hub itself still gets its configuration.
                launcher._start_all(self.registry, [ROUTER], lambda _: None,
                                    lambda _, env=None: seen.update(env))
        self.assertNotIn('STT_HOST', seen)
        self.assertEqual(before_path, seen['PATH'])  # A key the Hub overrode gets the process's own value back.
        self.assertEqual('yes', seen['ORION_KEEP'])
        self.assertEqual(str(self.registry.path), seen['ORION_INSTALLATION_REGISTRY'])

    def test_a_failed_router_start_is_reported_after_the_hub_start_finished(self):
        hub = MagicMock()
        with self.assertRaisesRegex(RuntimeError, 'router down'):
            launcher._start_all(self.registry, [ROUTER, HUB], hub, MagicMock(side_effect=RuntimeError('router down')))
        hub.assert_called_once_with('H')

    def test_window_opens_while_services_start(self):
        window_open = threading.Event()
        def start_hub(_):
            self.assertTrue(window_open.wait(5), 'window waited for the whole start')
        with patch.object(launcher, 'show_window', side_effect=lambda *a, **k: window_open.set()) as show, \
                patch('services.shared.automatic_updates.spawn_watcher'), patch.object(launcher, 'spawn_tray'):
            launcher._start_with_window(self.registry, [HUB], start_hub, MagicMock(), show=True)
        self.assertEqual(launcher.WINDOW_WAIT, show.call_args.kwargs['timeout'])

    def test_a_failed_start_cancels_the_waiting_window(self):
        aborted = []
        def show(registry, hub, *, timeout, abort):
            aborted.append(abort.wait(5))
        with patch.object(launcher, 'show_window', side_effect=show), \
                patch('services.shared.automatic_updates.spawn_watcher') as watcher, \
                self.assertRaisesRegex(RuntimeError, 'hub down'):
            launcher._start_with_window(self.registry, [HUB], MagicMock(side_effect=RuntimeError('hub down')),
                                        MagicMock(), show=True)
        self.assertEqual([True], aborted)
        watcher.assert_not_called()

    def test_show_window_returns_at_once_when_aborted(self):
        abort = threading.Event()
        abort.set()
        with patch('services.shared.process_lifecycle.Runtime') as runtime:
            launcher.show_window(self.registry, HUB, timeout=60, abort=abort)
        runtime.assert_not_called()

    def test_idle_launch_skips_the_update_helper_process(self):
        with patch.object(automatic_updates, 'runtime_command') as command, patch('subprocess.run') as run:
            automatic_updates.run_startup(self.registry)
        command.assert_not_called()
        run.assert_not_called()
        (self.registry.path.parent / 'updates').mkdir(parents=True)
        (self.registry.path.parent / 'updates/active.json').write_text('{}', encoding='utf-8')
        with patch.object(automatic_updates, 'runtime_command', return_value=['python']), \
                patch('subprocess.run', return_value=MagicMock(returncode=0)) as run:
            automatic_updates.run_startup(self.registry)
        run.assert_called_once()


class DeadSupervisorStopTests(unittest.TestCase):
    def test_stop_does_not_wait_for_a_supervisor_that_died_during_its_own_stop(self):
        record = {'status': 'stopping', 'run_id': 'r1', 'supervisor': {'pid': 1}, 'children': {}, 'database': None}
        temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='speed-')
        self.addCleanup(temp.cleanup)
        (Path(temp.name) / '.orion-lifecycle/hub').mkdir(parents=True)
        alive = iter([True, False])
        with patch.object(process_lifecycle, 'InstanceLock'), \
                patch.object(process_lifecycle.Runtime, 'read', return_value=dict(record)), \
                patch.object(process_lifecycle, 'living', side_effect=lambda _: next(alive)), \
                patch.object(process_lifecycle, 'durable_json') as saved, \
                patch.object(process_lifecycle.time, 'sleep') as sleep:
            self.assertTrue(process_lifecycle.stop_component(temp.name, 'hub', timeout=120))
        sleep.assert_not_called()  # Reconciled at once, like a crashed supervisor (before: 120 s, then an error).
        self.assertEqual('stopped', saved.call_args.args[1]['status'])


if __name__ == '__main__':
    unittest.main()
