"""A watcher of the trust database reads it only when it changed (and every few seconds anyway)."""
from orion.kernel import local_security
from orion.kernel.local_security import SecurityStore, TRUST_FULL_CHECK, TrustWatch


def test_the_database_is_read_again_only_when_it_changed_or_after_the_full_check(tmp_path):
    store = SecurityStore(tmp_path / 'devices.sqlite3')
    device, _ = store.claim(store.create_pairing()[0], 'Telefon', 'android', '127.0.0.1')
    now = [100.0]
    watch = TrustWatch(store.path, clock=lambda: now[0])
    assert watch.due()  # Never read: read now.
    watch.read()
    for _ in range(5):
        now[0] += .25
        assert not watch.due()  # Nothing written: no query, four times a second, for as long as nothing happens.
    store.revoke(device)
    assert watch.due()  # A revocation (any write) is seen at the next look.
    watch.read()
    assert not watch.due()
    now[0] += TRUST_FULL_CHECK
    assert watch.due()  # Read anyway every few seconds: a stamp that lags cannot keep a revoked device in.


def test_a_write_during_the_read_is_seen_the_next_time(tmp_path, monkeypatch):
    store = SecurityStore(tmp_path / 'devices.sqlite3')
    watch = TrustWatch(store.path)
    assert watch.due()
    store.remove_router('written-while-the-watcher-reads')  # After the stamp was taken, before read() records it.
    watch.read()
    assert watch.due()


def test_a_missing_database_has_a_stamp_too(tmp_path):
    assert local_security.store_stamp(tmp_path / 'absent.sqlite3') == (None, None)
