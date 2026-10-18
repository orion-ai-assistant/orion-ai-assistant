"""The update helper waits for the product's own processes instead of failing a transaction on them.

Observed: right after the installer started Orion, the helper's consumer scan found the freshly started /
still exiting services and failed the transaction with MigrationError ("An uncoordinated process uses this
installation")."""
import pytest

from services.shared import migration_host, update_helper
from services.shared.content_migration import MigrationError
from services.shared.migration_host import StoppedInstallation


def record(tmp_path):
    code = tmp_path / 'Hub/releases/hub-1'
    state = tmp_path / 'Hub/state'
    for path in (code, state, tmp_path / 'Hub/data'):
        path.mkdir(parents=True, exist_ok=True)
    return {'components': [{'kind': 'hub', 'component_id': '1', 'distribution': 'packaged',
                            'code_path': str(code), 'state_path': str(state), 'data_path': str(tmp_path / 'Hub/data')}]}


def scanner(failures, message='An uncoordinated process uses this installation; migration refused'):
    calls = []

    def scan(self):
        calls.append(1)
        if len(calls) <= failures:
            raise MigrationError(message)
    return scan, calls


def test_own_processes_that_exit_within_the_grace_period_do_not_fail_the_scan(tmp_path, monkeypatch):
    scan, calls = scanner(3)
    monkeypatch.setattr(StoppedInstallation, '_scan_consumers', scan)
    sleeps = []
    check = StoppedInstallation.__new__(StoppedInstallation)
    check.record = record(tmp_path)
    check._unmanaged_consumers(wait=20, sleep=sleeps.append)
    assert len(calls) == 4 and sleeps == [1, 1, 1]


def test_a_real_foreign_consumer_is_still_refused_after_the_wait(tmp_path, monkeypatch):
    scan, calls = scanner(10 ** 6)
    monkeypatch.setattr(StoppedInstallation, '_scan_consumers', scan)
    clock = iter(range(0, 1000))
    monkeypatch.setattr(migration_host.time, 'monotonic', lambda: next(clock))
    check = StoppedInstallation.__new__(StoppedInstallation)
    check.record = record(tmp_path)
    with pytest.raises(MigrationError, match='uncoordinated'):
        check._unmanaged_consumers(wait=5, sleep=lambda seconds: None)
    assert len(calls) >= 2


def test_ownership_that_cannot_be_established_is_not_retried(tmp_path, monkeypatch):
    scan, calls = scanner(10 ** 6, 'Cannot establish local consumer ownership')
    monkeypatch.setattr(StoppedInstallation, '_scan_consumers', scan)
    check = StoppedInstallation.__new__(StoppedInstallation)
    check.record = record(tmp_path)
    with pytest.raises(MigrationError):
        check._unmanaged_consumers(wait=20, sleep=lambda seconds: pytest.fail('must not wait'))
    assert len(calls) == 1


def test_helper_gate_reports_not_idle_instead_of_raising(tmp_path, monkeypatch):
    scan, calls = scanner(10 ** 6)
    monkeypatch.setattr(StoppedInstallation, '_scan_consumers', scan)
    monkeypatch.setattr(update_helper, 'CONSUMER_WAIT', 0)
    gate = update_helper.StoppedPackages(record(tmp_path))
    with gate as idle:
        assert idle is False  # The transaction stays waiting_for_idle and is retried.
    scan_ok, _ = scanner(0)
    monkeypatch.setattr(StoppedInstallation, '_scan_consumers', scan_ok)
    with update_helper.StoppedPackages(record(tmp_path)) as idle:
        assert idle is True
