"""Restart/fault tests in disposable packaged installations; no real user data."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import asyncio
import subprocess
import sys
import zipfile

import pytest

from test_release_packages import repo, publisher
from services.shared.installation_registry import Registry, component_record, new_registry
from services.shared.release_packages import record_prepared_package, sha256, verify_package_record
from services.shared.update_helper import UpdateHelper, UpdateError
from services.shared.update_gate import blocked, UpdateAdmission
from services.shared import update_log


class Crash(BaseException):
    pass


class Host:
    idle = True
    healthy = True
    stopped_calls = 0

    @contextmanager
    def stopped(self, record):
        yield self.idle

    def stop(self, record):
        self.stopped_calls += 1

    def health(self, record):
        return {k: self.healthy for k in ('hub_api', 'worker', 'database', 'client_api', 'router_identity')}

    def recovery_health(self, record):
        return True


@pytest.fixture
def installed(repo, monkeypatch):
    base, output, name, signers, bootstrap, artifacts, fetcher, client = repo
    store = Registry(base / 'install.json')
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(store.path))
    verified = client('install-cache')
    _, targets = verified.check([name])
    target = targets[name]
    tree = verified.prepare(target, base / 'Hub/releases' / target.custom['package_id'])
    data, state = base / 'Hub/data', base / 'Hub/state'
    data.mkdir()
    state.mkdir()
    (data / 'identity.manual').write_bytes(b'encrypted preference sentinel')
    item = component_record('hub', tree, distribution='packaged', data_path=data, state_path=state,
                            ownership='owned', management='local', package=record_prepared_package(tree, state, target))
    store.write(new_registry(base, [item], preferences={'automatic_updates': False}))
    old = store.read()
    release = deepcopy(target.custom)
    release.update(sequence=release['sequence'] + 1, version='9.0.1', package_id='orion-hub-9.0.1-windows-x86_64-cpu')
    release['capabilities'] = list(set(release['capabilities']) | {'lifecycle.update-quarantine.v1'})
    updated = artifacts / (release['package_id'] + '.zip')
    with zipfile.ZipFile(updated, 'w') as archive:
        archive.writestr('orion-release.json', json.dumps(release))
        archive.writestr(release['entrypoint'], 'candidate, never executed by the fake host')
    description = {'path': updated.name, 'length': updated.stat().st_size,
                   'hashes': {'sha256': sha256(updated)}, 'custom': release}
    (artifacts / 'hub.target.json').write_text(json.dumps(description))
    fetcher.directory = publisher.publish(artifacts, base / 'new-repository', bootstrap, signers, version=2)
    host = Host()
    helper = UpdateHelper(store, host=host)
    update_client = client('update-cache')
    # python-tuf Updater refresh is one-shot per instance; a restarted CLI
    # creates a new client while preserving the on-disk trust/high-water cache.
    update_client.fresh = lambda: client('update-cache')
    return helper, update_client, updated.name, old, data, host


def test_success_keeps_identity_data_preferences_old_code_and_backup(installed):
    helper, client, name, old, data, host = installed
    staged = helper.prepare(client, [name])
    assert staged['phase'] == 'staged'
    assert helper.registry.read() == old
    assert not blocked(helper.registry.path)
    result = helper.resume()
    assert result['phase'] == 'completed', result['reason']
    assert helper.registry.read() == result['proposed']
    assert helper.registry.read()['preferences'] == old['preferences']
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'
    assert Path(old['components'][0]['code_path']).is_dir()
    assert not blocked(helper.registry.path)
    verify_package_record(helper.registry.read()['components'][0])
    helper._verify_backups(result)
    assert helper.resume() == result


def test_running_install_waits_and_cannot_start_second_update(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    host.idle = False
    assert helper.resume()['phase'] == 'waiting_for_idle'
    assert helper.registry.read() == old
    assert not blocked(helper.registry.path)
    assert host.stopped_calls == 0
    with pytest.raises(UpdateError, match='pending'):
        helper.prepare(client, [name])
    host.idle = True
    assert helper.resume()['phase'] == 'completed'


ACTIVATION_WRITES = [
    'journal:draining', 'journal:backing_up', 'backups', 'journal:switching',
    'inventory:hub', 'registry', 'journal:health_check', 'journal:completed',
]


@pytest.mark.parametrize('label', [side + ':' + name for name in ACTIVATION_WRITES for side in ('before', 'after')]
                         + ['after:backup:hub'])
def test_restart_at_every_activation_durable_boundary(installed, label):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    def die(point):
        if point == label:
            raise Crash(point)
    helper.fault = die
    with pytest.raises(Crash):
        helper.resume()
    # A new helper object represents a fresh process; all recovery information
    # must come from disk, never the previous Python object's mutated dictionary.
    recovered = UpdateHelper(helper.registry, host=Host()).resume()
    assert recovered['phase'] == 'completed'
    verify_package_record(helper.registry.read()['components'][0])
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


@pytest.mark.parametrize('phase', ['checking', 'downloading', 'verifying', 'staged'])
@pytest.mark.parametrize('side', ['before', 'after'])
def test_prepare_crash_never_changes_active_release(installed, phase, side):
    helper, client, name, old, data, host = installed
    def die(point):
        if point == side + ':journal:' + phase:
            raise Crash(point)
    helper.fault = die
    with pytest.raises(Crash):
        helper.prepare(client, [name])
    helper = UpdateHelper(helper.registry, host=Host())
    tx = helper.status()
    if tx:
        tx = helper.resume()
        assert tx['phase'] in {'failed', 'completed'}
    if not tx or tx['phase'] == 'failed':
        assert helper.registry.read() == old
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


def test_failed_health_rolls_code_back_without_restoring_data(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    def health(record):
        (data / 'new-write').write_text('must survive rollback')
        raise TimeoutError('health timeout')
    host.health = health
    tx = helper.resume()
    assert tx['phase'] == 'rolled_back'
    assert helper.registry.read() == old
    assert (data / 'new-write').read_text() == 'must survive rollback'
    verify_package_record(old['components'][0])


@pytest.mark.parametrize('failure', ['disk', 'locked-file', 'corrupt-backup', 'broken-candidate'])
def test_failures_preserve_old_registry_and_data(installed, failure, monkeypatch):
    helper, client, name, old, data, host = installed
    staged = helper.prepare(client, [name])
    if failure == 'disk':
        monkeypatch.setattr('services.shared.update_helper.shutil.disk_usage', lambda p: type('Disk', (), {'free': 0})())
    elif failure == 'locked-file':
        monkeypatch.setattr('services.shared.update_helper._copy_file', lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    elif failure == 'corrupt-backup':
        # A restarted helper re-proves the backup in full before it switches anything.
        def die(point):
            if point == 'after:journal:switching':
                raise Crash(point)
        helper.fault = die
        with pytest.raises(Crash):
            helper.resume()
        folder = next(helper.base.glob('*/backup-hub-*'))
        (folder / 'identity.manual').write_bytes(b'corruption')
        helper = UpdateHelper(helper.registry, host=Host())
    else:
        (Path(staged['proposed']['components'][0]['code_path']) / 'orion-release.json').write_bytes(b'broken')
    assert helper.resume()['phase'] in {'failed', 'rolled_back'}
    assert helper.registry.read() == old
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


def test_rollback_failure_keeps_admission_closed_and_repair_accessible(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    host.healthy = False
    original = helper._publish
    def publish(tx, record, candidate):
        if not candidate:
            raise PermissionError('locked old inventory')
        return original(tx, record, candidate)
    helper._publish = publish
    tx = helper.resume()
    assert tx['phase'] == 'repair_required'
    assert blocked(helper.registry.path)
    assert UpdateHelper(helper.registry, host=host).status()['operation_id'] == tx['operation_id']
    assert helper.resume()['phase'] == 'repair_required'


@pytest.mark.parametrize('mutation', ['schema', 'sequence', 'kind', 'capability', 'api'])
def test_rejects_unsafe_release_before_preparation(installed, mutation):
    helper, client, name, old, data, host = installed
    original = client.check
    def check(names):
        status, targets = original(names)
        manifest = targets[name].unrecognized_fields['custom']
        if mutation == 'schema':
            manifest['data_schema'] += 1
        elif mutation == 'sequence':
            manifest['sequence'] = 1
        elif mutation == 'kind':
            manifest['component'] = 'router'
        elif mutation == 'capability':
            manifest['capabilities'] = []
        else:
            manifest['requires']['mobile'] = [2, 2]
        return status, targets
    client.check = check
    with pytest.raises((ValueError, KeyError)):
        helper.prepare(client, [name])
    assert helper.registry.read() == old


def test_foreign_registry_change_needs_repair(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    helper.registry.update(lambda r: {**r, 'root': str(Path(r['root']) / 'foreign-root')})
    assert helper.resume()['phase'] == 'repair_required'
    assert blocked(helper.registry.path)


def test_unknown_journal_and_path_traversal_fail_closed(installed):
    helper, client, name, old, data, host = installed
    (helper.base / 'active.json').write_text(json.dumps({'schema': 1, 'operation_id': '../outside'}))
    with pytest.raises(UpdateError):
        helper.status()
    assert blocked(helper.registry.path)


def test_bad_candidate_during_busy_use_does_not_stop_old_hub(installed):
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    host.idle = False
    (Path(tx['proposed']['components'][0]['code_path']) / 'orion-release.json').write_bytes(b'corrupt')
    assert helper.resume()['phase'] == 'failed'
    assert host.stopped_calls == 0
    assert helper.registry.read() == old


def test_cancel_staging_does_not_stop_old_hub(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    host.idle = False
    assert helper.rollback()['phase'] == 'failed'
    assert host.stopped_calls == 0
    assert helper.registry.read() == old


def test_manager_native_start_respects_durable_update_gate(installed, monkeypatch):
    from services.shared.native_services import start_hub
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    helper._phase(tx, 'health_check')
    with pytest.raises(RuntimeError, match='recovery'):
        start_hub(old['components'][0]['code_path'])


def test_health_retry_budget_is_durable(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    def crash(phase):
        if phase == 'after:journal:health_check':
            raise Crash()
    helper.fault = crash
    with pytest.raises(Crash):
        helper.resume()
    helper = UpdateHelper(helper.registry, host=host, fault=crash)
    with pytest.raises(Crash):
        helper.resume()
    tx = UpdateHelper(helper.registry, host=host).resume()
    assert tx['phase'] == 'rolled_back'
    assert tx['attempts'] == 2
    assert helper.registry.read() == old


def test_db_schema_error_after_code_rollback_requires_repair(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    host.healthy = False
    host.recovery_health = lambda record: False
    tx = helper.resume()
    assert tx['phase'] == 'repair_required'
    assert helper.registry.read() == old
    assert blocked(helper.registry.path)
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


def test_cli_accepts_documented_target_after_repository_flags(installed, monkeypatch, capsys):
    import update_local
    helper, client, name, old, data, host = installed
    monkeypatch.setattr(update_local, 'UpdateHelper', lambda registry: helper)
    monkeypatch.setattr(update_local, 'ReleaseClient', lambda **kwargs: client)
    assert update_local.main(['--registry', str(helper.registry.path), 'prepare',
        '--metadata-url', 'https://release.example/metadata/',
        '--targets-url', 'https://release.example/targets/', name]) == 0
    assert json.loads(capsys.readouterr().out)['phase'] == 'staged'


def test_future_journal_schema_cannot_open_admission(installed):
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    tx.update(schema=2, phase='completed')
    helper._json(helper._directory(tx) / 'journal.json', tx, 'future-schema-fixture')
    assert blocked(helper.registry.path)
    with pytest.raises(UpdateError):
        helper.status()


@pytest.mark.parametrize('phase', ['waiting_for_idle', 'rollback_pending', 'rolled_back', 'repair_required'])
@pytest.mark.parametrize('side', ['before', 'after'])
def test_restart_at_wait_rollback_and_repair_journal_boundaries(installed, phase, side):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    if phase == 'waiting_for_idle':
        host.idle = False
    else:
        host.healthy = False
    if phase == 'repair_required':
        publish = helper._publish
        def fail_old(tx, record, candidate):
            if not candidate:
                raise PermissionError('old inventory locked')
            return publish(tx, record, candidate)
        helper._publish = fail_old
    def die(point):
        if point == side + ':journal:' + phase:
            raise Crash(point)
    helper.fault = die
    with pytest.raises(Crash):
        helper.resume()
    resumed = UpdateHelper(helper.registry, host=host).resume()
    if phase == 'waiting_for_idle':
        assert resumed['phase'] == 'waiting_for_idle'
        assert helper.registry.read() == old
    else:
        assert resumed['phase'] in {'rolled_back', 'repair_required'}
        if resumed['phase'] == 'rolled_back':
            assert helper.registry.read() == old
        else:
            assert blocked(helper.registry.path)
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


@pytest.mark.parametrize('interruption', ['after-extraction', 'after-rollback'])
def test_same_target_can_be_prepared_again_without_patching_orphan_release(installed, interruption):
    helper, client, name, old, data, host = installed
    if interruption == 'after-extraction':
        def crash(point):
            if point == 'before:candidate:hub':  # The tree is extracted and hashed.
                raise Crash()
        helper.fault = crash
        with pytest.raises(Crash):
            helper.prepare(client, [name])
        helper = UpdateHelper(helper.registry, host=host)
        assert helper.resume()['phase'] == 'failed'
    else:
        helper.prepare(client, [name])
        host.healthy = False
        assert helper.resume()['phase'] == 'rolled_back'
        host.healthy = True
    assert helper.registry.read() == old
    assert helper.prepare(client.fresh(), [name])['phase'] == 'staged'
    assert helper.resume()['phase'] == 'completed'
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


def test_stopped_marker_waits_for_exact_supervisor_exit_before_reconciliation():
    from services.shared.process_lifecycle import ProcessReference
    from services.shared.update_helper import stop_and_wait
    child = subprocess.Popen([sys.executable, '-B', '-c',
        "import sys,time; print('stopped',flush=True); time.sleep(.4)"], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'stopped'
        record = ProcessReference.capture(child.pid)
        assert child.poll() is None
        stop_and_wait({'supervisor': record}, lambda: None)
        assert child.poll() == 0
    finally:
        child.wait(timeout=5)


def test_released_runtime_does_not_wait_on_a_historic_supervisor_pid(installed):
    from services.shared.process_lifecycle import Runtime
    from services.shared.update_helper import StoppedPackages, stop_and_wait
    helper, client, name, old, data, host = installed
    with Runtime(old['components'][0]['code_path'], 'hub'):
        pass
    stop_and_wait(Runtime(old['components'][0]['code_path'], 'hub').read(), lambda: None)
    # This test process still lives, but no longer owns that Runtime. Held OS
    # locks, stopped children/DB and root scanning are the authority for update.
    with StoppedPackages(old) as stopped:
        assert stopped


def test_http_and_websocket_admission_during_health(installed):
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    helper._phase(tx, 'health_check')
    called, messages = [], []
    async def app(scope, receive, send):
        called.append(scope['path'])
    async def send(message):
        messages.append(message)
    boundary = UpdateAdmission(app)
    asyncio.run(boundary({'type': 'http', 'path': '/api/v1/chats/messages', 'method': 'POST'}, None, send))
    assert messages[0]['status'] == 503
    asyncio.run(boundary({'type': 'websocket', 'path': '/ws'}, None, send))
    assert messages[-1]['code'] == 1013
    asyncio.run(boundary({'type': 'http', 'path': '/health', 'method': 'GET'}, None, send))
    assert called == ['/health']


@pytest.mark.parametrize('label', [side + ':' + name for name in ACTIVATION_WRITES for side in ('before', 'after')])
def test_actual_helper_process_exit_releases_os_lock_and_recovers(installed, label):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    program = '''import os, sys
from contextlib import contextmanager
from services.shared.update_helper import UpdateHelper
class Host:
    @contextmanager
    def stopped(self, record):
        yield True
    def stop(self, record):
        pass
    def health(self, record):
        return dict(hub_api=True,worker=True,database=True,client_api=True,router_identity=True)
    def recovery_health(self, record):
        return True
def die(label):
    if label == sys.argv[2]:
        os._exit(73)
UpdateHelper(sys.argv[1],host=Host(),fault=die).resume()
'''
    result = subprocess.run([sys.executable, '-B', '-c', program, str(helper.registry.path), label], timeout=30)
    assert result.returncode == 73
    assert UpdateHelper(helper.registry, host=Host()).resume()['phase'] == 'completed'
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'


def test_update_uses_same_os_operation_lock_as_launcher_and_migration(installed):
    helper, client, name, old, data, host = installed
    program = '''import sys
from services.shared.installation_registry import operation_lock
with operation_lock(sys.argv[1]):
    print('locked',flush=True)
    sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, '-B', '-c', program, str(helper.registry.path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(RuntimeError, match='busy|inaccessible'):
            helper.prepare(client, [name])
        assert helper.registry.read() == old
    finally:
        child.communicate('\n', timeout=15)


def test_health_probe_uses_the_launcher_default_flutter_port(monkeypatch):
    """Regression: a default install has no ORION_FLUTTER_PORT and the probe used to crash."""
    from services.shared import update_health
    monkeypatch.delenv('ORION_FLUTTER_PORT', raising=False)
    assert update_health.flutter_port() == 8090
    monkeypatch.setenv('ORION_FLUTTER_PORT', '18090')
    assert update_health.flutter_port() == 18090


def test_success_records_timings_and_determinate_progress(installed):
    helper, client, name, old, data, host = installed
    helper.prepare(client, [name])
    seen = []
    original = helper._json
    def spy(path, value, label):
        if path.name == 'journal.json' and value['phase'] in {'draining', 'backing_up', 'switching', 'health_check'}:
            seen.append(value['progress']['percent'])
        return original(path, value, label)
    helper._json = spy
    result = helper.resume()
    assert result['phase'] == 'completed'
    assert {'verify.start', 'backup.copy.hub', 'backup.hash-source.hub', 'switch', 'health.probe',
            'verify.final'} <= set(result['timings'])
    assert seen == sorted(seen) and seen[0] >= 8 and seen[-1] <= 95
    assert 'update.timing' in chr(10).join(update_log.read_tail(helper.base, 100))


def test_backup_covers_only_changed_components_unless_hub_changes():
    def tx(changes):
        return {'changes': changes, 'previous': {'components': [{'kind': k} for k in ('hub', 'client', 'router')]}}
    assert UpdateHelper.backup_kinds(tx(['client', 'router'])) == ['client', 'router']
    assert UpdateHelper.backup_kinds(tx(['router'])) == ['router']
    assert UpdateHelper.backup_kinds(tx(['hub'])) == ['hub', 'client', 'router']
    assert UpdateHelper.backup_kinds(tx([])) == ['hub', 'client', 'router']
    assert UpdateHelper.backup_kinds({**tx(['router']), 'conversion': True}) == ['hub', 'client', 'router']


def test_progress_percent_is_monotonic_across_phases():
    from services.shared.update_helper import percent
    values = [percent('draining', 0, 1), percent('backing_up', 0, 9), percent('backing_up', 9, 9),
              percent('switching', 1, 2), percent('health_check', 0, 3), percent('health_check', 3, 3)]
    assert values == sorted(values) and values[-1] == 95


def test_unchanged_component_is_not_fully_rehashed(installed, monkeypatch):
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    calls = []
    import services.shared.update_helper as module
    real = module.verify_package_record
    monkeypatch.setattr(module, 'verify_package_record', lambda item, **kw: calls.append(kw) or real(item, **kw))
    helper._fresh()
    helper._verify(tx, tx['proposed'], True, quick=True)
    helper._verify(tx, tx['proposed'], True, quick=True)  # Memoized: no second hash in the same window.
    assert len(calls) == 1



@pytest.mark.parametrize('case', ['straight', 'interrupted', 'tampered'])
def test_straight_apply_trusts_the_staging_hash_resume_and_tampering_do_not(installed, monkeypatch, case):
    helper, client, name, old, data, host = installed
    tx = helper.prepare(client, [name])
    import services.shared.release_packages as packages
    real, hashed = packages.sha256, []
    monkeypatch.setattr(packages, 'sha256', lambda path: hashed.append(Path(path)) or real(path))
    releases = Path(old['components'][0]['code_path']).parent
    if case == 'tampered':
        # Changed after staging (size differs): caught before anything switches.
        tree = Path(tx['proposed']['components'][0]['code_path'])
        entry = next(p for p in tree.rglob('*') if p.is_file() and p.name != 'orion-release.json')
        entry.write_bytes(entry.read_bytes() + b'!')
        assert helper.resume()['phase'] == 'failed'
        return
    if case == 'interrupted':
        def die(point):
            if point == 'after:backup:hub':
                raise Crash(point)
        helper.fault = die
        with pytest.raises(Crash):
            helper.resume()
        hashed.clear()
        helper = UpdateHelper(helper.registry, host=Host())
    assert helper.resume()['phase'] == 'completed'
    code = [p for p in hashed if p.is_relative_to(releases)]
    # Straight from staging the trees' size+mtime cache is enough; a resumed run hashes every file.
    assert bool(code) == (case == 'interrupted')
