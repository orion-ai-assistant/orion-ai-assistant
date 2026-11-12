"""The Hub runs for weeks: children come back when they end, the log does not grow without end."""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from services.hub import run_local


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ChildSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.stopping = threading.Event()
        self.reports = []
        self.alive = True
        self.restart = Mock()
        self.supervisor = run_local.ChildSupervisor('Redis', lambda: self.alive, self.restart, self.stopping,
                                                    self.reports.append, clock=self.clock)

    def tick(self, seconds=1):
        self.clock.now += seconds
        self.supervisor.check()

    def test_a_living_child_is_left_alone(self):
        for _ in range(5):
            self.tick()
        self.restart.assert_not_called()
        self.assertEqual(self.reports, [])

    def test_an_ended_child_is_started_again(self):
        self.alive = False
        self.restart.side_effect = lambda: setattr(self, 'alive', True)
        self.tick()
        self.restart.assert_called_once()
        self.assertEqual(self.reports, ['Redis is not running; starting it again', 'Redis started again'])
        self.tick()
        self.restart.assert_called_once()

    def test_failed_restarts_back_off_to_a_minute_and_never_give_up(self):
        # Before: a port still held or an API that could not start while Redis was down ended the whole Hub.
        self.alive = False
        self.restart.side_effect = RuntimeError('Port conflict: 6379')
        delays = []
        for _ in range(8):
            calls = self.restart.call_count
            waited = 0
            while self.restart.call_count == calls:
                self.tick()
                waited += 1
            delays.append(waited)
        self.assertEqual(delays[1:], [2, 4, 8, 16, 32, 60, 60])
        self.restart.side_effect = None
        self.tick(60)
        self.assertEqual(self.reports[-1], 'Redis started again')
        self.assertEqual(self.supervisor.failures, 0)

    def test_unknown_state_is_not_a_restart(self):
        def unknown():
            raise PermissionError('access denied')
        self.supervisor.running = unknown
        self.tick()
        self.restart.assert_not_called()
        self.assertIn('state unknown', self.reports[0])

    def test_nothing_happens_while_the_hub_stops(self):
        self.alive = False
        self.stopping.set()
        self.tick(100)
        self.restart.assert_not_called()


class WorkerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.worker = Mock(pid=1)
        self.worker.poll.return_value = 1
        self.start = Mock(return_value=self.worker)
        self.report = Mock()
        with patch.object(run_local.time, 'monotonic', return_value=0.0):
            self.supervisor = run_local.WorkerSupervisor(self.worker, self.start, Mock(), threading.Event(),
                                                         self.report)

    def check(self, now):
        with patch.object(run_local.time, 'monotonic', return_value=now):
            self.supervisor.check()

    def exhaust(self):
        now = 0
        for delay in [1, 2, 4, 8, 16]:
            self.check(now)
            now += delay
            self.check(now)
        self.check(now)
        self.assertTrue(self.supervisor.exhausted)
        return now

    def test_after_five_quick_restarts_it_keeps_trying_every_ten_minutes(self):
        now = self.exhaust()
        self.check(now + 599)
        self.assertEqual(self.start.call_count, 5)
        self.check(now + 600)
        self.assertEqual(self.start.call_count, 6)
        self.check(now + 600)
        self.check(now + 1200)
        self.assertEqual(self.start.call_count, 7)

    def test_a_worker_that_stays_up_earns_its_quick_restarts_back(self):
        now = self.exhaust()
        self.check(now + 600)  # Restarted; this one lives.
        self.worker.poll.return_value = None
        self.check(now + 600 + 599)
        self.assertEqual(self.supervisor.attempts, 5)
        self.check(now + 1200)
        self.assertEqual(self.supervisor.attempts, 0)
        self.assertFalse(self.supervisor.exhausted)
        self.worker.poll.return_value = 1
        self.check(now + 1300)
        self.assertEqual(self.supervisor.restart_at, now + 1301)  # One second again, not ten minutes.

    def test_a_lifecycle_error_does_not_escape(self):
        from services.shared.process_lifecycle import LifecycleError
        self.start.side_effect = LifecycleError('Live descendants require reconciliation before replacement')
        self.check(0)
        self.check(1)  # Raised out of the Hub's loop before, which then stopped every service.
        self.assertIn('failed', self.report.call_args.args[0])


class RotatingLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'hub.log')

    def tearDown(self):
        self.directory.cleanup()

    def test_rotates_at_the_limit_and_keeps_three_old_files(self):
        log = run_local.RotatingLog(self.path, max_bytes=100, backups=3)
        for index in range(30):
            log.write(f'line {index:02d} ' + 'x' * 40 + '\n')
            log.flush()
        log.close()
        names = sorted(os.listdir(self.directory.name))
        self.assertEqual(names, ['hub.log', 'hub.log.1', 'hub.log.2', 'hub.log.3'])
        for name in names:
            self.assertLessEqual(os.path.getsize(os.path.join(self.directory.name, name)), 150)
        self.assertIn('line 29', Path(self.path).read_text(encoding='utf-8'))
        self.assertIn('line 26', Path(self.path + '.1').read_text(encoding='utf-8'))

    def test_an_existing_large_log_is_rotated_on_the_first_write(self):
        Path(self.path).write_text('old\n' * 100, encoding='utf-8')
        log = run_local.RotatingLog(self.path, max_bytes=100)
        log.write('new\n')
        log.close()
        self.assertEqual(Path(self.path).read_text(encoding='utf-8'), 'new\n')
        self.assertTrue(Path(self.path + '.1').is_file())

    def test_a_refused_rename_keeps_writing(self):
        log = run_local.RotatingLog(self.path, max_bytes=10)
        with patch.object(run_local.os, 'replace', side_effect=PermissionError('in use')):
            log.write('a' * 20 + '\n')
            log.write('b\n')
            log.write('c\n')
        log.close()
        self.assertEqual(Path(self.path).read_text(encoding='utf-8'), 'a' * 20 + '\nb\nc\n')

    def test_writes_after_close_are_ignored(self):
        log = run_local.RotatingLog(self.path)
        log.close()
        log.write('late line from a log thread\n')
        log.flush()


class RefreshChildrenTests(unittest.TestCase):
    def test_state_is_saved_only_when_children_change(self):
        from services.shared import process_lifecycle
        runtime = process_lifecycle.Runtime.__new__(process_lifecycle.Runtime)
        runtime.children = {'redis': {'job': {'name': 'j'}, 'process': {'pid': 1}}}
        records = [[{'pid': 2, 'started': 1.0}]]
        job = Mock()
        job.__enter__ = Mock(return_value=job)
        job.__exit__ = Mock(return_value=False)
        job.records.side_effect = lambda: records[0]
        adapter = Mock()
        adapter.Job.return_value = job
        runtime.save = Mock()
        with patch.object(process_lifecycle, 'job_adapter', return_value=adapter):
            runtime.refresh_children()
            runtime.refresh_children()
            runtime.refresh_children()
            self.assertEqual(runtime.save.call_count, 1)
            records[0] = [{'pid': 2, 'started': 1.0}, {'pid': 3, 'started': 2.0}]
            runtime.refresh_children()
        self.assertEqual(runtime.save.call_count, 2)


REDIS_EXE = Path(__file__).resolve().parents[3] / '.local_db' / 'redis' / 'redis-server.exe'


@unittest.skipUnless(os.name == 'nt' and REDIS_EXE.is_file(), 'portable Redis (Windows) not installed')
class LiveRedisRestartTests(unittest.TestCase):
    """A real Redis under a real Runtime (its own temporary folder and port, never the Hub's): killed, it is
    started again by the same supervisor the Hub uses, and answers."""

    def test_a_killed_redis_comes_back(self):
        import socket
        import subprocess
        import psutil
        from services.shared.process_lifecycle import Runtime, assert_port_free, living
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as root:
            with Runtime(root, 'redis-test') as runtime:
                def start():
                    proc = runtime.launch('redis', [str(REDIS_EXE), '--bind', '127.0.0.1', '--port', str(port),
                                                    '--save', '', '--appendonly', 'no'],
                                          cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=0x08000000)
                    run_local.wait_redis(proc.child['process'], port)
                    return proc

                def restart():
                    assert_port_free(port)
                    start()

                try:
                    start()
                    reports = []
                    supervisor = run_local.ChildSupervisor(
                        'Redis', lambda: living(runtime.children['redis']['process']), restart,
                        threading.Event(), reports.append)
                    supervisor.check()
                    self.assertEqual(reports, [])
                    # Redis itself ends (as when Windows refused it memory), not the host that waits on it.
                    host = psutil.Process(runtime.children['redis']['process']['pid'])
                    servers = [item for item in host.children(recursive=True)
                               if item.name().lower() == 'redis-server.exe']
                    self.assertEqual(len(servers), 1)
                    servers[0].kill()
                    deadline = time.monotonic() + 10
                    while living(runtime.children['redis']['process']) and time.monotonic() < deadline:
                        time.sleep(.05)
                    supervisor.check()
                    self.assertEqual(reports, ['Redis is not running; starting it again', 'Redis started again'])
                    with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                        client.sendall(b'*1\r\n$4\r\nPING\r\n')
                        self.assertTrue(client.recv(64).startswith(b'+PONG'))
                finally:
                    runtime.stop_children(timeout=1)  # This Redis has no stop channel: its job ends it.


