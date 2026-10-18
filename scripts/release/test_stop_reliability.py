"""Stopping Orion ("Çık", `Orion.ps1 -Stop`) must stop everything or say loudly that it could not.

Observed on the real install: the tray "Çık" stopped the Hub side but left the Router (the first component's
failure aborted the rest), a stale "update now" request survived the restart and later stopped a fresh Hub, and
failures were swallowed by the entry script."""
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

import pytest

from services.shared import launcher, update_now
from services.shared.automatic_updates import AutomaticUpdates, watch
from services.shared.installation_registry import Registry, RegistryError, component_record, new_registry
from services.shared.process_lifecycle import LifecycleError
from services.shared.update_helper import UpdateError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def install(tmp_path):
    hub = tmp_path / 'Hub'
    (hub / 'services/hub').mkdir(parents=True)
    (hub / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
    router = tmp_path / 'Router'
    router.mkdir()
    (router / 'pyproject.toml').write_text('[project]\nname="orion-router"', encoding='utf-8')
    (router / 'cli.py').write_text('', encoding='utf-8')
    registry = Registry(tmp_path / 'Orion/install.json')
    registry.write(new_registry(tmp_path, [
        component_record('hub', hub, ownership='owned', management='local'),
        component_record('router', router, ownership='owned', management='local')]))
    (registry.path.parent / 'updates').mkdir(exist_ok=True)
    return registry, hub, router


@pytest.fixture
def fakes(install):
    registry, hub, router = install
    calls = []
    with patch('services.shared.native_services.stop_hub', side_effect=lambda root: calls.append('hub')) as stop_hub, \
            patch('services.shared.native_services.stop_router', side_effect=lambda script: calls.append('router')) as stop_router, \
            patch('services.shared.native_services.start_hub'), patch('services.shared.native_services.start_router'), \
            patch('services.shared.launcher.stop_tray', side_effect=lambda r: calls.append('tray')), \
            patch('services.shared.launcher.close_window', side_effect=lambda r: calls.append('window')), \
            patch('services.shared.launcher.spawn_tray'), patch('services.shared.launcher.show_window'), \
            patch('services.shared.launcher.STOP_RETRY_DELAY', 0), \
            patch('services.shared.update_ui.stop_window', side_effect=lambda r: calls.append('updating-window')):
        yield registry, calls, stop_hub, stop_router


def request_waiting(registry, phase='waiting_for_idle'):
    update_now._save(registry, {'schema': 1}, time.time, installation_id=registry.read()['installation_id'],
                     operation_id='00000000-0000-4000-8000-000000000000', phase=phase, requested_at=time.time(),
                     handoff_at=time.time(), error=None, busy={})


def test_a_failing_hub_stop_still_stops_router_tray_and_window_and_reports_loudly(fakes):
    registry, calls, stop_hub, stop_router = fakes
    stop_hub.side_effect = LifecycleError('Shutdown timeout; state retained and database left running')
    with pytest.raises(RegistryError, match='tamamen kapatilamadi.*hub'):
        launcher.launch(registry, stop=True)
    assert 'router' in calls and 'tray' in calls and 'window' in calls  # Nothing was skipped.
    assert not (registry.path.parent / launcher.WATCHER_EXIT).exists()  # Services remain: the watcher stays.
    log = (registry.path.parent / 'updates/watcher.log').read_text(encoding='utf-8')
    assert 'stop.incomplete' in log and 'hub' in log


def test_a_failing_router_stop_is_reported_too(fakes):
    registry, calls, stop_hub, stop_router = fakes
    stop_router.side_effect = LifecycleError('Router shutdown failed; state retained')
    with pytest.raises(RegistryError, match='router'):
        launcher.launch(registry, stop=True)
    assert 'hub' in calls and 'tray' in calls


def test_a_clean_stop_ends_tray_window_watcher_helper_window_and_a_stale_update_request(fakes):
    registry, calls, *_ = fakes
    request_waiting(registry)
    assert launcher.launch(registry, stop=True) == {'status': 'stopped'}
    assert calls == ['updating-window', 'tray', 'window'] or set(calls) >= {'hub', 'router', 'tray', 'window'}
    assert (registry.path.parent / launcher.WATCHER_EXIT).exists()
    assert update_now.read(registry)['phase'] == 'cancelled'


def test_services_left_running_are_detected_even_without_an_exception(fakes):
    registry, calls, *_ = fakes
    with patch('services.shared.launcher.running_services', return_value=['router']):
        with pytest.raises(RegistryError, match='calismaya devam eden: router'):
            launcher.launch(registry, stop=True)


def test_running_services_reads_the_real_ownership_state(install):
    registry, hub, router = install
    components = launcher.managed_components(registry.read())
    assert launcher.running_services(components) == []
    state = {'status': 'ready', 'children': {}}
    with patch('services.shared.process_lifecycle.Runtime.read', autospec=True,
               side_effect=lambda self: state if self.component == 'router-prod' else None) as read:
        assert launcher.running_services(components) == ['router']
        state['status'] = 'stopped'
        assert launcher.running_services(components) == []


def test_a_briefly_busy_lifecycle_lock_is_retried(fakes):
    registry, calls, stop_hub, stop_router = fakes
    attempts = []

    def flaky(root):
        attempts.append(1)
        if len(attempts) < 3:
            raise LifecycleError('Lifecycle busy or mutex inaccessible')
        calls.append('hub')
    stop_hub.side_effect = flaky
    launcher.launch(registry, stop=True)
    assert len(attempts) == 3 and 'hub' in calls
    stop_hub.side_effect = LifecycleError('Shutdown timeout; state retained')  # A real timeout is not retried.
    attempts.clear()
    with pytest.raises(RegistryError):
        launcher.launch(registry, stop=True)


@pytest.mark.skipif(os.name != 'nt', reason='thread-owned OS mutex; the product is Windows-only')
def test_stop_waits_for_the_operation_lock_held_by_another_process_for_a_moment(fakes):
    registry, calls, *_ = fakes
    ready = threading.Event()

    def hold():
        with launcher.operation_lock(registry.path, timeout=1):
            ready.set()
            time.sleep(1.2)
    thread = threading.Thread(target=hold)
    thread.start()
    ready.wait(5)
    started = time.monotonic()
    assert launcher.launch(registry, stop=True) == {'status': 'stopped'}
    thread.join()
    assert time.monotonic() - started >= 1.0 and {'hub', 'router', 'tray'} <= set(calls)


def test_the_update_flow_stop_keeps_the_watcher_and_its_own_request(fakes):
    registry, calls, *_ = fakes
    request_waiting(registry, 'requested')
    launcher.launch(registry, stop=True, keep_window=True, apply_owner=True)
    assert not (registry.path.parent / launcher.WATCHER_EXIT).exists()
    assert update_now.read(registry)['phase'] == 'requested'
    assert 'window' not in calls


def test_starting_again_clears_the_exit_request(fakes):
    registry, *_ = fakes
    marker = registry.path.parent / launcher.WATCHER_EXIT
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b'exit')
    with patch('services.shared.automatic_updates.spawn_watcher'), patch('services.shared.automatic_updates.run_startup'):
        launcher.launch(registry, background=True)
    assert not marker.exists()


