"""Apply-now end to end in a sandbox: the apply owns the operation lock from before "stop" until the
transaction ended; nothing restarts services in between; the user gets a window and a result message."""
from contextlib import contextmanager
import os
import threading
import time

import pytest

from test_automatic_updates import automatic  # noqa: F401  (fixtures below)
from test_update_helper import installed  # noqa: F401
from test_release_packages import repo  # noqa: F401
from test_update_now import Calls, staged, request
from services.shared import update_now, update_ui, launcher
from services.shared.installation_registry import operation_lock
from services.shared.process_lifecycle import LifecycleError

windows_only = pytest.mark.skipif(os.name != 'nt', reason='thread-owned OS mutex; the product is Windows-only')


class Rival:
    """Another launcher (second start, tray, Windows startup) taking the operation lock the way the retained
    `startup` helper does: `operation_lock(timeout=0)` in a loop."""

    def __init__(self, registry):
        self.registry, self.got, self.stop_flag = registry, [], False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop_flag:
            try:
                with operation_lock(self.registry.path, timeout=0):
                    self.got.append(time.monotonic())
                    time.sleep(.05)
            except LifecycleError:
                pass
            time.sleep(.002)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_flag = True
        self.thread.join()


class RealisticStop(Calls):
    """Like launch(stop=True): takes the (re-entrant) operation lock itself and needs a moment."""

    def stop(self, registry):
        self.began = time.monotonic()
        with operation_lock(registry.path, timeout=5):
            time.sleep(.3)
        time.sleep(.3)  # Services still shutting down after the launcher returned: the old race window.
        self.events.append('stop')

    def start(self, registry):
        self.restarted = time.monotonic()
        self.events.append('start')


def released_at(monkeypatch, calls):
    """Record the moment the apply owner released the operation lock (just before it restarts services)."""
    original = update_now.operation_lock

    def spy(path, **kwargs):
        lock = original(path, **kwargs)
        if path != service_registry_lock(path):
            return lock
        exit_ = lock.__exit__

        def finish(*args):
            calls.released = time.monotonic()  # Taken before the release: a rival can only be later.
            return exit_(*args)
        lock.__exit__ = finish
        return lock
    monkeypatch.setattr(update_now, 'operation_lock', spy)


def service_registry_lock(path):
    return path if os.path.basename(str(path)) == 'install.json' else None


@windows_only
def test_nobody_can_take_the_operation_lock_between_stop_and_resume(automatic, monkeypatch):
    """Regression: the Hub was stopped, then the helper's resume failed with "Lifecycle busy" because another
    launcher had taken the lock in between (apply-now.json ended 'failed', RuntimeError)."""
    service, now, data, host = staged(automatic)
    request(service, now)
    calls = RealisticStop()
    released_at(monkeypatch, calls)
    with Rival(service.registry) as rival:
        state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
        taken = list(rival.got)
    assert state['phase'] == 'completed' and state['error'] is None, state
    assert calls.events == ['stop', 'start']
    assert service.helper.status()['phase'] == 'completed'
    assert taken, 'the rival never ran: the test proves nothing'
    during = [t for t in taken if calls.began <= t < calls.released]
    assert during == [], 'a rival got the operation lock between stop and the end of the transaction'


@windows_only
def test_rollback_with_rival_launchers_still_restarts_exactly_once(automatic, monkeypatch):
    service, now, data, host = staged(automatic)
    host.healthy = False
    request(service, now)
    calls = RealisticStop()
    released_at(monkeypatch, calls)
    with Rival(service.registry) as rival:
        state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
        taken = list(rival.got)
    assert state['phase'] == 'failed' and state['outcome'] == 'rolled_back'
    assert calls.events == ['stop', 'start']
    assert [t for t in taken if calls.began <= t < calls.released] == []
    assert service.helper.status()['phase'] == 'rolled_back'


