"""Automatic staging with real signed local fixtures; no product services start."""
from copy import deepcopy
import json
from unittest.mock import Mock, patch

import pytest

from test_update_helper import installed, Host, Crash
from test_release_packages import repo, publisher
from services.shared.automatic_updates import AutomaticUpdates, validate_source, INTERVAL, run_startup, watch
from services.shared.update_helper import UpdateHelper, UpdateError
from services.shared import launcher


@pytest.fixture
def automatic(installed, monkeypatch):
    helper, client, name, old, data, host = installed
    feed = {'schema': 1, 'channel': 'stable', 'metadata_url': 'https://release.example/metadata/',
            'targets_url': 'https://release.example/targets/', 'targets': {'hub': name}}
    bootstrap = helper.registry.path.parent / 'bootstrap'
    bootstrap.mkdir(exist_ok=True)
    (bootstrap / 'release-source.json').write_text(json.dumps(feed))
    now = [2_000_000_000.0]
    monkeypatch.setattr('services.shared.automatic_updates.time.time', lambda: now[0])
    service = AutomaticUpdates(helper.registry, helper=helper, client_factory=lambda: client.fresh(), clock=lambda: now[0])
    service.preferences(enabled=True)
    return service, now, data, host


def test_signed_background_stages_without_stopping_active_local_or_remote_work(automatic):
    service, now, data, host = automatic
    host.idle = False  # Live API/worker/remote clients; no activation permitted.
    before = service.registry.read()
    service.check()
    assert service.helper.status()['phase'] == 'staged'
    assert service.registry.read() == before
    assert host.stopped_calls == 0
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'
    status = service.status()
    assert status['pending']['packages']['hub'].startswith('orion-hub-9.0.1')
    assert status['last_success'] == now[0]
    assert not any(key in json.dumps(status) for key in ('code_path', 'data_path', 'backups', 'identity.manual'))
    assert service.startup()['phase'] == 'waiting_for_idle'
    assert host.stopped_calls == 0
    host.idle = True
    assert service.startup()['phase'] == 'completed'
    assert service.status()['pending'] is None


def test_disable_between_prepare_and_start_keeps_candidate_and_latest_preferences(automatic):
    service, now, data, host = automatic
    service.check()
    service.preferences(enabled=False)
    assert service.startup()['phase'] == 'staged'
    assert host.stopped_calls == 0
    service.preferences(enabled=True)
    assert service.startup()['phase'] == 'completed'
    assert service.registry.read()['preferences']['automatic_updates'] is True


def test_deferral_and_network_pause_are_durable_and_separate(automatic):
    service, now, data, host = automatic
    service.preferences(paused=True)
    service.check()
    assert service.helper.status() is None
    service.preferences(paused=False)
    service.check()
    service.preferences(defer_hours=24, paused=True)
    restarted = AutomaticUpdates(service.registry, helper=service.helper, clock=lambda: now[0])
    assert restarted.startup()['phase'] == 'staged'
    now[0] += 86401
    assert restarted.startup()['phase'] == 'completed'
    assert restarted.policy()['paused'] is True


def test_disable_or_pause_at_admission_prevents_new_preparation(automatic):
    service, now, data, host = automatic
    factory = service.client_factory
    client = factory()
    class Client:
        def check(self, names):
            result = client.check(names)
            service.preferences(enabled=False)
            return result
    service.client_factory = Client
    service.check()
    assert service.helper.status() is None
    assert host.stopped_calls == 0


def test_check_cut_short_by_quit_is_interrupted_not_an_error_and_retries(automatic, monkeypatch):
    from services.shared.process_lifecycle import LifecycleError
    service, now, data, host = automatic
    real = service.helper.prepare
    def busy(*args, **kwargs):
        raise LifecycleError('Lifecycle busy or mutex inaccessible')  # Orion is closing and holds the install.
    monkeypatch.setattr(service.helper, 'prepare', busy)
    service.check()
    status = service.status()
    assert status['error'] is None and status['check_outcome'] == 'interrupted'
    assert status['newer']['components']['hub']  # What the check found stays on the card.
    monkeypatch.setattr(service.helper, 'prepare', real)
    now[0] += 60  # Well inside the 6-hour spacing: the cut-short round was not spent.
    service.check()
    assert service.helper.status()['phase'] == 'staged'


