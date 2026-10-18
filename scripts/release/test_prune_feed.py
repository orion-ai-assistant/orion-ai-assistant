"""Feed pruning: what a client may still need stays, what nothing can reach goes."""
from datetime import datetime, timedelta, timezone

import pytest

from prune_feed import plan

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
ROUTER, LEGACY, LAYER, BOOT = ('orion-router-code-windows-x86_64-cpu.zip', 'orion-router-windows-x86_64-cpu.zip',
                               'orion-router-runtime-aaaa.zip', 'orion-bootstrap-windows-x64.zip')


def digest(n):
    return f'{n:064x}'


def feed(versions, *, days_between=10, current=None, extra=None):
    """One release per version: version v lists Router code digest(v) (+ the layer, the bootstrap, and from v>=3 the
    carried legacy package digest(100)). Releases are `days_between` apart, the newest one at NOW - 1 day."""
    assets, files = {'1.root.json': NOW - timedelta(days=400), 'timestamp.json': NOW, BOOT: NOW}, {}
    for v in versions:
        when = NOW - timedelta(days=1 + days_between * (max(versions) - v))
        targets = {ROUTER: {'hashes': {'sha256': digest(v)}}, LAYER: {'hashes': {'sha256': digest(50)}},
                   BOOT: {'hashes': {'sha256': digest(60)}}}
        if v >= 3:
            targets[LEGACY] = {'hashes': {'sha256': digest(100)}}
        files[f'{v}.snapshot.json'] = {'signed': {'meta': {'targets.json': {'version': v}}}}
        files[f'{v}.targets.json'] = {'signed': {'targets': targets}}
        for name in (f'{v}.snapshot.json', f'{v}.targets.json', f'{digest(v)}.{ROUTER}'):
            assets[name] = when
        for d, name in ((50, LAYER), (60, BOOT), (100, LEGACY)):
            assets.setdefault(f'{digest(d)}.{name}', NOW - timedelta(days=300))
    assets.update(extra or {})
    files['timestamp.json'] = {'signed': {'meta': {'snapshot.json': {'version': current or max(versions)}}}}
    return assets, files.__getitem__


def test_keeps_the_newest_versions_and_everything_they_list_and_deletes_the_rest():
    assets, fetch = feed(range(1, 9))
    delete, kept = plan(assets, fetch, now=NOW, keep=3)
    assert kept == [6, 7, 8]
    assert delete == {f'{v}.{k}.json' for v in range(1, 6) for k in ('snapshot', 'targets')} | \
        {f'{digest(v)}.{ROUTER}' for v in range(1, 6)}
    for name in ('1.root.json', 'timestamp.json', BOOT, f'{digest(50)}.{LAYER}', f'{digest(60)}.{BOOT}',
                 f'{digest(100)}.{LEGACY}', f'{digest(8)}.{ROUTER}'):
        assert name not in delete   # roots, the plain download, the shared layer and the carried legacy package


def test_by_default_only_the_live_version_and_what_it_lists_stay():
    assets, fetch = feed(range(1, 9), days_between=1)
    delete, kept = plan(assets, fetch, now=NOW)
    assert kept == [8]
    assert set(assets) - delete == {'1.root.json', 'timestamp.json', BOOT, '8.snapshot.json', '8.targets.json',
                                    f'{digest(8)}.{ROUTER}', f'{digest(50)}.{LAYER}', f'{digest(60)}.{BOOT}',
                                    f'{digest(100)}.{LEGACY}'}


def test_a_client_that_was_downloading_a_deleted_package_is_told_it_will_retry():
    from services.shared.update_log import describe
    for code in ('DownloadHTTPError', 'DownloadError', 'DownloadLengthMismatchError'):
        assert 'Kurulu sürüm değişmedi' in describe(code) and 'denetimde' in describe(code)


def test_a_version_superseded_within_the_grace_period_stays_for_clients_still_using_it():
    assets, fetch = feed(range(1, 9), days_between=1)   # a release a day: 8 versions within 8 days
    delete, kept = plan(assets, fetch, now=NOW, keep=3, grace=timedelta(days=7))
    assert kept == [2, 3, 4, 5, 6, 7, 8]   # 1 was superseded by 2 eight days ago
    assert delete == {'1.snapshot.json', '1.targets.json', f'{digest(1)}.{ROUTER}'}


def test_a_release_in_progress_is_never_touched():
    # Version 9 is staged (packages and metadata up) but its timestamp is not: clients are on 8.
    assets, fetch = feed(range(1, 10), current=8, extra={f'{digest(999)}.{ROUTER}': NOW})
    delete, kept = plan(assets, fetch, now=NOW, keep=3)
    assert kept == [6, 7, 8]   # the live version and the two before it
    assert not {'9.snapshot.json', '9.targets.json', f'{digest(9)}.{ROUTER}', f'{digest(999)}.{ROUTER}'} & delete


def test_unknown_assets_stay():
    assets, fetch = feed(range(1, 9), extra={'notes.txt': NOW - timedelta(days=90),
                                             'something-else.zip': NOW - timedelta(days=90)})
    delete, _ = plan(assets, fetch, now=NOW)
    assert 'notes.txt' not in delete and 'something-else.zip' not in delete


def test_an_inconsistent_feed_deletes_nothing():
    assets, fetch = feed(range(1, 9))
    del assets['8.snapshot.json']
    with pytest.raises(ValueError, match='not pruning'):
        plan(assets, fetch, now=NOW)
    assets, fetch = feed(range(1, 9))
    del assets['8.targets.json']
    with pytest.raises(ValueError, match='not pruning'):
        plan(assets, fetch, now=NOW)
