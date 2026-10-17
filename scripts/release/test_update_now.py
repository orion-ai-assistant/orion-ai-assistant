"""Explicit "update now": waiting, hand-over, apply outside the Hub, restart and failure paths."""
import pytest

from test_automatic_updates import automatic  # noqa: F401  (fixtures below)
from test_update_helper import installed  # noqa: F401
from test_release_packages import repo  # noqa: F401
from services.shared import update_now
from services.shared.update_helper import UpdateError


class Calls:
    def __init__(self, host=None):
        self.events, self.host = [], host

    def stop(self, registry):
        self.events.append('stop')

    def start(self, registry):
        self.events.append('start')


def staged(automatic):
    service, now, data, host = automatic
    service.check()
    assert service.helper.status()['phase'] == 'staged'
    return service, now, data, host


def request(service, now):
    update_now.begin(service.registry, service.helper, clock=lambda: now[0])
    assert update_now.handoff(service.registry, clock=lambda: now[0])


def test_applies_staged_update_stopping_first_and_restarting_after(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['phase'] == 'completed' and state['error'] is None
    assert calls.events == ['stop', 'start']
    assert service.helper.status()['phase'] == 'completed'
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'
    assert update_now.view(service.registry, clock=lambda: now[0])['phase'] == 'completed'


def test_works_even_when_automatic_updates_are_off_or_deferred(automatic):
    service, now, data, host = staged(automatic)
    service.preferences(enabled=False, defer_hours=24)
    request(service, now)
    state = update_now.process_request(service, stop=Calls().stop, start=Calls().start, clock=lambda: now[0])
    assert state['phase'] == 'completed'


def test_nothing_staged_or_already_requested_is_refused(automatic):
    service, now, data, host = automatic
    with pytest.raises(UpdateError, match='NothingStaged'):
        update_now.begin(service.registry, service.helper)
    service.check()
    update_now.begin(service.registry, service.helper)
    with pytest.raises(UpdateError, match='AlreadyRequested'):
        update_now.begin(service.registry, service.helper)


def test_watcher_ignores_a_request_that_is_still_waiting_for_idle(automatic):
    service, now, data, host = staged(automatic)
    update_now.begin(service.registry, service.helper)
    calls = Calls()
    assert update_now.process_request(service, stop=calls.stop, start=calls.start) is None
    assert calls.events == [] and service.helper.status()['phase'] == 'staged'


def test_cancel_wins_until_the_watcher_claims(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    update_now.cancel(service.registry)
    calls = Calls()
    assert update_now.process_request(service, stop=calls.stop, start=calls.start) is None
    assert calls.events == [] and service.helper.status()['phase'] == 'staged'
    update_now.begin(service.registry, service.helper)
    update_now.handoff(service.registry)
    assert update_now.claim(service.registry)['phase'] == 'stopping'
    with pytest.raises(UpdateError, match='NotCancellable'):
        update_now.cancel(service.registry)


def test_failed_health_rolls_back_and_still_restarts(automatic):
    service, now, data, host = staged(automatic)
    host.healthy = False
    request(service, now)
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['phase'] == 'failed' and state['outcome'] == 'rolled_back'
    assert state['error'] and calls.events == ['stop', 'start']
    assert service.helper.status()['phase'] == 'rolled_back'


def test_services_that_cannot_be_stopped_are_never_switched(automatic):
    service, now, data, host = staged(automatic)
    host.idle = False
    request(service, now)
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['phase'] == 'failed' and state['error'] == 'ServicesStillRunning'
    assert service.helper.status()['phase'] == 'waiting_for_idle'
    assert calls.events == ['stop', 'start']


def test_staged_update_changed_after_request_is_not_applied(automatic):
    service, now, data, host = staged(automatic)
    request(service, now)
    service.helper.rollback()  # The staged transaction is cancelled meanwhile.
    calls = Calls()
    state = update_now.process_request(service, stop=calls.stop, start=calls.start, clock=lambda: now[0])
    assert state['error'] == 'StagedUpdateChanged' and calls.events == ['start']


def test_stale_requests_become_explicit_failures(automatic):
    service, now, data, host = staged(automatic)
    update_now.begin(service.registry, service.helper, clock=lambda: now[0])
    now[0] += update_now.HEARTBEAT_TIMEOUT + 1
    assert update_now.view(service.registry, clock=lambda: now[0])['error'] == 'RequestLost'
    now[0] += 1
    update_now.begin(service.registry, service.helper, clock=lambda: now[0])
    update_now.handoff(service.registry, clock=lambda: now[0])
    now[0] += update_now.HANDOFF_TIMEOUT + 1
    assert update_now.view(service.registry, clock=lambda: now[0])['error'] == 'WatcherDidNotRespond'
    assert update_now.process_request(service, stop=Calls().stop, start=Calls().start) is None


def test_view_exposes_no_paths(automatic):
    service, now, data, host = staged(automatic)
    update_now.begin(service.registry, service.helper)
    text = str(update_now.view(service.registry))
    assert 'path' not in text.lower() and str(service.registry.path.parent) not in text