@unittest.skipUnless(os.name == 'nt' and REDIS_EXE.is_file(), 'portable Redis (Windows) not installed')
class LiveRedisAofTests(unittest.TestCase):
    """The switch to the append-only file keeps what the snapshot held, and a crash afterwards loses nothing."""

    def start(self, directory, port, argv=None):
        import subprocess
        argv = argv or run_local.redis_argv(str(REDIS_EXE), port, directory)[0]
        proc = subprocess.Popen(argv, cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=0x08000000)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.monotonic() + 15
        while True:
            try:
                if run_local.redis_call(port, 'PING').startswith(b'+PONG'):
                    return proc
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError('Redis did not start')
            time.sleep(.05)

    def test_snapshot_then_aof_then_crash_keeps_every_key(self):
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            # An older Hub: snapshots only.
            old = self.start(directory, port, [str(REDIS_EXE), '--bind', '127.0.0.1', '--port', str(port)])
            run_local.redis_call(port, 'SET', 'from-snapshot', '1')
            self.assertTrue(run_local.redis_call(port, 'SAVE').startswith(b'+OK'))
            old.kill(); old.wait(10)
            argv, switch = run_local.redis_argv(str(REDIS_EXE), port, directory)
            self.assertTrue(switch)
            self.assertTrue(os.path.isfile(os.path.join(directory, 'dump.rdb.before-aof')))
            first = self.start(directory, port, argv)
            run_local.switch_on_aof(port)
            deadline = time.monotonic() + 30
            while not run_local.aof_written(port):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.1)
            run_local.redis_call(port, 'SET', 'after-switch', '2')
            time.sleep(1.5)  # appendfsync everysec
            first.kill(); first.wait(10)  # A crash: no snapshot is written at exit.
            argv, switch = run_local.redis_argv(str(REDIS_EXE), port, directory)
            self.assertFalse(switch)
            self.assertIn('--appendonly', argv)
            last = self.start(directory, port, argv)
            try:
                self.assertEqual(run_local.redis_call(port, 'GET', 'from-snapshot'), b'$1')
                self.assertEqual(run_local.redis_call(port, 'GET', 'after-switch'), b'$1')
            finally:
                last.kill()
                last.wait(10)

    def test_an_empty_aof_left_by_an_interrupted_switch_does_not_hide_the_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'dump.rdb').write_bytes(b'REDIS0009 snapshot')
            Path(directory, run_local.AOF_NAME).write_bytes(b'')
            argv, switch = run_local.redis_argv('redis-server.exe', 6379, directory)
            self.assertTrue(switch)
            self.assertNotIn('--appendonly', argv)
            self.assertTrue(Path(directory, run_local.AOF_NAME + '.empty').is_file())


class RouterAddressTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_address_is_written_only_when_it_changed(self):
        # Runs every 15 s for weeks; each write made every trust watcher read the store again.
        from orion.kernel import pinned_router
        store = Mock()
        store.routers.return_value = [{'id': 'r', 'fp': 'f', 'url': 'https://router.local:9443'}]
        with patch.object(pinned_router, 'SecurityStore', return_value=store), \
                patch.object(pinned_router, 'discover_router', return_value=['https://router.local:9443']):
            await pinned_router.resolve_trusted_routers()
        store.update_router_address.assert_not_called()
        with patch.object(pinned_router, 'SecurityStore', return_value=store), \
                patch.object(pinned_router, 'discover_router', return_value=['https://10.0.0.5:9443']):
            await pinned_router.resolve_trusted_routers()
        store.update_router_address.assert_called_once_with('r', 'f', 'https://10.0.0.5:9443')


class SchemaOnceTests(unittest.IsolatedAsyncioTestCase):
    async def test_tables_are_created_once_per_process(self):
        from orion.kernel import registry
        conn = AsyncMock()
        with patch.object(registry, '_tables_ready', False), \
                patch.object(registry, '_create_tables', AsyncMock()) as create:
            await registry._ensure_tables(conn)
            await registry._ensure_tables(conn)
            await registry._ensure_tables(conn)
        create.assert_awaited_once()
        self.assertEqual(conn.execute.await_count, 2)  # One advisory lock and unlock, not one per request.

    async def test_a_failed_creation_is_tried_again(self):
        from orion.kernel import registry
        conn = AsyncMock()
        create = AsyncMock(side_effect=[OSError('connection lost'), None])
        with patch.object(registry, '_tables_ready', False), patch.object(registry, '_create_tables', create):
            with self.assertRaises(OSError):
                await registry._ensure_tables(conn)
            await registry._ensure_tables(conn)
        self.assertEqual(create.await_count, 2)

    async def test_session_connection_is_closed_when_the_schema_fails(self):
        from orion.kernel import registry
        conn = AsyncMock()
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), \
                patch.object(registry, '_ensure_tables', AsyncMock(side_effect=OSError('ddl failed'))):
            with self.assertRaises(OSError):
                await registry._session_connection()
        conn.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
