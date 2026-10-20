"""Stage 01 ownership and real Windows process/lock integration tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import Mock, patch

import psutil
import pytest

from services.shared import process_lifecycle as lifecycle
from services.shared.native_postgres import Postgres

ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name('probe_supervisor.py')


@pytest.fixture
def lab(tmp_path):
    (tmp_path / 'child.py').write_text(
        'import time\nfrom pathlib import Path\n'
        'try:\n    while True: time.sleep(.05)\n'
        'finally:\n    Path("graceful-marker").touch()\n', encoding='utf-8')
    yield tmp_path
    # Only captured ownership references from this test's state are eligible.
    for file in tmp_path.rglob('processes.json'):
        try:
            record = lifecycle.read_json(file)
        except lifecycle.LifecycleError:
            continue
        try:
            if record['status'] != 'stopped':
                lifecycle.stop_component(record['root'], record['component'], timeout=5)
        except lifecycle.LifecycleError:
            for child in record['children'].values():
                lifecycle.ManagedChild(child).stop(.2)


def start(root, component='probe'):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return lifecycle.ensure_started(root, component, [sys.executable, str(PROBE), str(root), component],
                                    env=env, timeout=30)


def state(root, component='probe'):
    return lifecycle.Runtime(root, component).read()


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('Integration condition timed out')


def test_ten_concurrent_starts_produce_one_instance_and_graceful_stop(lab):
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert all(pool.map(lambda _: start(lab), range(10)))
    original = state(lab)
    assert len(original['children']) == 1
    assert start(lab)
    assert state(lab)['supervisor'] == original['supervisor']
    assert lifecycle.stop_component(lab, 'probe')
    assert (lab / 'graceful-marker').exists()
    assert state(lab)['status'] == 'stopped'
    assert not lifecycle.living(original['children']['first']['process'])


def test_partial_installation_starts_only_missing_child(lab):
    start(lab)
    first = state(lab)['children']['first']['process']
    (lab / 'second.enabled').touch()
    wait_until(lambda: len(state(lab)['children']) == 2
               and state(lab)['children']['second']['process'] is not None
               and lifecycle.child_running(state(lab)['children']['second']))
    assert state(lab)['children']['first']['process'] == first
    lifecycle.ManagedChild(state(lab)['children']['second']).stop()
    wait_until(lambda: state(lab)['children']['second']['process'] is not None
               and state(lab)['children']['second']['process']['pid'] != first['pid']
               and lifecycle.living(state(lab)['children']['second']['process']))
    assert state(lab)['children']['first']['process'] == first


def test_a_killed_supervisor_takes_its_children_with_it_and_the_next_start_is_clean(lab):
    start(lab)
    previous = state(lab)
    with lifecycle.ProcessReference(previous['supervisor']) as reference:
        reference.force()  # Task Manager, a crash, Windows signing out: no clean stop runs.
        reference.wait(5)
    # The supervisor held the only handle of its children's jobs: Windows ended them with it.
    wait_until(lambda: not lifecycle.child_tree_alive(previous['children']['first']))
    (lab / 'second.enabled').touch()
    start(lab)
    current = state(lab)
    assert current['run_id'] != previous['run_id']
    assert current['children']['first']['process'] != previous['children']['first']['process']  # Never adopted.
    wait_until(lambda: len(state(lab)['children']) == 2)


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership')
def test_a_new_supervisor_stops_ownerless_leftovers_instead_of_adopting_them(lab):
    # An older Orion's host kept its own job handle, so its child outlived the supervisor: simulated by holding
    # a handle to the job here. The next supervisor ends that child and starts its own.
    start(lab)
    previous = state(lab)
    child = previous['children']['first']
    held = lifecycle.job_adapter().Job(child['job'])
    try:
        with lifecycle.ProcessReference(previous['supervisor']) as reference:
            reference.force()
            reference.wait(5)
        assert lifecycle.child_tree_alive(child)
        start(lab)
        assert not lifecycle.child_tree_alive(child)
        assert state(lab)['children']['first']['process'] != child['process']
    finally:
        held.close()


def test_two_installations_and_foreign_listener_remain_independent(lab):
    other = lab / 'İkinci kurulum ş'
    other.mkdir()
    (other / 'child.py').write_bytes((lab / 'child.py').read_bytes())
    start(lab)
    start(other)
    foreign = socket.socket()
    try:
        foreign.bind(('127.0.0.1', 0))
        foreign.listen()
        with pytest.raises(lifecycle.LifecycleError, match='Port conflict'):
            lifecycle.assert_port_free(foreign.getsockname()[1])
        saved = state(other)
        lifecycle.stop_component(lab, 'probe')
        assert lifecycle.living(saved['supervisor'])
        assert lifecycle.living(saved['children']['first']['process'])
        assert foreign.getsockname()[1] > 0
        lifecycle.stop_component(other, 'probe')
    finally:
        foreign.close()


def test_operation_and_lifetime_locks_are_independent(lab):
    start(lab)
    directory = lifecycle.state_dir(lab, 'probe')
    with lifecycle.InstanceLock(directory, 'operation', timeout=.1):
        with pytest.raises(lifecycle.LifecycleError, match='busy'):
            with lifecycle.InstanceLock(directory, 'instance', timeout=.1):
                pass


def test_abandoned_lifetime_lock_does_not_authorize_killing_children(lab):
    start(lab)
    previous = state(lab)
    handle = None
    if os.name == 'nt':
        name = 'Local\\Orion-' + lifecycle.digest([lifecycle.user_key(),
            lifecycle.canonical(lifecycle.state_dir(lab, 'probe')), 'instance'])
        handle = lifecycle.K.CreateMutexW(None, False, name)
        assert handle
    try:
        with lifecycle.ProcessReference(previous['supervisor']) as reference:
            reference.force()
            reference.wait(5)
        with lifecycle.InstanceLock(lifecycle.state_dir(lab, 'probe'), 'instance', timeout=.2) as lock:
            if handle:
                assert lock.abandoned
            # The lock only reports the dead owner; its children ended with the owner, not with this lock.
            wait_until(lambda: not lifecycle.child_tree_alive(previous['children']['first']))
    finally:
        if handle:
            lifecycle.K.CloseHandle(handle)
    assert lifecycle.stop_component(lab, 'probe')


def test_reused_pid_or_changed_command_does_not_signal_foreign_process(lab):
    start(lab)
    original = state(lab)['children']['first']['process']
    for field, value in [('started', original['started'] - 100), ('command_hash', 'other-command'), ('exe', 'foreign.exe')]:
        with pytest.raises(lifecycle.LifecycleError, match='identity mismatch'):
            lifecycle.ManagedChild({'process': {**original, field: value}, 'control': None}).stop(.1)
        assert lifecycle.living(original)


def test_access_denied_does_not_become_not_running(lab):
    start(lab)
    record = state(lab)['children']['first']['process']
    with patch.object(lifecycle.ProcessReference, 'describe', side_effect=psutil.AccessDenied(record['pid'])):
        with pytest.raises(lifecycle.LifecycleError, match='access denied'):
            lifecycle.living(record)
    assert lifecycle.living(record)


def test_stop_timeout_preserves_state_and_never_forces_supervisor(lab):
    # Current process deliberately does not consume the request during stop.
    with lifecycle.Runtime(lab, 'timeout') as runtime:
        runtime.launch('first', [sys.executable, str(lab / 'child.py')], python=True)
        runtime.ready()
        before = state(lab, 'timeout')
        with pytest.raises(lifecycle.LifecycleError, match='timeout'):
            lifecycle.stop_component(lab, 'timeout', timeout=.2)
        assert state(lab, 'timeout')['supervisor'] == before['supervisor']
        assert lifecycle.living(before['children']['first']['process'])


def test_stale_legacy_locks_are_preserved(lab):
    old = lab / '.orion.prod.lock'
    old.write_text('unverified old lock')
    start(lab)
    assert old.read_text() == 'unverified old lock'


def test_process_handle_is_revalidated_before_signal(lab):
    start(lab)
    record = state(lab)['children']['first']['process']
    with patch.object(lifecycle.ProcessReference, 'describe', return_value={**record, 'started': 1}):
        with pytest.raises(lifecycle.LifecycleError):
            lifecycle.ManagedChild({'process': record, 'control': None}).stop(.1)
    assert lifecycle.living(record)


def test_corrupt_state_fails_closed(lab):
    path = lifecycle.state_dir(lab, 'probe') / 'processes.json'
    path.parent.mkdir(parents=True)
    path.write_text('{')
    with pytest.raises(lifecycle.LifecycleError, match='unreadable'):
        start(lab)
    assert path.read_text() == '{'


def test_a_failed_graceful_stop_still_ends_the_child_then_stops_the_database(lab):
    runtime = lifecycle.Runtime(lab, 'failed-stop')
    runtime.__enter__()
    child = runtime.launch('first', [sys.executable, str(lab / 'child.py')], python=True).child
    database = Mock(ctl='fake', data='fake', port=1, user='fake', database='fake')
    runtime.database = database
    runtime.before_stop = Mock(side_effect=RuntimeError('browser shutdown failed'))  # Never stops the stop.
    with patch.object(lifecycle.ManagedChild, 'stop', side_effect=lifecycle.LifecycleError('timeout')):
        runtime.__exit__()
    assert not lifecycle.child_tree_alive(child)  # Ended through its job.
    database.stop.assert_called_once()
    assert state(lab, 'failed-stop')['status'] == 'stopped'


def test_database_is_never_stopped_while_a_child_still_runs(lab):
    runtime = lifecycle.Runtime(lab, 'stuck-stop')
    runtime.__enter__()
    runtime.launch('first', [sys.executable, str(lab / 'child.py')], python=True)
    database = Mock(ctl='fake', data='fake', port=1, user='fake', database='fake')
    runtime.database = database
    with patch.object(lifecycle.ManagedChild, 'stop', side_effect=lifecycle.LifecycleError('timeout')), \
            patch.object(lifecycle, 'child_tree_alive', return_value=True), \
            patch.object(lifecycle, 'job_adapter') as jobs:
        jobs.return_value.Job.return_value.__enter__.return_value.terminate.side_effect = lifecycle.LifecycleError('stuck')
        with pytest.raises(lifecycle.LifecycleError, match='still running'):
            runtime.__exit__()
    database.stop.assert_not_called()
    assert state(lab, 'stuck-stop')['status'] == 'stopping'
    # The supervisor's handles are closed by __exit__: the child already ended with them.


def test_recovery_keeps_database_shutdown_metadata_until_verified_stop(lab):
    runtime = lifecycle.Runtime(lab, 'recovered-database')
    runtime.__enter__()
    runtime.launch('first', [sys.executable, str(lab / 'child.py')], python=True)
    database = dict(ctl='fake', data=str(lab / 'db'), port=15432, user='fixture', database='postgres')
    runtime.previous_database = database
    runtime.ready()
    assert state(lab, 'recovered-database')['database'] == database
    with patch('services.shared.native_postgres.Postgres') as adapter:
        runtime.__exit__()
        adapter.assert_called_once_with(**database)
        adapter.return_value.stop.assert_called_once()
    assert state(lab, 'recovered-database')['status'] == 'stopped'


def test_compose_selection_rejects_other_checkout_and_name_only_matches(tmp_path):
    import manager
    from installer.app import config
    own = tmp_path / 'own/hub'
    other = tmp_path / 'other/hub'
    with patch.object(config, 'all_manifests', return_value=[({'category': 'hub'}, str(own / 'manifest.json'))]), \
            patch.object(config, '_load_global_env', return_value={}):
        assert manager._owns_compose_project('orion-hub', str(own))
        assert not manager._owns_compose_project('orion-hub', str(other))
        assert not manager._owns_compose_project('orion-other', str(own))
        assert not manager._owns_compose_project('orion-hub', '')


def test_ready_record_with_missing_child_is_not_reported_ready(tmp_path):
    from services.shared import native_services
    record = dict(status='ready', supervisor={}, children={'app': {'process': {}}})
    with patch.object(lifecycle.Runtime, 'read', return_value=record), \
            patch.object(lifecycle, 'living', side_effect=[True, False]):
        assert native_services.component_status(tmp_path, 'service') == 'starting'


def test_installer_never_reports_foreign_port_as_ready(tmp_path):
    from installer.app import services, config
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        data = dict(id='fixture-service', name='Fixture', category='tts', port=listener.getsockname()[1])
        with patch.dict(os.environ, {'ORION_INSTALL_MODE': 'local'}), \
                patch.object(config, 'all_manifests', return_value=[(data, str(tmp_path / 'manifest.json'))]), \
                patch.object(services.docker_utils, 'get_running_containers', return_value={}):
            result = services.get_services()[0]
        assert not result['is_running']
        assert result['lifecycle_status'] == 'unverified'


def test_postgres_failure_prevents_hub_accepting_work():
    from services.hub import run_local
    fake = Mock()
    fake.ensure.side_effect = lifecycle.LifecycleError('DB unavailable')
    env = {'HUB_PORT': '18080', 'REDIS_PORT': '16379', 'POSTGRES_PORT': '15432',
           'JWT_SECRET_KEY': 'fixture-key', 'ORION_LOCAL_SHORTCUTS': '0'}
    with patch.object(run_local, 'get_env', side_effect=lambda key, default=None, **kw: env.get(key, default)), \
            patch.dict(os.environ, env), patch('orion.kernel.local_installation.register_native_hub'), \
            patch('services.shared.browser_shutdown.allow_hub_browsers'), \
            patch('services.shared.native_services.hub_database', return_value=fake), \
            patch('builtins.open', __import__('unittest.mock').mock.mock_open()), \
            patch.object(run_local.subprocess, 'Popen') as launch:
        with pytest.raises(lifecycle.LifecycleError, match='DB unavailable'):
            run_local._main(Mock())
        launch.assert_not_called()


def test_cli_and_installer_delegate_to_same_hub_contract():
    import manager
    from installer.app import services, config
    with patch.object(manager, 'get_mode_from_args', return_value='local'), \
            patch.object(manager, 'find_orionrouter_script', return_value=(None, None)), \
            patch.object(services, '_get_context', return_value=({'id': 'orion-hub'}, 'unused', {})), \
            patch.dict(os.environ, {'ORION_INSTALL_MODE': 'local'}), \
            patch('services.shared.native_services.start_hub', return_value=True) as start_mock, \
            patch('services.shared.native_services.stop_hub', return_value=True) as stop_mock:
        manager.handle_start(['local'])
        services.start_local_service('orion-hub')
        assert start_mock.call_args_list[0] == start_mock.call_args_list[1]
        manager.handle_stop(['local'])
        services.stop_service('orion-hub')
        assert stop_mock.call_args_list[0] == stop_mock.call_args_list[1]


def test_legacy_router_uses_existing_python_without_requiring_new_venv(tmp_path):
    from services.shared import native_services
    with patch.object(native_services.shutil, 'which', return_value='existing-python'):
        assert native_services.router_python(tmp_path) == 'existing-python'
    with patch.object(native_services.shutil, 'which', return_value=None):
        with pytest.raises(RuntimeError, match='runtime unavailable'):
            native_services.router_python(tmp_path)


def test_service_supervisor_uses_installer_dependencies_and_service_python(tmp_path):
    from installer.app import services, config
    service = tmp_path / 'services/tts'
    service.mkdir(parents=True)
    (service / 'api.py').touch()
    from services.shared import native_services
    with patch.object(services, '_get_context', return_value=({'category': 'tts'}, str(service), {})), \
            patch.object(config, 'PROJECT_ROOT', str(tmp_path)), \
            patch.object(config, '_load_global_env', return_value={'TTS_PORT': '18810'}), \
            patch.object(native_services, 'python_at', side_effect=lambda root: str(Path(root) / 'python')), \
            patch.object(lifecycle, 'ensure_started', return_value=True) as start:
        assert services.start_local_service('orion-tts')
        command = start.call_args.args[2]
        assert command[0] == str(tmp_path / 'installer/python')
        assert command[-1] == str(service / 'python')


def test_router_vendored_engine_has_one_canonical_source():
    router = ROOT.parent / 'orion-router/core'
    for name in ('process_lifecycle.py', 'process_child.py', 'process_child.cjs',
                 'windows_job.py', 'process_host.py', 'windows_postgres.py'):
        assert (ROOT / 'services/shared' / name).read_bytes() == (router / name).read_bytes()
    expected = (ROOT / 'services/shared/native_postgres.py').read_text().replace(
        'from services.shared.process_lifecycle import', 'from core.process_lifecycle import')
    expected = expected.replace('from services.shared.windows_postgres import', 'from core.windows_postgres import')
    assert expected == (router / 'native_postgres.py').read_text()


def test_postgres_durability_and_foreign_pid_checks(tmp_path):
    db = Postgres(tmp_path / 'bin/pg_ctl.exe', tmp_path / 'data', 15432, 'fake', 'fake')
    db.data.mkdir()
    sentinel = db.data / 'postmaster.pid'
    # A live process (this test) with its real start time, but not postgres.exe: refused, never signalled.
    sentinel.write_text(f'{os.getpid()}\n{db.data}\n{int(psutil.Process().create_time())}\n')
    with pytest.raises(lifecycle.LifecycleError, match='ownership mismatch'):
        db.stop()
    assert sentinel.exists()
    with patch.object(db, 'owner', return_value={'pid': 1}), \
            patch('services.shared.native_postgres.ProcessReference') as reference, \
            patch('services.shared.native_postgres.subprocess.run', return_value=Mock(returncode=0)), \
            patch.object(db, 'sql', return_value=json.dumps(dict(
                data_directory=str(db.data), fsync='off', full_page_writes='on', synchronous_commit='on',
                started=0))):
        reference.return_value.__enter__.return_value.alive.return_value = True
        with pytest.raises(lifecycle.LifecycleError, match='durability disabled'):
            db.ready()


def test_real_pinned_tls_health_requires_uuid_and_owned_listener(tmp_path):
    from scripts.lifecycle.test_environment import temporary_lab
    with temporary_lab() as fixtures:
        tls = fixtures.installations[0]['router'] / 'persistent/tls'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        script = tmp_path / 'tls_child.py'
        script.write_text(
            'import ssl\nfrom http.server import HTTPServer, BaseHTTPRequestHandler\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            '    def do_GET(self):\n'
            '        self.send_response(200);self.end_headers();self.wfile.write(b\'{"id":"fixture-id"}\')\n'
            '    def log_message(self, *args): pass\n'
            f'server=HTTPServer(("127.0.0.1",{port}),Handler)\n'
            'ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\n'
            f'ctx.load_cert_chain({str(tls / "certificate.pem")!r},{str(tls / "private-key.pem")!r})\n'
            'server.socket=ctx.wrap_socket(server.socket,server_side=True)\n'
            'try: server.serve_forever(.05)\nfinally: server.server_close()\n', encoding='utf-8')
        with lifecycle.Runtime(tmp_path, 'tls') as runtime:
            child = runtime.launch('app', [sys.executable, str(script)], python=True)
            lifecycle.wait_health(child, port, certificate=tls / 'certificate.pem', identity='fixture-id')
            with pytest.raises(lifecycle.LifecycleError, match='readiness timeout'):
                lifecycle.wait_health(child, port, certificate=tls / 'certificate.pem', identity='wrong-id', timeout=.2)
            assert child.poll() is None


def test_real_redis_controlled_shutdown_preserves_saved_data(tmp_path):
    executable = ROOT / '.local_db/redis/redis-server.exe'
    if os.name != 'nt' or not executable.is_file():
        pytest.skip('Portable Windows Redis unavailable')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with lifecycle.Runtime(tmp_path, 'redis') as runtime:
        child = runtime.launch('redis', [str(executable), '--port', str(port)], cwd=tmp_path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child.child['native'] = {'kind': 'redis', 'port': port}
        wait_until(lambda: lifecycle.owns_listener(child.child['process'], port))
        with socket.create_connection(('127.0.0.1', port), timeout=2) as channel:
            channel.sendall(b'*3\r\n$3\r\nSET\r\n$7\r\nfixture\r\n$5\r\nvalue\r\n')
            assert channel.recv(64).startswith(b'+OK')
        runtime.refresh_children()
    assert (tmp_path / 'dump.rdb').is_file()
    assert not lifecycle.living(child.child['process'])
    with lifecycle.Runtime(tmp_path, 'redis') as runtime:
        reloaded = runtime.launch('redis', [str(executable), '--port', str(port)], cwd=tmp_path,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        reloaded.child['native'] = {'kind': 'redis', 'port': port}
        wait_until(lambda: lifecycle.owns_listener(reloaded.child['process'], port))
        with socket.create_connection(('127.0.0.1', port), timeout=2) as channel:
            channel.sendall(b'*2\r\n$3\r\nGET\r\n$7\r\nfixture\r\n')
            assert b'value' in channel.recv(64)


def test_real_node_uses_graceful_handler_without_console(tmp_path):
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    marker = tmp_path / 'node-stopped'
    script = tmp_path / 'app.cjs'
    script.write_text("const fs=require('node:fs');\n"
        f"process.on('SIGINT',()=>{{fs.writeFileSync({json.dumps(str(marker))},'closed');process.exit(0)}});\n"
        "setInterval(()=>{},100);", encoding='utf-8')
    with lifecycle.Runtime(tmp_path, 'node') as runtime:
        child = runtime.launch('app', [node, str(script)], node=True)
        time.sleep(.3)
    assert marker.read_text() == 'closed'
    assert not lifecycle.living(child.child['process'])


def test_real_isolated_postgres_start_ready_stop_and_foreign_cluster(tmp_path):
    binaries = ROOT / '.local_db/postgres/bin'
    initdb = binaries / 'initdb.exe'
    if os.name != 'nt' or not initdb.exists():
        pytest.skip('Real Windows PostgreSQL binaries unavailable')
    databases = []
    try:
        for label in ('one', 'two'):
            # Each cluster in its own folder, as Hub and Router are: the server log and the lifecycle lock live
            # beside the data directory (two servers appending to one log made the second start fail).
            data = tmp_path / label / 'data'
            data.parent.mkdir()
            log_path = tmp_path / (label + '-initdb.log')
            with log_path.open('wb') as log:
                result = subprocess.run([str(initdb), '-D', str(data), '-U', 'fixture', '--auth=trust', '--locale=C', '-E', 'UTF8'],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=90)
            assert result.returncode == 0, log_path.read_text(errors='replace')[-1000:]
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            db = Postgres(binaries / 'pg_ctl.exe', data, port, 'fixture', 'postgres')
            databases.append(db)
            db.ensure()
            assert db.ready()
        foreign_owner = databases[1].owner()
        databases[0].stop()
        assert databases[1].ready()
        assert databases[1].owner() == foreign_owner
    finally:
        for db in databases:
            db.stop()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership')
def test_real_postgres_ends_with_its_owner_and_an_ownerless_server_is_taken_over(tmp_path):
    binaries = ROOT / '.local_db/postgres/bin'
    if not (binaries / 'initdb.exe').exists():
        pytest.skip('Real Windows PostgreSQL binaries unavailable')
    data = tmp_path / 'cluster/data'
    data.parent.mkdir()
    subprocess.run([str(binaries / 'initdb.exe'), '-D', str(data), '-U', 'fixture', '--auth=trust', '--locale=C',
                    '-E', 'UTF8'], check=True, capture_output=True, timeout=120)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    db = Postgres(binaries / 'pg_ctl.exe', data, port, 'fixture', 'postgres')
    holder = tmp_path / 'holder.py'
    holder.write_text('\n'.join([
        'import time',
        'from services.shared.native_postgres import Postgres',
        f'db = Postgres(r"{binaries / "pg_ctl.exe"}", r"{data}", {port}, "fixture", "postgres")',
        'db.ensure()',
        'print("ready", flush=True)',
        'time.sleep(600)', '']), encoding='utf-8')
    owner = subprocess.Popen([sys.executable, str(holder)], stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, PYTHONPATH=str(ROOT)))
    try:
        assert owner.stdout.readline().strip() == 'ready'
        assert db.owned()  # Another live process owns it: used, never taken over.
        # The server's parent stays alive under its owner (Task Manager shows it there, not on its own).
        holders = {owner.pid} | {child.pid for child in psutil.Process(owner.pid).children()}  # venv redirector
        assert psutil.Process(db.owner()['pid']).parent().parent().pid in holders
        owner.kill()  # Task Manager / a crash: no clean stop runs.
        owner.wait(10)
        wait_until(lambda: db.owner() is None)  # Windows ended the server with its owner.
        db.ensure()  # Crash recovery, then owned by this process.
        assert db.owned()
        db.stop()
        assert db.owner() is None
        # A server started outside any job (an older Orion) has no owner: shut down and started as ours.
        subprocess.run([str(binaries / 'pg_ctl.exe'), 'start', '-D', str(data), '-l', str(data.parent / 'server.log'),
                        '-o', f'-p {port}', '-w', '-t', '30'], check=True, timeout=60,
                       # Never pipes: pg_ctl hands them to the server, and the call would wait for it to exit.
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        leftover = db.owner()
        assert leftover is not None and not db.owned()
        db.ensure()
        assert db.owned() and db.owner()['pid'] != leftover['pid']
    finally:
        if owner.poll() is None:
            owner.kill()
        db.stop()


def test_node_fork_inherits_graceful_control(tmp_path):
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    script = tmp_path / 'fork.cjs'
    script.write_text("const fs=require('node:fs');\n"
        "if(process.argv[2]==='child'){\n"
        " fs.writeFileSync('fork-ready','ready');\n"
        " process.on('SIGINT',()=>{fs.writeFileSync('fork-stopped','closed');process.exit(0)});\n"
        " setInterval(()=>{},100);\n"
        "}else{\n"
        " const child=require('node:child_process').fork(__filename,['child']);\n"
        " process.on('SIGINT',()=>{});child.on('exit',()=>process.exit(0));\n"
        "}\n", encoding='utf-8')
    with lifecycle.Runtime(tmp_path, 'node-fork') as runtime:
        child = runtime.launch('app', [node, str(script)], node=True, cwd=tmp_path)
        wait_until(lambda: (tmp_path / 'fork-ready').is_file())
        runtime.refresh_children()
    assert (tmp_path / 'fork-stopped').read_text() == 'closed'
    assert not lifecycle.living(child.child['process'])


def test_worker_legacy_check_does_not_block_other_installation(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    from orion.kernel import job_safety
    foreign = Mock(pid=99999, info={'cmdline': ['python', '-m', 'orion.worker.main']})
    foreign.cwd.return_value = str(tmp_path)
    redis = Mock(get=AsyncMock(return_value=None), eval=AsyncMock(return_value=1))
    with patch.object(job_safety.psutil, 'process_iter', return_value=[foreign]):
        owner = json.loads(asyncio.run(job_safety.acquire_owner(redis)))
    assert owner['pid'] == os.getpid()


def test_worker_readiness_requires_owned_journal_and_resp2():
    from services.hub import run_local
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    launcher = Mock(pid=100)
    worker = Mock(pid=101)
    worker.create_time.return_value = 12.5
    worker.is_running.return_value = True
    launcher.children.return_value = [worker]
    raw = dict(host=socket.gethostname(), pid=101, created=12.5)
    client.get.return_value = json.dumps(raw)
    with patch('redis.Redis', return_value=client) as factory, patch.object(psutil, 'Process', return_value=launcher):
        assert run_local.worker_ready(launcher, 12345)
        assert factory.call_args.kwargs['protocol'] == 2
        raw['pid'] = 102
        client.get.return_value = json.dumps(raw)
        assert not run_local.worker_ready(launcher, 12345)


def test_real_windows_protected_process_access_is_denied_without_signal():
    if os.name != 'nt':
        pytest.skip('Windows access boundary')
    with pytest.raises(lifecycle.LifecycleError, match='access denied'):
        lifecycle.ProcessReference({'pid': 4})  # System: OpenProcess only, no signal.


def test_fast_parent_exit_does_not_leave_unobserved_grandchild(tmp_path):
    """Acceptance requirement: process-tree ownership must not depend on polling."""
    script = tmp_path / 'fast-parent.py'
    script.write_text('import os,subprocess,sys,json\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"])\n'
        'Path("grandchild.json").write_text(json.dumps({"pid":child.pid}))\n'
        'os._exit(0)\n', encoding='utf-8')
    reference = None
    try:
        with lifecycle.Runtime(tmp_path, 'fast-parent') as runtime:
            parent = runtime.launch('parent', [sys.executable, str(script)], python=True,
                                    cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_until(lambda: (tmp_path / 'grandchild.json').is_file())
            record = lifecycle.ProcessReference.capture(json.loads((tmp_path / 'grandchild.json').read_text())['pid'])
            reference = lifecycle.ProcessReference(record)
            wait_until(lambda: parent.poll() is not None)
            # Intentionally no refresh before parent death: this is the race.
            runtime.refresh_children()
        assert not reference.alive(), 'Unobserved grandchild survived controlled component shutdown'
    finally:
        # The test-created process has a held, validated handle, even on failure.
        if reference is not None:
            try:
                if reference.alive():
                    reference.force()
                    reference.wait(5)
            finally:
                reference.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
@pytest.mark.parametrize('pending', [False, True])
def test_supervisor_crash_ends_its_job_and_stop_reconciles_even_an_incomplete_pid_record(lab, pending):
    script = lab / 'crash-supervisor.py'
    script.write_text('import os,sys,time\nfrom pathlib import Path\n'
        'from services.shared.process_lifecycle import Runtime\n'
        'root=Path(sys.argv[1])\n'
        'with Runtime(root,"job-recovery") as runtime:\n'
        ' child=runtime.launch("app",[sys.executable,str(root/"child.py")],python=True)\n'
        ' if sys.argv[2]=="pending": runtime.children["app"]["process"]=None\n'
        ' runtime.save()\n'
        ' os._exit(17)\n', encoding='utf-8')
    child = subprocess.Popen([sys.executable, str(script), str(lab), 'pending' if pending else 'complete'],
        env=dict(os.environ, PYTHONPATH=str(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert child.wait(20) == 17
        record = state(lab, 'job-recovery')
        # The crashed supervisor held the only job handle: the application ended with it.
        wait_until(lambda: not lifecycle.child_tree_alive(record['children']['app']))
        assert lifecycle.stop_component(lab, 'job-recovery')
        assert not lifecycle.child_tree_alive(record['children']['app'])
        assert state(lab, 'job-recovery')['status'] == 'stopped'
    finally:
        if child.poll() is None:
            child.terminate()  # Exact Popen handle, test-only controller.
            child.wait(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_job_scope_tampering_does_not_stop_another_installation(lab):
    other = lab / 'other-installation'
    with lifecycle.Runtime(other, 'other') as foreign, lifecycle.Runtime(lab, 'own') as own:
        foreign_child = foreign.launch('app', [sys.executable, str(lab / 'child.py')], python=True)
        own_child = own.launch('app', [sys.executable, str(lab / 'child.py')], python=True)
        tampered = own.read()
        tampered['children']['app']['job'] = foreign_child.child['job']
        lifecycle.durable_json(own.path, tampered)
        try:
            with pytest.raises(lifecycle.LifecycleError, match='scope mismatch'):
                lifecycle.stop_component(lab, 'own', timeout=.2)
            assert foreign_child.poll() is None
            assert own_child.poll() is None
        finally:
            own.save()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_job_membership_ignores_reused_historical_descendant_pid(lab):
    with lifecycle.Runtime(lab / 'foreign', 'foreign') as other:
        foreign = other.launch('app', [sys.executable, str(lab / 'child.py')], python=True)
        record = foreign.child['process']
        with lifecycle.Runtime(lab, 'own') as own:
            child = own.launch('app', [sys.executable, str(lab / 'child.py')], python=True)
            child.child['descendants'] = [{**record, 'started': record['started'] - 10,
                                           'native_started': record['native_started'] - 100000000}]
            own.save()
            child.stop(.5)
            assert foreign.poll() is None
        assert foreign.poll() is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_missing_job_never_runs_bootstrap_application(tmp_path):
    host = ROOT / 'services/shared/process_host.py'
    marker = tmp_path / 'must-not-run'
    result = subprocess.run([sys._base_executable, '-I', '-S', str(host),
        'Local\\Orion-Job-missing-' + __import__('uuid').uuid4().hex,
        str(tmp_path / 'cancel'), sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
        capture_output=True, timeout=10)
    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_failed_root_record_save_leaves_no_unrecorded_application(lab):
    with lifecycle.Runtime(lab, 'failed-save') as runtime:
        save = runtime.save
        writes = 0
        def fail_second_save():
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('fixture state write failure')
            save()
        with patch.object(runtime, 'save', side_effect=fail_second_save):
            with pytest.raises(OSError, match='state write failure'):
                runtime.launch('app', [sys.executable, str(lab / 'child.py')], python=True)
        assert runtime.read()['children']['app']['process'] is None
        assert not lifecycle.child_tree_alive(runtime.children['app'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_canceled_pending_host_cannot_start_application_later(lab):
    import uuid
    with lifecycle.Runtime(lab, 'pending-cancel') as runtime:
        instance_id = str(uuid.uuid4())
        metadata = lifecycle.job_adapter().metadata(lab, 'pending-cancel', runtime.identity['id'], instance_id)
        creator = lifecycle.job_adapter().Job(metadata, create=True)
        runtime.children['app'] = dict(process=None, instance_id=instance_id, job=metadata,
                                       control=None, entrypoint='fixture', target_hash='fixture')
        runtime.save()
        script = lab / 'paused-host.py'
        script.write_text('import ctypes,sys,time\nfrom ctypes import wintypes as W\nfrom pathlib import Path\n'
            'kernel=ctypes.WinDLL("kernel32",use_last_error=True)\n'
            'kernel.OpenJobObjectW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR]\n'
            'kernel.OpenJobObjectW.restype=W.HANDLE\n'
            'kernel.CloseHandle.argtypes=[W.HANDLE]\n'
            'handle=kernel.OpenJobObjectW(1,False,sys.argv[1])\nassert handle\n'
            'Path("opened").touch()\n'
            'while not Path("resume").exists(): time.sleep(.01)\n'
            f'sys.path.insert(0,{str(ROOT / "services/shared")!r})\n'
            'from process_host import main\n'
            'try:\n'
            ' sys.argv=["process_host.py",sys.argv[1],sys.argv[2],sys.executable,"-c",'
            '"from pathlib import Path; Path(\'must-not-run\').touch()"]\n'
            ' main()\n'
            'finally: kernel.CloseHandle(handle)\n', encoding='utf-8')
        paused = subprocess.Popen([sys._base_executable, '-I', '-S', str(script), metadata['name'],
                                   str(creator.cancel_path)], cwd=lab,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_until(lambda: (lab / 'opened').exists())
            lifecycle.ManagedChild(runtime.children['app']).stop(.1)
            (lab / 'resume').touch()
            paused.wait(10)
            assert not (lab / 'must-not-run').exists()
        finally:
            if paused.poll() is None:
                paused.terminate()
                paused.wait(5)
            creator.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership adapter')
def test_losing_both_controllers_cannot_strand_grandchild(lab):
    (lab / 'orphan-parent.py').write_text('import subprocess,sys,json\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"])\n'
        'Path("orphan.json").write_text(json.dumps({"pid":child.pid}))\n', encoding='utf-8')
    script = lab / 'both-controllers.py'
    script.write_text('import os,sys,time\nfrom pathlib import Path\n'
        'from services.shared.process_lifecycle import Runtime\n'
        'root=Path(sys.argv[1])\n'
        'with Runtime(root,"both-controllers") as runtime:\n'
        ' child=runtime.launch("app",[sys.executable,str(root/"orphan-parent.py")],cwd=root,python=True)\n'
        ' while child.poll() is None: time.sleep(.01)\n'
        ' (root/"ready").touch()\n'
        ' while not (root/"exit").exists(): time.sleep(.01)\n'
        ' os._exit(17)\n', encoding='utf-8')
    controller = subprocess.Popen([sys.executable, str(script), str(lab)],
        env=dict(os.environ, PYTHONPATH=str(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reference = None
    try:
        wait_until(lambda: (lab / 'ready').exists())
        reference = lifecycle.ProcessReference(lifecycle.ProcessReference.capture(
            json.loads((lab / 'orphan.json').read_text())['pid']))
        (lab / 'exit').touch()
        assert controller.wait(10) == 17
        reference.wait(5)
        assert lifecycle.stop_component(lab, 'both-controllers')
    finally:
        (lab / 'exit').touch()
        if reference:
            if reference.alive():
                reference.force()
                reference.wait(5)
            reference.close()
        if controller.poll() is None:
            controller.terminate()
            controller.wait(5)


def test_postgres_role_without_settings_privilege_is_identified_by_start_time(tmp_path):
    # Older developer databases: the `orion` role may not read data_directory (pg_read_all_settings), which
    # pg_settings then leaves out.
    db = Postgres(tmp_path / 'bin/pg_ctl.exe', tmp_path / 'data', 15432, 'fake', 'fake')
    db.data.mkdir()
    (db.data / 'postmaster.pid').write_text(f'1\n{db.data}\n1792370309\n5445\n')
    def settings(directory, started):
        return json.dumps(dict(data_directory=directory, fsync='on', full_page_writes='on',
                               synchronous_commit='on', started=started))
    with patch.object(db, 'sql', return_value=settings(None, 1792370309)):
        assert db.same_cluster()
    with patch.object(db, 'sql', return_value=settings(None, 1792000000)):
        assert not db.same_cluster()   # another cluster on this port
    with patch.object(db, 'sql', return_value=settings(str(tmp_path / 'other'), 1792370309)):
        assert not db.same_cluster()   # a readable data_directory decides, whatever the start time
