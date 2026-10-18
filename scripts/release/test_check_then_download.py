"""Refresh only checks, "İndir" downloads, "Kur"/"Sonra" apply now or at the next safe start."""
import json
import sys

import pytest

from test_update_helper import installed, Host, Crash  # noqa: F401 (fixtures)
from test_release_packages import repo, publisher  # noqa: F401 (fixtures)
from test_automatic_updates import automatic  # noqa: F401 (fixture)
from test_bootstrap_update import feed  # noqa: F401 (fixture)
from services.shared.automatic_updates import AutomaticUpdates, INTERVAL
from services.shared import update_ui


class Counting:
    """Wraps the signed fixture client and counts package downloads (prepare); checks only read metadata."""
    def __init__(self, factory):
        self.factory, self.prepared = factory, []

    def __call__(self):
        outer, inner = self, self.factory()

        class Client:
            def check(self, names):
                return inner.check(names)

            def prepare(self, target, destination, progress=None):
                outer.prepared.append(target.path)
                return inner.prepare(target, destination, progress=progress)

            def download_length(self, target):
                return target.length
        return Client()


def state(service):
    return json.loads(service.state_path.read_text())


def test_refresh_lists_newer_components_with_size_and_downloads_nothing(automatic):
    service, now, data, host = automatic
    service.preferences(enabled=False)  # The refresh button works with automatic updates off.
    service.client_factory = counting = Counting(service.client_factory)
    service.check(manual=True, with_router=True, download=False)
    assert counting.prepared == [] and service.helper.status() is None
    status = service.status()
    hub = status['newer']['components']['hub']
    assert hub['package_id'].startswith('orion-hub-9.0.1') and hub['version'] == '9.0.1'
    assert hub['bytes'] > 0 and hub['staged'] is False
    assert status['newer']['download'] is True and status['newer']['bytes'] == hub['bytes']
    assert status['check_outcome'] == 'update_found' and status['pending'] is None
    assert host.stopped_calls == 0


def test_download_stages_one_transaction_that_starts_with_automatic_updates_off(automatic):
    service, now, data, host = automatic
    service.preferences(enabled=False)
    service.check(manual=True, with_router=True, download=False)
    service.check(manual=True, with_router=True, download=True)
    tx = service.helper.status()
    assert tx['phase'] == 'staged' and tx['requested_by'] == 'user'
    status = service.status()
    assert status['pending']['requested_by'] == 'user'
    assert status['newer']['components']['hub']['staged'] is True
    assert status['newer']['download'] is False and status['newer']['bytes'] == 0
    # "Sonra": nothing more happens until the next safe start applies it (automatic updates still off).
    assert service.startup()['phase'] == 'completed'
    assert service.status()['newer'] is None  # Installed now: no longer offered.


def test_user_download_still_respects_the_24_hour_deferral(automatic):
    service, now, data, host = automatic
    service.preferences(enabled=False, defer_hours=24)
    service.check(manual=True, download=True)
    assert service.startup()['phase'] == 'staged'
    now[0] += 86401
    assert service.startup()['phase'] == 'completed'


def test_background_download_with_automatic_updates_off_is_not_applied_at_start(automatic):
    service, now, data, host = automatic
    service.check()  # Automatic updates on: silent background download.
    assert service.helper.status().get('requested_by') is None
    service.preferences(enabled=False)
    assert service.startup()['phase'] == 'staged'  # Unchanged rule for background downloads.


def test_with_automatic_updates_off_the_background_never_checks_or_downloads(automatic):
    service, now, data, host = automatic
    service.preferences(enabled=False)
    service.client_factory = counting = Counting(service.client_factory)
    now[0] += INTERVAL + 1
    service.check()
    assert counting.prepared == [] and service.helper.status() is None
    assert not service.state_path.exists() or 'newer' not in state(service)


def test_a_refresh_does_not_postpone_the_automatic_download_round(automatic):
    service, now, data, host = automatic
    service.check(manual=True, download=False)  # Refresh: only looked.
    assert service.helper.status() is None
    service.check()  # The watcher's next round downloads (automatic updates on) instead of waiting 6 hours.
    assert service.helper.status()['phase'] == 'staged'
    assert service.helper.status().get('requested_by') is None


def test_refresh_while_staged_keeps_the_waiting_update(automatic):
    service, now, data, host = automatic
    service.check()
    operation = service.helper.status()['operation_id']
    service.check(manual=True, download=False)
    assert service.helper.status()['operation_id'] == operation
    assert service.helper.status()['phase'] == 'staged'
    assert service.status()['newer']['download'] is False


def test_refresh_reports_a_newer_updater_without_adopting_it(feed):
    old = feed.current(sequence=1)
    feed.publish(2)
    service = feed.service(old)
    service.check(manual=True, with_router=True, download=False)
    assert feed.pointer() == old and feed.siblings() == ['Bootstrap-Installer']  # Nothing downloaded or switched.
    offer = service.status()['newer']['components']['bootstrap']
    assert offer['version'] == '0.1.2' and offer['sequence'] == 2 and offer['bytes'] > 0
    assert feed.helper.status() is None
    # "İndir" renews the updater first (its new watcher or the re-run downloads the components).
    service.check(manual=True, with_router=True, download=True)
    assert feed.pointer() != old
    assert 'bootstrap' not in ((service.status()['newer'] or {}).get('components') or {})


def test_card_buttons_run_one_user_round_with_fixed_arguments(feed, monkeypatch):
    import update_automatic
    calls = []

    class Fake:
        def __init__(self, registry, *, helper=None, scope='hub', **kw):
            self.scope, self.base, self.helper, self.registry = scope, feed.helper.base, helper, registry
            self.state_path = feed.helper.base / 'automatic.json'

        def check(self, **kw):
            calls.append((self.scope, kw))

        def replaced(self):
            return False
    monkeypatch.setattr(update_automatic, 'AutomaticUpdates', Fake)
    for flag, download in (('--only-check', False), ('--download', True)):
        calls.clear()
        monkeypatch.setattr(sys, 'argv', ['update_automatic.py', '--registry', str(feed.registry.path), 'check', flag])
        assert update_automatic.run() == 0
        assert calls == [('hub', {'manual': True, 'with_router': True, 'download': download})]  # No Router-only round.
    monkeypatch.setattr(sys, 'argv', ['update_automatic.py', '--registry', str(feed.registry.path), 'check',
                                      '--only-check', '--download'])
    with pytest.raises(SystemExit):
        update_automatic.run()


def test_download_that_renewed_the_updater_continues_with_the_new_one(feed, monkeypatch):
    import update_automatic
    reruns = []

    class Fake:
        def __init__(self, registry, *, helper=None, scope='hub', **kw):
            self.base, self.registry = feed.helper.base, registry

        def check(self, **kw):
            pass

        def replaced(self):
            return True
    monkeypatch.setattr(update_automatic, 'AutomaticUpdates', Fake)
    monkeypatch.setattr(update_automatic, '_recheck_with_new_bootstrap', lambda automatic, args: reruns.append(args) or 0)
    monkeypatch.setattr(sys, 'argv', ['update_automatic.py', '--registry', str(feed.registry.path), 'check', '--download'])
    assert update_automatic.run() == 0 and reruns == [['check', '--download']]


def test_later_is_remembered_per_staged_update(automatic):
    service, now, data, host = automatic
    service.check()
    operation = service.helper.status()['operation_id']
    assert not update_ui.prompt_postponed(service.registry, operation)
    update_ui.prompt_later(service.registry, operation)
    assert update_ui.prompt_postponed(service.registry, operation)
    assert not update_ui.prompt_postponed(service.registry, 'another-update')