def test_failure_preserves_last_success_then_online_retry_after_interval(automatic):
    service, now, data, host = automatic
    factory = service.client_factory
    client = service.client_factory()
    fetcher = client.updater._fetcher
    # Use a fresh client for each refresh; the injected fixture transport is shared.
    fetcher.offline = True
    service.client_factory = lambda: client
    service.check()
    assert service.status()['last_success'] is None
    assert service.status()['error'] == 'DownloadError'
    fetcher.offline = False
    now[0] += INTERVAL + 1
    service.client_factory = factory
    service.check()
    assert service.helper.status()['phase'] == 'staged'
    assert service.status()['error'] is None


def test_same_version_check_does_not_create_an_update(automatic):
    service, now, data, host = automatic
    service.check()
    assert service.startup()['phase'] == 'completed'
    now[0] += INTERVAL + 1
    service.check()
    assert service.helper.status()['phase'] == 'completed'
    assert len(service.status()['history']) == 1


def test_rollback_keeps_preference_changes_made_after_staging(automatic):
    service, now, data, host = automatic
    service.check()
    record = service.registry.read()
    record['preferences']['show_client'] = False
    service.registry.write(record)
    host.healthy = False
    assert service.startup()['phase'] == 'rolled_back'
    assert service.registry.read()['preferences']['show_client'] is False
    assert (data / 'identity.manual').exists()


def test_preference_or_unconfigured_feed_never_starts_a_check(installed):
    helper, client, name, old, data, host = installed
    service = AutomaticUpdates(helper.registry, helper=helper, client_factory=Mock())
    service.check()
    service.client_factory.assert_not_called()
    with pytest.raises(UpdateError, match='not configured'):
        service.preferences(enabled=True)


@pytest.mark.parametrize('field,value', [('channel', 'beta'), ('metadata_url', 'http://release.example'),
    ('targets', {'unknown': 'unknown.zip'}), ('targets', {'hub': '../escape.zip'})])
def test_feed_rejects_other_channels_router_and_unsafe_transport(automatic, field, value):
    service, *_ = automatic
    source = deepcopy(service.source())
    source[field] = value
    with pytest.raises(ValueError):
        validate_source(source)


def test_launcher_runs_update_before_operation_lock_and_rereads_registry(automatic):
    service, now, data, host = automatic
    service.check()
    with patch('services.shared.automatic_updates.run_startup', side_effect=lambda registry: service.startup()) as boundary, \
         patch.object(launcher, 'managed_components', return_value=[]), \
         patch.object(launcher, 'launch_environment'):
        assert launcher.launch(service.registry, background=True)['status'] == 'ready'
    boundary.assert_called_once()
    assert service.helper.status()['phase'] == 'completed'


def test_missing_retained_runtime_does_not_spawn_from_active_hub(automatic):
    service, *_ = automatic
    with patch('services.shared.automatic_updates.subprocess.run') as run:
        run_startup(service.registry)
    run.assert_not_called()


def test_watcher_checks_without_activation_and_exits_after_services_stop(automatic):
    service, now, data, host = automatic
    host.idle = False
    def stop_later(seconds):
        assert seconds == .25  # The 5 s pause is sliced so that a quit ends it at once.
        assert service.helper.status()['phase'] == 'staged'
        assert host.stopped_calls == 0
        host.idle = True
    with patch('services.shared.automatic_updates.time.sleep', side_effect=stop_later):
        watch(service)
    assert service.helper.status()['phase'] == 'staged'


def test_interrupted_background_download_recovers_without_changing_active_data(automatic):
    service, now, data, host = automatic
    before = service.registry.read()
    def crash(point):
        if point == 'after:journal:downloading':
            raise Crash(point)
    service.helper.fault = crash
    with pytest.raises(Crash):
        service.check()
    assert service.helper.status()['phase'] == 'downloading'
    restarted = AutomaticUpdates(service.registry, helper=UpdateHelper(service.registry, host=Host()),
                                 client_factory=service.client_factory)
    assert restarted.startup()['phase'] == 'failed'
    assert service.registry.read() == before
    assert (data / 'identity.manual').read_bytes() == b'encrypted preference sentinel'
    now[0] += INTERVAL + 1
    restarted.check()
    assert restarted.helper.status()['phase'] == 'staged'


