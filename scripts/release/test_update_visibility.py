"""Diagnosable and visible updates: watcher.log, Turkish reasons, check states, one transaction for all
components, the automatic apply at launch."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_automatic_updates import automatic  # noqa: F401  (fixtures below)
from test_update_helper import installed  # noqa: F401
from test_release_packages import repo  # noqa: F401
from services.shared import update_log, update_ui
from services.shared.automatic_updates import AutomaticUpdates, watch
from services.shared.installation_registry import Registry, component_record, new_registry
from services.shared.process_lifecycle import LifecycleError


# ---- B: private, size-limited log without secrets ------------------------------------------------

def test_log_hides_urls_paths_and_tokens_and_is_rotated(tmp_path):
    update_log.log(tmp_path, 'check', detail='GET https://user:pw@release.example/metadata?token=abc failed in '
                   'C:\\Users\\kisi\\AppData\\Local\\Orion\\x.json key=0123456789abcdef0123456789abcdef0123')
    text = (tmp_path / update_log.LOG_NAME).read_text(encoding='utf-8')
    for secret in ('release.example', 'user:pw', 'token=abc', 'kisi', 'AppData', '0123456789abcdef0123456789abcdef'):
        assert secret not in text
    assert '<url>' in text and '<path>' in text
    for number in range(4000):
        update_log.log(tmp_path, 'filler', n=number, pad='x' * 100)
    sizes = [p.stat().st_size for p in tmp_path.glob('watcher.log*')]
    assert len(sizes) <= 1 + update_log.BACKUPS
    assert all(size <= update_log.MAX_BYTES + 1024 for size in sizes)


def test_exception_log_keeps_frames_but_no_message_secrets(tmp_path):
    try:
        raise RuntimeError('boom https://secret.example/x C:\\Users\\me\\f')
    except RuntimeError as error:
        update_log.log_exception(tmp_path, 'where', error)
    line = (tmp_path / update_log.LOG_NAME).read_text(encoding='utf-8')
    assert 'RuntimeError' in line and 'test_update_visibility.py' in line
    assert 'secret.example' not in line and 'Users' not in line


def test_turkish_reasons_for_common_codes_and_a_safe_fallback():
    codes = ('OperationBusy', 'CheckProcessFailed', 'BootstrapIncompatible', 'URLError', 'ServicesStillRunning',
             'StagedUpdateChanged', 'RuntimeError', 'UpdateError', 'rolled_back')
    texts = [update_log.describe(code) for code in codes]
    assert all(texts) and len(set(texts)) == len(texts)  # Each code has its own explanation.
    assert not any(code in text for code, text in zip(codes, texts) if code.isalpha() and code[0].isupper())
    assert update_log.describe(None) is None
    unknown = update_log.describe('SomethingNew https://x.example/secret')
    assert 'x.example' not in unknown and 'watcher.log' in unknown


def test_watcher_survives_a_busy_check_lock_and_logs_other_errors(automatic):
    """Regression: a manual check holds the check lock for minutes; the watcher's own poll hit it, got
    LifecycleError and the watcher exited silently (so "Şimdi güncelle" found no watcher)."""
    service, now, data, host = automatic
    host.idle = False
    runs = []

    def check(*args, **kwargs):
        runs.append(1)
        if len(runs) == 1:
            raise LifecycleError('Lifecycle busy or mutex inaccessible')
        if len(runs) == 2:
            raise RuntimeError('network died https://release.example/m C:\\Users\\kisi\\x')

    def sleep(seconds):
        if len(runs) >= 3:
            host.idle = True
    with patch.object(service, 'check', check), patch('services.shared.automatic_updates.time.sleep', side_effect=sleep):
        watch(service)
    assert len(runs) >= 3  # It kept polling through both failures.
    log = (service.base / update_log.LOG_NAME).read_text(encoding='utf-8')
    assert 'watcher.start' in log and 'watch.check' in log and 'RuntimeError' in log
    assert 'release.example' not in log and 'kisi' not in log
    assert log.count('LifecycleError') == 0  # Busy is normal, not an error line.


# ---- D: clear states after refresh ---------------------------------------------------------------

def test_refresh_outcomes_are_always_reported(automatic):
    service, now, data, host = automatic
    service.preferences(enabled=False)
    service.check(now=True)
    assert service.status()['check_outcome'] == 'disabled'
    service.preferences(enabled=True)
    service.check(now=True)
    status = service.status()
    assert status['check_outcome'] == 'update_found' and status['checking'] is False
    assert status['pending']['progress'] == {'done': 1, 'total': 1, 'component': None}
    service.check(now=True)
    assert service.status()['check_outcome'] == 'pending'
    service.helper.resume(automatic=False)
    now[0] += 10
    service.check(now=True)
    assert service.status()['check_outcome'] == 'up_to_date'


def test_download_progress_is_in_the_journal(automatic):
    service, now, data, host = automatic
    seen = []
    original = service.helper._phase

    def spy(tx, phase, **fields):
        if phase == 'downloading':
            seen.append(fields.get('progress'))
        return original(tx, phase, **fields)
    service.helper._phase = spy
    service.check()
    first, *rest = seen
    assert first['done'] == 0 and first['total'] == 1 and first['component'] == 'hub' and first['bytes'] == 0
    assert first['bytes_total'] > 0
    assert rest and rest[-1]['bytes'] == first['bytes_total']  # Byte progress reached the end of the download.
    assert all(a['bytes'] <= b['bytes'] for a, b in zip(seen, seen[1:]))


def test_failed_check_says_why_in_turkish(automatic):
    service, now, data, host = automatic
    client = service.client_factory()
    fetcher = client.updater._fetcher
    fetcher.offline = True
    service.client_factory = lambda: client
    service.check(now=True)
    status = service.status()
    assert status['error'] and status['pending'] is None and status['checking'] is False
    assert 'Güncelleme' in status['error_message']
    assert status['check_outcome'] is None


def test_running_check_is_reported_as_checking_and_stale_markers_are_not(automatic):
    service, now, data, host = automatic
    state = {'schema': 1, 'last_attempt': now[0], 'running_pid': os.getpid()}
    service.state_path.write_text(json.dumps(state))
    assert service.status()['checking'] is True
    state['running_pid'] = 2 ** 22 + 12345  # No such process: a crashed check.
    service.state_path.write_text(json.dumps(state))
    assert service.status()['checking'] is False
    state.update(running_pid=os.getpid(), last_attempt=now[0] - 3600)
    service.state_path.write_text(json.dumps(state))
    assert service.status()['checking'] is False


# ---- E: automatic apply at launch shows the window and records the outcome ------------------------

def test_launch_time_apply_opens_the_window_and_writes_the_result(automatic):
    service, now, data, host = automatic
    service.check()
    events = []
    from contextlib import contextmanager

    @contextmanager
    def window(registry, **kwargs):
        events.append('open')
        yield
        events.append('close')
    service.preferences(enabled=True)
    with patch.object(update_ui, 'updating_window', window):
        assert service.startup()['phase'] == 'completed'
    assert events == ['open', 'close']
    result = update_ui.read_result(service.registry)
    assert result['status'] == 'completed' and result['message'].startswith('Güncellendi: hub 9.0.1')


class PowerCut(BaseException):
    """Not an Exception: nothing in the helper may catch it, exactly like a killed process."""


@pytest.mark.parametrize('cut', ['after:journal:backing_up', 'after:journal:switching', 'after:registry',
                                 'after:journal:health_check'])
def test_an_apply_cut_off_mid_way_is_finished_by_the_next_launch_not_waited_for(automatic, cut):
    """Power loss / Task Manager during the switch: the next launch must neither wait 30 minutes for an
    applier that is gone nor refuse to start; it resumes (or rolls back) at once, even with updates off."""
    from services.shared import launcher
    service, now, data, host = automatic
    service.check()
    def fault(label):
        if label == cut:
            raise PowerCut(label)
    service.helper.fault = fault
    entry = service.registry.path.parent / 'bootstrap/Orion.ps1'
    entry.write_text('# an older Hub launch rewrote the entry with its own copy', encoding='utf-8')
    with pytest.raises(PowerCut):
        service.helper.resume()
    service.helper.fault = lambda label: None
    # The entry that the next launch runs is the helper's own, with the recovery step (any Hub version).
    assert 'Invoke-OrionRecovery' in entry.read_text(encoding='utf-8')
    assert service.helper.status()['phase'] not in {'completed', 'rolled_back', 'failed'}
    service.preferences(enabled=False)
    sleeps = []
    assert launcher.wait_for_update(service.registry, sleep=sleeps.append) is False and sum(sleeps) < 2
    result = service.startup()
    assert result['phase'] in {'completed', 'rolled_back'}, result.get('reason')
    assert update_ui.read_result(service.registry)['status'] == result['phase']


def test_launch_time_apply_that_rolls_back_says_so(automatic):
    service, now, data, host = automatic
    service.check()
    host.healthy = False
    assert service.startup()['phase'] == 'rolled_back'
    result = update_ui.read_result(service.registry)
    assert result['status'] == 'rolled_back' and 'önceki sürümle devam ediliyor' in result['message']


def test_deferred_update_is_not_applied_and_shows_no_window(automatic):
    service, now, data, host = automatic
    service.check()
    service.preferences(defer_hours=24)
    with patch.object(update_ui, 'updating_window', side_effect=AssertionError('no window')):
        assert service.startup()['phase'] == 'staged'
    assert update_ui.read_result(service.registry) is None


# ---- C: one transaction for every component with a newer signed sequence -------------------------

def _install(tmp_path, kinds, *, router_enabled=True):
    components = []
    for kind in kinds:
        code = tmp_path / kind / 'releases' / f'orion-{kind}-1.0.0-x'
        code.mkdir(parents=True)
        (code / 'orion-release.json').write_text(json.dumps({'sequence': 1}))
        data, state = tmp_path / kind / 'data', tmp_path / kind / 'state'
        data.mkdir(), state.mkdir()
        components.append(component_record(kind, code, distribution='packaged', data_path=data, state_path=state,
                                           ownership='owned', management='local'))
    registry = Registry(tmp_path / 'install.json')
    registry.write(new_registry(tmp_path, components, preferences={'automatic_updates': True}))
    (tmp_path / 'bootstrap').mkdir()
    (tmp_path / 'bootstrap/release-source.json').write_text(json.dumps({
        'schema': 1, 'channel': 'stable', 'metadata_url': 'https://release.example/m/',
        'targets_url': 'https://release.example/t/', 'targets': {k: f'{k}.zip' for k in kinds}}))
    (tmp_path / 'updates').mkdir()
    (tmp_path / 'updates/router-policy.json').write_text(json.dumps(
        {'schema': 1, 'paused': False, 'deferred_until': 0, 'enabled': router_enabled}))
    return registry


class FakeClient:
    def __init__(self, sequences):
        self.sequences = sequences

    def check(self, names):
        targets = {n: SimpleNamespace(custom={'component': n.split('.')[0], 'sequence': self.sequences[n.split('.')[0]]})
                   for n in names}
        return {'reason': None}, targets


class FakeHelper:
    def __init__(self, registry):
        self.registry, self.base, self.prepared = registry, registry.path.parent / 'updates', []

    def status(self):
        return None

    def prepare(self, selection, names, **kwargs):
        self.prepared.append((sorted(names), kwargs))


@pytest.mark.skipif(os.name != 'nt', reason='registry paths are Windows paths')
def test_hub_client_and_router_are_staged_in_one_transaction(tmp_path):
    registry = _install(tmp_path, ['hub', 'client', 'router'])
    helper = FakeHelper(registry)
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 7, 'client': 5, 'router': 3}), clock=lambda: 1_000.0)
    service.check(now=True)
    assert helper.prepared == [(['client.zip', 'hub.zip', 'router.zip'],
                                {'automatic': True, 'automatic_scope': 'hub'})]
    assert service.status()['check_outcome'] == 'update_found'


@pytest.mark.skipif(os.name != 'nt', reason='registry paths are Windows paths')
def test_only_components_with_a_newer_sequence_join_and_a_disabled_router_stays_out(tmp_path):
    registry = _install(tmp_path, ['hub', 'client', 'router'])
    helper = FakeHelper(registry)
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 1, 'client': 5, 'router': 3}), clock=lambda: 1_000.0)
    service.check(now=True)
    assert [names for names, _ in helper.prepared] == [['client.zip', 'router.zip']]
    other = tmp_path / 'second'
    other.mkdir()
    registry = _install(other, ['hub', 'client', 'router'], router_enabled=False)
    helper = FakeHelper(registry)
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 7, 'client': 5, 'router': 3}), clock=lambda: 1_000.0)
    service.check(now=True)
    assert [names for names, _ in helper.prepared] == [['client.zip', 'hub.zip']]


@pytest.mark.skipif(os.name != 'nt', reason='registry paths are Windows paths')
def test_router_only_install_keeps_its_own_scheduler(tmp_path):
    registry = _install(tmp_path, ['router'])
    helper = FakeHelper(registry)
    hub_scope = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient({'router': 3}),
                                 clock=lambda: 1_000.0)
    hub_scope.check(now=True)
    assert helper.prepared == []  # No Hub: the hub-scope scheduler never stages a standalone Router.
    router_scope = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient({'router': 3}),
                                    clock=lambda: 1_000.0, scope='router')
    router_scope.check(now=True)
    assert [names for names, _ in helper.prepared] == [['router.zip']]
