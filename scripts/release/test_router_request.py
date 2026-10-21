"""The Router panel's "Güncellemeleri Denetle" joins the single transaction and is never blocked by a failed attempt."""
import json
import os
from unittest.mock import patch

import pytest

from test_update_visibility import _install, FakeClient, FakeHelper
from services.shared.automatic_updates import AutomaticUpdates, watch

nt_only = pytest.mark.skipif(os.name != 'nt', reason='registry paths are Windows paths')


@nt_only
def test_explicit_router_check_stages_hub_client_and_router_together_even_with_router_switch_off(tmp_path):
    registry = _install(tmp_path, ['hub', 'client', 'router'], router_enabled=False)
    helper = FakeHelper(registry)
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 7, 'client': 5, 'router': 3}), clock=lambda: 1_000.0)
    service.check(manual=True, with_router=True)
    assert [names for names, _ in helper.prepared] == [['client.zip', 'hub.zip', 'router.zip']]


@nt_only
def test_a_failed_earlier_router_attempt_does_not_block_the_next_press_for_six_hours(tmp_path):
    registry = _install(tmp_path, ['hub', 'client', 'router'])
    helper = FakeHelper(registry)
    now = [1_000.0]
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 1, 'client': 1, 'router': 3}), clock=lambda: now[0])
    state = {'schema': 1, 'last_attempt': now[0] - 60, 'error': 'UpdateError'}
    (tmp_path / 'updates/automatic.json').write_text(json.dumps(state))
    service.check()  # Automatic poll within the spacing: waits.
    assert helper.prepared == []
    service.check(manual=True, with_router=True)  # The button press goes through at once.
    assert [names for names, _ in helper.prepared] == [['router.zip']]


@nt_only
def test_request_file_from_the_router_panel_is_served_by_the_watcher(tmp_path):
    registry = _install(tmp_path, ['hub', 'client', 'router'])
    helper = FakeHelper(registry)

    class Host:
        calls = 0

        def stopped(self, record):
            from contextlib import nullcontext
            Host.calls += 1
            return nullcontext(Host.calls > 1)  # Run one loop, then "stopped".
    helper.host = Host()
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient(
        {'hub': 1, 'client': 1, 'router': 3}), clock=lambda: 1_000.0)
    request = tmp_path / 'updates/router-request.json'
    request.write_text(json.dumps({'schema': 1, 'installation_id': registry.read()['installation_id'], 'action': 'prepare'}))
    with patch('services.shared.automatic_updates.time.sleep'):
        watch(service)
    assert not request.exists()
    assert [names for names, _ in helper.prepared] == [['router.zip']]
    assert 'update_found' == json.loads((tmp_path / 'updates/automatic.json').read_text())['outcome']


@nt_only
def test_router_first_then_hub_added_later_joins_the_single_transaction(tmp_path):
    """A Router-only install updates the Router on its own; once the Hub is installed beside it, the Hub's
    scheduler takes the Router into the same transaction (nothing is fixed at install time)."""
    registry = _install(tmp_path / 'first', ['router'])
    helper = FakeHelper(registry)
    clock = lambda: 1_000.0
    factory = lambda: FakeClient({'hub': 7, 'client': 5, 'router': 3})
    hub_scope = AutomaticUpdates(registry, helper=helper, client_factory=factory, clock=clock)
    assert not hub_scope.covers_router()
    AutomaticUpdates(registry, helper=helper, client_factory=factory, clock=clock, scope='router').check(manual=True)
    assert [names for names, _ in helper.prepared] == [['router.zip']]

    registry = _install(tmp_path / 'later', ['hub', 'client', 'router'])  # the Hub is added later
    helper = FakeHelper(registry)
    service = AutomaticUpdates(registry, helper=helper, client_factory=factory, clock=clock)
    assert service.covers_router()
    service.check(manual=True)
    assert [names for names, _ in helper.prepared] == [['client.zip', 'hub.zip', 'router.zip']]


def _watch_once_with_apply_request(tmp_path, kinds):
    from services.shared import update_now
    registry = _install(tmp_path, kinds)
    helper = FakeHelper(registry)
    record = registry.read()
    helper.status = lambda: {'operation_id': 'op-1', 'installation_id': record['installation_id'], 'phase': 'staged',
                             'proposed': {'components': []}}

    class Host:
        calls = 0

        def stopped(self, _):
            from contextlib import nullcontext
            Host.calls += 1
            return nullcontext(Host.calls > 2)
    helper.host = Host()
    service = AutomaticUpdates(registry, helper=helper, client_factory=lambda: FakeClient({'router': 3}),
                               clock=lambda: 1_000.0)
    request = tmp_path / 'updates/router-request.json'
    request.write_text(json.dumps({'schema': 1, 'installation_id': record['installation_id'], 'action': 'apply'}))
    seen = []
    with patch('services.shared.automatic_updates.time.sleep'),             patch.object(update_now, 'process_request', side_effect=lambda a, **k: seen.append(update_now.read(registry))):
        watch(service)
    assert not request.exists()
    return seen


@nt_only
def test_router_only_panel_apply_request_hands_the_staged_update_to_the_watcher(tmp_path):
    seen = _watch_once_with_apply_request(tmp_path, ['router'])
    assert seen[0] is None  # nothing requested yet in the first round
    assert seen[1]['phase'] == 'requested' and seen[1]['operation_id'] == 'op-1'


@nt_only
def test_apply_request_is_ignored_when_a_hub_is_installed(tmp_path):
    seen = _watch_once_with_apply_request(tmp_path, ['hub', 'client', 'router'])
    assert all(state is None for state in seen)