def test_watcher_exits_when_stop_asked_for_it(install):
    registry, hub, router = install

    class Host:
        def stopped(self, record):
            from contextlib import nullcontext
            return nullcontext(False)

    class Helper:
        base = registry.path.parent / 'updates'
        host = Host()

        def status(self):
            return None
    Helper.base.mkdir(exist_ok=True)
    service = AutomaticUpdates(registry, helper=Helper())
    (registry.path.parent / 'bootstrap').mkdir()
    (registry.path.parent / 'bootstrap/release-source.json').write_text(json.dumps({
        'schema': 1, 'channel': 'stable', 'metadata_url': 'https://release.example/m/',
        'targets_url': 'https://release.example/t/', 'targets': {'hub': 'hub.zip'}}))
    (Helper.base / 'watcher.exit').write_bytes(b'exit')
    with patch('services.shared.automatic_updates.time.sleep', side_effect=AssertionError('kept polling')):
        assert watch(service) is None
    assert not (Helper.base / 'watcher.exit').exists()


def test_a_stale_handoff_is_never_applied_by_a_later_hub_session(install):
    registry, hub, router = install

    class Helper:
        base = registry.path.parent / 'updates'

        def status(self):
            raise AssertionError('a stale request must not even be examined')
    service = AutomaticUpdates(registry, helper=Helper())
    request_waiting(registry, 'requested')
    later = time.time() + update_now.HANDOFF_TIMEOUT + 5
    calls = []
    assert update_now.process_request(service, stop=calls.append, start=calls.append, clock=lambda: later) is None
    assert calls == []
    state = update_now.read(registry)
    assert state['phase'] == 'failed' and state['error'] == 'WatcherDidNotRespond'


