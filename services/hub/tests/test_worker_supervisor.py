import subprocess
import io
import signal
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from services.hub import run_local


class WorkerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.worker = Mock(pid=101)
        self.worker.poll.return_value = 1
        self.replacement = Mock(pid=102)
        self.replacement.poll.return_value = None
        self.start = Mock(return_value=self.replacement)
        self.replace = Mock()
        self.stopping = threading.Event()
        self.report = Mock()
        self.supervisor = run_local.WorkerSupervisor(
            self.worker, self.start, self.replace, self.stopping, self.report)

    def check(self, now):
        with patch.object(run_local.time, 'monotonic', return_value=now):
            self.supervisor.check()

    def test_restart_only_after_exit_and_delay(self):
        self.worker.poll.return_value = None
        self.check(0)
        self.start.assert_not_called()
        self.worker.poll.return_value = 1
        self.check(0)
        self.check(0.9)
        self.start.assert_not_called()
        self.check(1)
        self.start.assert_called_once_with()
        self.replace.assert_called_once_with(self.worker, self.replacement)
        self.check(100)
        self.assertEqual(self.start.call_count, 1)

    def test_restart_budget_and_exponential_delays(self):
        self.start.return_value = self.worker
        now = 0
        for delay in [1, 2, 4, 8, 16]:
            self.check(now)
            self.assertEqual(self.supervisor.restart_at, now + delay)
            now += delay
            self.check(now)
        self.check(now)
        self.assertTrue(self.supervisor.exhausted)
        count = self.report.call_count
        self.check(now + 100)
        self.assertEqual(self.report.call_count, count)
        self.assertEqual(self.start.call_count, 5)

    def test_failed_launches_consume_budget(self):
        self.start.side_effect = OSError('launch failed')
        now = 0
        for delay in [1, 2, 4, 8, 16]:
            self.check(now)
            now += delay
            self.check(now)
        self.check(now)
        self.assertTrue(self.supervisor.exhausted)
        self.assertEqual(self.start.call_count, 5)
        self.replace.assert_not_called()

    def test_normal_shutdown_prevents_pending_restart(self):
        self.check(0)
        self.stopping.set()
        self.check(10)
        self.start.assert_not_called()
        self.replace.assert_not_called()

    def test_zero_exit_without_shutdown_is_also_restarted(self):
        self.worker.poll.return_value = 0
        self.check(0)
        self.check(1)
        self.start.assert_called_once()

    def test_real_child_crash_restarts_and_shutdown_does_not(self):
        child = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)'])
        child.wait(timeout=10)
        children = [child]

        def launch():
            proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            children.append(proc)
            return proc

        self.supervisor.worker = child
        self.supervisor.start_worker = Mock(side_effect=launch)
        try:
            self.check(0)
            self.check(1)
            replacement = self.supervisor.worker
            self.assertIsNone(replacement.poll())
            self.stopping.set()
            replacement.terminate()
            replacement.wait(timeout=10)
            self.check(100)
            self.supervisor.start_worker.assert_called_once()
        finally:
            for proc in children:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)

    def test_main_updates_pid_and_stops_current_worker_on_signal(self):
        apis = [Mock(pid=pid) for pid in range(200, 204)]
        for proc in apis:
            proc.poll.return_value = None
        redis = Mock(pid=199)
        redis.child = {'process': {}}
        redis.poll.return_value = None
        children = [redis] + apis + [self.worker, self.replacement]
        runtime = Mock(children={label: {'process': {}} for label in ('api', 'public', 'local', 'mobile')})
        runtime.requested.return_value = False
        runtime.launch.side_effect = children
        handlers = {}
        now = [0]
        waits = [0]
        event = threading.Event()

        def wait(timeout):
            waits[0] += 1
            if waits[0] == 3:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                return event.is_set()
            now[0] = waits[0] - 1
            return False

        settings = {'ORION_FLUTTER_AUTOSTART': '0', 'HUB_PORT': '8900', 'ORION_PUBLIC_PORT': '8911',
                    'ORION_LOCAL_PORT': '8910', 'ORION_MOBILE_PORT': '8443',
                    'ORION_LOCAL_SHORTCUTS': '0', 'REDIS_PORT': '6379',
                    'POSTGRES_PORT': '5432'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(run_local, 'get_env', side_effect=lambda name, default=None, **kw: settings.get(name, default)))
            stack.enter_context(patch.dict(run_local.os.environ, {'JWT_SECRET_KEY': 'test-key'}))
            stack.enter_context(patch.object(run_local.os.path, 'exists', return_value=False))
            stack.enter_context(patch.object(run_local.os, 'system'))
            stack.enter_context(patch('orion.kernel.local_installation.register_native_hub'))
            stack.enter_context(patch('services.shared.browser_shutdown.allow_hub_browsers'))
            stop_browsers = stack.enter_context(patch('services.shared.browser_shutdown.stop_hub_browsers'))
            stack.enter_context(patch.object(run_local.socket, 'socket'))
            stack.enter_context(patch.object(run_local.os.path, 'isfile', return_value=True))
            stack.enter_context(patch('services.shared.native_services.hub_database'))
            stack.enter_context(patch('services.shared.process_lifecycle.owns_listener', return_value=True))
            stack.enter_context(patch('services.shared.process_lifecycle.wait_health'))
            stack.enter_context(patch.object(run_local, 'worker_ready', return_value=True))
            stack.enter_context(patch('services.shared.process_lifecycle.living', return_value=True))
            stack.enter_context(patch('orion.kernel.local_security.tls_identity', return_value={'cert': 'fake', 'id': 'fake'}))
            stack.enter_context(patch.object(run_local.socket, 'create_connection'))
            stack.enter_context(patch.object(run_local.threading, 'Thread'))
            stack.enter_context(patch.object(run_local.threading, 'Event', return_value=event))
            stack.enter_context(patch.object(event, 'wait', side_effect=wait))
            stack.enter_context(patch.object(run_local.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(run_local.signal, 'signal', side_effect=lambda sig, handler: handlers.update({sig: handler})))
            stack.enter_context(patch('builtins.open', return_value=io.StringIO()))
            stack.enter_context(patch('builtins.print'))
            write = stack.enter_context(patch.object(run_local, 'write_pids'))
            with self.assertRaises(SystemExit) as exited:
                run_local._main(runtime)
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(write.call_count, 2)
        self.assertIn(self.replacement.pid, write.call_args.args[1])
        self.assertNotIn(self.worker.pid, write.call_args.args[1])
        self.worker.terminate.assert_not_called()
        runtime.stop_children.assert_called_once()
        runtime.database.stop.assert_called_once()
        self.assertTrue(event.is_set())


if __name__ == '__main__':
    unittest.main()
