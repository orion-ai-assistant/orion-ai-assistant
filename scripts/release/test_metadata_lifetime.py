"""Signed metadata never locks an installation out: 100-year lifetimes, and a renewed root an old client follows."""
from datetime import datetime, timedelta, timezone
import shutil
import tempfile
from pathlib import Path

import pytest
from tuf.api.metadata import Metadata
from tuf.ngclient import Updater

import publish_metadata as publisher
import release_keys
from test_multi_feed import Feeds, package

URL = 'https://feed.example/'


@pytest.fixture
def tmp(request):
    base = Path.home() / '.orion-lifecycle-tests'  # never inside a real %LOCALAPPDATA%/Orion
    base.mkdir(parents=True, exist_ok=True)
    owned = tempfile.TemporaryDirectory(prefix='metadata-lifetime-', dir=base)
    request.addfinalizer(owned.cleanup)
    return Path(owned.name)


def release(tmp, root, signer, version, sequence):
    artifacts = tmp / f'artifacts-{version}'
    artifacts.mkdir()
    package(artifacts, 'hub', sequence)
    out = publisher.publish(artifacts, tmp / f'repo-{version}', root, {r: [signer] for r in
                            ('root', 'targets', 'snapshot', 'timestamp')}, version=version,
                            expiry_days=publisher.SINGLE_KEY_EXPIRY_DAYS)
    return Path(out)


def test_an_installation_follows_a_renewed_root_and_metadata_lives_a_century(tmp, monkeypatch):
    monkeypatch.setenv(release_keys.PASSPHRASE_ENV, 'correct horse battery')
    key, root = release_keys.init(tmp / 'keys', tmp / 'trusted-root.json')
    shipped = root.read_bytes()   # what the installer bundled
    signer = release_keys.load_signer(key)
    first = release(tmp, root, signer, 1, 1)
    fetcher = Feeds({URL: first / 'metadata'})
    targets = Feeds({URL: first / 'targets'})

    class Both(Feeds):
        def _fetch(self, url):
            source = fetcher if url.rsplit('/', 1)[-1].endswith('.json') else targets
            return source._fetch(url)
    (tmp / 'cache').mkdir()
    client = Updater(str(tmp / 'cache'), URL, target_base_url=URL, bootstrap=shipped, fetcher=Both({}))
    client.refresh()
    in_a_century = datetime.now(timezone.utc) + timedelta(days=36500 - 2)
    assert Metadata.from_file(str(tmp / 'cache/timestamp.json')).signed.expires > in_a_century

    version, expires = release_keys.renew_root(key, root)
    assert version == 2 and expires > in_a_century
    second = release(tmp, root, signer, 2, 2)
    shutil.copyfile(first / 'metadata/1.root.json', second / 'metadata/1.root.json')  # the feed keeps every root
    fetcher.directories, targets.directories = {URL: second / 'metadata'}, {URL: second / 'targets'}
    client = Updater(str(tmp / 'cache'), URL, target_base_url=URL, bootstrap=shipped, fetcher=Both({}))
    client.refresh()
    assert Metadata.from_file(str(tmp / 'cache/root.json')).signed.version == 2
    assert client.get_targetinfo(next(iter(Metadata.from_file(str(second / 'metadata/2.targets.json'))
                                           .signed.targets))).custom['sequence'] == 2


def test_an_installation_whose_root_already_expired_recovers_with_the_next_renewed_root(tmp, monkeypatch):
    # Even if nobody released for longer than the root lived: the next release (same key) renews the root and every
    # installation follows it. Only the final root of the chain must be valid, not the one the PC shipped with.
    import time
    monkeypatch.setenv(release_keys.PASSPHRASE_ENV, 'correct horse battery')
    monkeypatch.setattr(release_keys, 'ROOT_VALID_DAYS', 3 / 86400)   # a root that lives 3 seconds
    key, root = release_keys.init(tmp / 'keys', tmp / 'trusted-root.json')
    shipped = root.read_bytes()
    signer = release_keys.load_signer(key)
    time.sleep(3.5)
    assert Metadata.from_bytes(shipped).signed.expires < datetime.now(timezone.utc)   # long expired
    monkeypatch.setattr(release_keys, 'ROOT_VALID_DAYS', 36500)
    release_keys.renew_root(key, root)
    repo = release(tmp, root, signer, 1, 7)
    shutil.copyfile(tmp / 'trusted-root.json', repo / 'metadata/2.root.json')
    (repo / 'metadata/1.root.json').write_bytes(shipped)
    fetcher = Feeds({URL: repo / 'metadata'})
    files = Feeds({URL: repo / 'targets'})

    class Both(Feeds):
        def _fetch(self, url):
            return (fetcher if url.endswith('.json') else files)._fetch(url)
    (tmp / 'cache').mkdir()
    client = Updater(str(tmp / 'cache'), URL, target_base_url=URL, bootstrap=shipped, fetcher=Both({}))
    client.refresh()
    assert Metadata.from_file(str(tmp / 'cache/root.json')).signed.version == 2


def test_release_renews_a_short_lived_root_once(tmp, monkeypatch):
    monkeypatch.setenv(release_keys.PASSPHRASE_ENV, 'correct horse battery')
    key, root = release_keys.init(tmp / 'keys', tmp / 'trusted-root.json')
    monkeypatch.setattr(release_keys, 'ROOT_VALID_DAYS', 365 * 5)   # as the first root was made
    release_keys.renew_root(key, root)                                  # version 2, 5 years
    monkeypatch.setattr(release_keys, 'ROOT_VALID_DAYS', 36500)
    version, expires = release_keys.renew_if_needed(key, root)
    assert version == 3 and expires > datetime.now(timezone.utc) + timedelta(days=36000)
    assert release_keys.renew_if_needed(key, root) is None              # already long-lived: left alone


def test_renew_root_refuses_a_key_that_does_not_sign_the_root(tmp, monkeypatch):
    monkeypatch.setenv(release_keys.PASSPHRASE_ENV, 'correct horse battery')
    _, root = release_keys.init(tmp / 'keys', tmp / 'trusted-root.json')
    other, _ = release_keys.init(tmp / 'other', tmp / 'other-root.json')
    before = root.read_bytes()
    with pytest.raises(ValueError, match='does not sign'):
        release_keys.renew_root(other, root)
    assert root.read_bytes() == before


def test_a_bootstrap_with_the_renewed_root_is_the_same_trust_but_another_key_is_not(tmp, monkeypatch):
    # The 0.2.6 bootstraps compared root bytes and refused every bootstrap after the first renewal (RootMismatch).
    from services.shared.bootstrap_update import same_trust
    monkeypatch.setenv(release_keys.PASSPHRASE_ENV, 'correct horse battery')
    key, root = release_keys.init(tmp / 'keys', tmp / 'trusted-root.json')
    shipped = root.read_bytes()
    release_keys.renew_root(key, root)
    renewed = root.read_bytes()
    _, other = release_keys.init(tmp / 'other', tmp / 'other-root.json')
    assert same_trust(shipped, shipped) and same_trust(shipped, renewed)
    assert not same_trust(renewed, shipped)                  # never back to an older root
    assert not same_trust(shipped, other.read_bytes())       # a different key is a real rotation