def test_abandon_cancels_only_hub_owned_phases(install):
    registry, hub, router = install
    for phase, expected in (('waiting_for_idle', 'cancelled'), ('requested', 'cancelled'),
                            ('applying', 'applying'), ('completed', 'completed')):
        request_waiting(registry, phase)
        update_now.abandon(registry)
        assert update_now.read(registry)['phase'] == expected
    assert update_now.abandon(Registry(registry.path.parent / 'nothing/install.json')) is None


def test_the_entry_script_shows_stop_failures_and_survives_native_stderr():
    text = (ROOT / 'services/shared/launcher_entry.ps1').read_text(encoding='ascii')
    assert 'function Show-OrionStopFailure' in text
    assert 'if ($Stop) { Show-OrionStopFailure' in text
    assert "$ErrorActionPreference = 'Continue'" in text  # stderr of the launcher is not a terminating error


# ---- a normal quit takes seconds, and a launch right after it waits for it ------------------------------

def test_hub_and_router_stop_at_the_same_time_and_the_window_closes_first(fakes):
    registry, calls, stop_hub, stop_router = fakes
    stop_hub.side_effect = lambda root: (calls.append('hub'), time.sleep(.6))
    stop_router.side_effect = lambda script: (calls.append('router'), time.sleep(.6))
    started = time.monotonic()
    assert launcher.launch(registry, stop=True) == {'status': 'stopped'}
    assert time.monotonic() - started < 1.1  # Not 0.6 + 0.6.
    assert calls.index('window') < min(calls.index('hub'), calls.index('router'))
    assert not (registry.path.parent / launcher.QUITTING).exists()  # The quit marker is gone afterwards.


def test_a_launch_waits_for_a_running_quit_instead_of_racing_it(fakes):
    registry, calls, stop_hub, _ = fakes
    seen = []
    def slow_hub(root):
        seen.append(launcher.quitting(registry))
        time.sleep(.8)
    stop_hub.side_effect = slow_hub
    quit_thread = threading.Thread(target=launcher.launch, args=(registry,), kwargs={'stop': True})
    quit_thread.start()
    while not seen:
        time.sleep(.02)
    assert launcher.wait_for_quit(registry) is True  # Returned only once the quit was over.
    assert not launcher.quitting(registry)
    quit_thread.join()
    assert seen == [True]


def test_a_marker_left_by_a_killed_quit_is_ignored(install):
    registry, *_ = install
    # Its pid now belongs to another process (different start time): not a quit in progress.
    (registry.path.parent / launcher.QUITTING).write_text(json.dumps({'pid': os.getpid(), 'started': 1.0, 'at': time.time()}))
    assert launcher.wait_for_quit(registry) is False


def test_supervised_children_stop_together_and_are_not_stopped_twice(tmp_path):
    from services.shared import process_lifecycle
    runtime = process_lifecycle.Runtime.__new__(process_lifecycle.Runtime)
    runtime.children = {name: {'control': str(tmp_path / name), 'process': None} for name in ('api', 'local', 'mobile')}
    runtime.children['redis'] = {'control': None, 'process': None}
    stopped = []
    def stop(self, timeout=10):
        time.sleep(.5 if self.child['control'] else 0)
        stopped.append(self.child['control'] is None)
    with patch.object(process_lifecycle.Runtime, 'refresh_children'), patch.object(process_lifecycle.Runtime, 'save'), \
            patch.object(process_lifecycle.ManagedChild, 'stop', stop):
        started = time.monotonic()
        runtime.stop_children()
        assert time.monotonic() - started < 1.2  # Three 0.5 s graceful stops share one wait.
        assert stopped[-1] is True  # The native child (redis) still goes last.
        runtime.database, runtime.jobs, runtime.lock = None, {}, type('L', (), {'__exit__': lambda self: None})()
        with patch.object(process_lifecycle, 'child_tree_alive', return_value=False), \
                patch.object(process_lifecycle.Runtime, 'stop_children') as again:
            runtime.__exit__(None, None, None)
        again.assert_not_called()
        assert runtime.status == 'stopped'