@windows_only
def test_busy_install_fails_before_anything_is_stopped(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    calls = Calls()
    ready, release = threading.Event(), threading.Event()

    def hold():
        with operation_lock(service.registry.path, timeout=1):
            ready.set()
            release.wait(10)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    ready.wait(5)
    try:
        state = update_now.process_request(service, stop=calls.stop, start=calls.start,
                                           clock=lambda: now[0], lock_wait=.3)
    finally:
        release.set()
        thread.join()
    assert state['phase'] == 'failed' and state['error'] == 'OperationBusy'
    assert calls.events == ['start']  # Never stopped; the app window is simply reopened.
    assert service.helper.status()['phase'] == 'staged'  # Can be applied again.
    assert 'başka bir orion işlemi' in update_now.view(service.registry)['message'].lower()


def test_update_finished_by_another_launcher_is_reported_as_completed(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    original = service.helper.status
    seen = []

    def applied_elsewhere():
        # The first look (before the lock) still sees it staged; once we hold the lock it is done.
        seen.append(1)
        if len(seen) == 2:
            service.helper.status = original
            service.helper.resume(automatic=False)
        return original()
    service.helper.status = applied_elsewhere
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['phase'] == 'completed' and state['outcome'] == 'completed'
    assert calls.events == ['start']


def test_rollback_is_reported_with_a_turkish_reason_and_services_restart(automatic):
    service, now, data, host = staged(automatic)
    host.healthy = False
    request(service, now)
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['phase'] == 'failed' and state['outcome'] == 'rolled_back' and calls.events == ['stop', 'start']
    result = update_ui.read_result(service.registry)
    assert result['status'] == 'rolled_back' and result['seen'] is False
    assert 'önceki sürümle devam ediliyor' in result['message']


def test_success_result_names_the_new_versions_and_is_shown_once(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    update_now.process_request(service, stop=Calls().stop, start=Calls().start, clock=lambda: now[0])
    result = update_ui.read_result(service.registry)
    assert result['status'] == 'completed' and result['message'].startswith('Güncellendi: Hub 9.0.1')
    assert update_ui.mark_seen(service.registry)['seen'] is True


def test_updating_window_wraps_the_whole_apply_and_closes_even_on_failure(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    events = []

    @contextmanager
    def window(registry):
        events.append('window-open')
        try:
            yield
        finally:
            events.append('window-close')

    def stop(registry):
        events.append('stop')
        raise RuntimeError('boom')
    state = update_now.process_request(service, stop=stop, start=lambda r: events.append('start'),
                                       clock=lambda: now[0], window=window)
    assert events == ['window-open', 'stop', 'start', 'window-close']
    assert state['phase'] == 'failed' and state['error'] == 'RuntimeError'


def test_launchers_wait_while_the_watcher_applies_but_the_watcher_itself_does_not(automatic, monkeypatch):
    service, now, data, host = staged(automatic)
    request(service, now)
    update_now.claim(service.registry, clock=lambda: now[0])  # 'stopping': the watcher owns the install.
    monkeypatch.setattr(update_now, 'watcher_alive', lambda registry: True)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:  # The watcher finished.
            update_now.update(service.registry, phase='completed', clock=lambda: now[0])
    assert launcher.wait_for_update(service.registry, sleep=sleep, interval=1) is True and len(sleeps) == 3
    update_now.update(service.registry, phase='stopping', clock=lambda: now[0])
    assert launcher.wait_for_update(service.registry, sleep=sleep, apply_owner=True) is False


def test_an_abandoned_apply_does_not_block_launchers_forever(automatic, monkeypatch):
    service, now, data, host = staged(automatic)
    request(service, now)
    update_now.claim(service.registry, clock=lambda: now[0])
    monkeypatch.setattr(update_now, 'watcher_alive', lambda registry: True)
    assert update_now.in_progress(service.registry, clock=lambda: now[0]) is True
    assert update_now.in_progress(service.registry, clock=lambda: now[0] + update_now.ACTIVITY_TIMEOUT + 1) is False
    monkeypatch.setattr(update_now, 'watcher_alive', lambda registry: False)
    assert update_now.in_progress(service.registry, clock=lambda: now[0]) is False