def test_refresh_now_skips_only_the_spacing_not_the_policy(automatic):
    service, now, data, host = automatic
    client = service.client_factory()
    fetcher = client.updater._fetcher
    factory = service.client_factory
    fetcher.offline = True
    service.client_factory = lambda: client
    service.check()
    first = service.status()['last_attempt']
    now[0] += 60
    service.check()  # Within the spacing: no new attempt.
    assert service.status()['last_attempt'] == first
    fetcher.offline = False
    service.client_factory = factory
    service.check(now=True)  # Refresh button: immediate retry.
    assert service.status()['last_attempt'] == now[0]
    assert service.helper.status()['phase'] == 'staged'
    service.preferences(enabled=False)
    now[0] += 60
    before = service.status()['last_attempt']
    service.check(now=True)  # Turned off: refresh never overrides the preference.
    assert service.status()['last_attempt'] == before


def test_unreadable_state_file_does_not_stop_checks_or_the_watcher(automatic):
    service, now, data, host = automatic
    service.state_path.write_bytes(b'\xef\xbb\xbf{"schema": 1, "last_attempt": 0}')  # BOM is tolerated
    assert service.status()['last_attempt'] == 0
    service.state_path.write_bytes(b'not json at all')
    service.check()
    assert service.helper.status()['phase'] == 'staged'
    assert service.status()['last_success'] == now[0]


def test_launch_with_services_still_up_skips_silently_without_window_or_rehash(automatic):
    service, now, data, host = automatic
    host.idle = False
    service.check()
    assert service.helper.status()['phase'] == 'staged'
    host.quiet_now = lambda record: False  # Hub running, or its tree still exiting: "not now"
    with patch('services.shared.update_ui.updating_window', side_effect=AssertionError('no window')), \
            patch.object(service.helper, 'resume', side_effect=AssertionError('no re-verify')):
        assert service.startup()['phase'] == 'staged'
    assert host.stopped_calls == 0
    host.quiet_now = lambda record: True
    host.idle = True
    assert service.startup()['phase'] == 'completed'


def test_launch_loses_the_operation_lock_race_without_failing(automatic):
    from services.shared.process_lifecycle import LifecycleError
    service, now, data, host = automatic
    service.check()
    host.quiet_now = lambda record: True
    with patch('services.shared.update_ui.updating_window'), \
            patch.object(service.helper, 'resume', side_effect=LifecycleError('busy')):
        assert service.startup()['phase'] == 'staged'


def test_feed_failure_while_the_bootstrap_replaces_itself_is_retried_once(automatic, monkeypatch):
    from services.shared.release_packages import PackageError
    service, now, data, host = automatic
    monkeypatch.setattr('services.shared.automatic_updates.RETRY_PAUSE', 0)
    good = service.client_factory
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise PackageError('trust root being replaced')
        return good()
    service.client_factory = flaky
    service.check(now=True)
    assert len(calls) == 2
    assert service.status()['check_outcome'] == 'update_found' and service.status()['error'] is None


def test_history_shows_the_updater_renewing_itself_newest_first(automatic):
    # The updater replaces itself outside any journal: the history listed only component updates, while
    # "Kurulu sürümler" already showed the new updater.
    service, now, data, host = automatic
    service.check()
    assert service.startup()['phase'] == 'completed'
    from services.shared import bootstrap_update
    path = bootstrap_update.state_path(service.registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema': 1, 'state': 'confirmed', 'confirmed_at': now[0] + 50}))
    history = service.status()['history']
    assert history[0] == {'operation_id': None, 'phase': 'completed', 'changes': ['bootstrap'],
                          'updated_at': now[0] + 50}
    assert len(history) == 2 and history[1]['phase'] == 'completed'
    path.write_text(json.dumps({'schema': 1, 'state': 'trial'}))  # Not confirmed yet: not in the history.
    assert len(service.status()['history']) == 1
