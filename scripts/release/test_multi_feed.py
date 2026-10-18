"""Hub/Client feed + Router feed: separate signed repositories, one transaction, independent failures."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import zipfile

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.exceptions import DownloadError, DownloadHTTPError
from tuf.api.metadata import Metadata, Root
from tuf.ngclient.fetcher import FetcherInterface

from test_release_packages import publisher
from test_update_helper import Host
from services.shared.automatic_updates import (AutomaticUpdates, merge_sources, normalize_sources, source_value,
                                               validate_source)
from services.shared.installation_registry import Registry, component_record, new_registry
from services.shared.release_packages import ReleaseClient, record_prepared_package, sha256
from services.shared.update_helper import UpdateError, UpdateHelper

HUB = 'https://hub-feed.example/'
ROUTER = 'https://router-feed.example/'


def feed(url, targets, cache=None):
    value = {'metadata_url': url + 'metadata/', 'targets_url': url + 'targets/', 'targets': targets}
    return {**value, 'cache': cache} if cache else value


def test_source_forms_stay_compatible_and_validate():
    legacy = {'schema': 1, 'channel': 'stable', 'metadata_url': HUB + 'metadata/', 'targets_url': HUB + 'targets/',
              'targets': {'hub': 'a.zip', 'router': 'b.zip'}}
    assert validate_source(legacy) is legacy
    only = normalize_sources(legacy)
    assert len(only) == 1 and only[0]['cache'] == 'release-cache'
    both = {'schema': 2, 'channel': 'stable', 'sources': [feed(HUB, {'hub': 'a.zip'}, 'release-cache'),
                                                         feed(ROUTER, {'router': 'r.zip'})]}
    feeds = normalize_sources(both)
    assert feeds[0]['cache'] == 'release-cache' and feeds[1]['cache'].startswith('release-cache-')
    for broken in (
            {**both, 'sources': [feed(HUB, {'hub': 'a.zip'}), feed(HUB, {'client': 'c.zip'})]},        # same feed twice
            {**both, 'sources': [feed(HUB, {'hub': 'a.zip'}), feed(ROUTER, {'hub': 'b.zip'})]},        # component in two feeds
            {**both, 'sources': [feed('http://hub-feed.example/', {'hub': 'a.zip'})]},                 # not HTTPS
            {**both, 'sources': [feed('https://user:pw@hub-feed.example/', {'hub': 'a.zip'})]},        # credentials
            {**both, 'sources': [feed(HUB, {'hub': 'a.zip'}, 'cache/../x')]},                          # cache escape
            {**both, 'sources': []}):
        with pytest.raises(UpdateError):
            validate_source(broken)


def test_merge_moves_router_to_its_own_feed_and_keeps_the_hub_cache():
    old = {'schema': 1, 'channel': 'stable', 'metadata_url': HUB + 'metadata/', 'targets_url': HUB + 'targets/',
           'targets': {'hub': 'a.zip', 'client': 'c.zip', 'router': 'old-r.zip'}}
    new = {'schema': 2, 'channel': 'stable', 'sources': [feed(HUB, {'hub': 'a2.zip', 'client': 'c2.zip'}, 'release-cache'),
                                                        feed(ROUTER, {'router': 'r.zip'}, 'release-cache-router')]}
    merged = merge_sources(old, new)
    feeds = normalize_sources(merged)
    assert [f['targets'] for f in feeds] == [{'hub': 'a2.zip', 'client': 'c2.zip'}, {'router': 'r.zip'}]
    assert feeds[0]['cache'] == 'release-cache'
    assert merge_sources(merged, new) == merged  # idempotent
    # A Router-only bootstrap never drops the Hub feed that a later "add Hub" registered.
    router_only = {'schema': 1, 'channel': 'stable', 'metadata_url': ROUTER + 'metadata/',
                   'targets_url': ROUTER + 'targets/', 'targets': {'router': 'r3.zip'}}
    kept = normalize_sources(merge_sources(merged, router_only))
    assert {k for f in kept for k in f['targets']} == {'hub', 'client', 'router'}
    assert {f['metadata_url'] for f in kept} == {HUB + 'metadata/', ROUTER + 'metadata/'}
    assert source_value(kept[:1])['schema'] == 1


# --- two real signed repositories -------------------------------------------------------------

def manifest(kind, sequence):
    caps = ['lifecycle.update-quarantine.v1']
    requires = {'hub': {'client': [1, 1], 'router': [2, 2]}, 'router': {'hub': [1, 1]}, 'client': {}}[kind]
    if kind == 'router':
        caps += ['hubs.accounts.get', 'hubs.account-status.get']
    return {'schema': 1, 'component': kind, 'package_id': f'orion-{kind}-{sequence}.0.0-windows-x86_64-cpu',
            'version': f'{sequence}.0.0', 'sequence': sequence, 'platform': 'windows', 'arch': 'x86_64',
            'profile': 'cpu', 'api': {'hub': 1, 'router': 2, 'client': 1}[kind], 'requires': requires, 'capabilities': caps,
            'data_schema': 1, 'rollback': 'same-schema-only', 'entrypoint': 'run.py'}


def package(directory, kind, sequence):
    value = manifest(kind, sequence)
    path = directory / (value['package_id'] + '.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('orion-release.json', json.dumps(value))
        archive.writestr('run.py', 'fixture')
    (directory / f'{kind}.target.json').write_text(json.dumps({
        'path': path.name, 'length': path.stat().st_size, 'hashes': {'sha256': sha256(path)}, 'custom': value}))
    return path.name


class Feeds(FetcherInterface):
    """Routes https://<feed>.example/... to that feed's repository directory; either can be offline."""
    def __init__(self, directories):
        self.directories, self.offline = directories, set()

    def _fetch(self, url):
        for prefix, directory in self.directories.items():
            if url.startswith(prefix):
                if prefix in self.offline:
                    raise DownloadError('Offline fixture')
                path = directory / url[len(prefix):]
                if not path.is_file():
                    raise DownloadHTTPError('Missing fixture', 404)
                yield path.read_bytes()
                return
        raise DownloadHTTPError('Unknown feed', 404)


@pytest.fixture
def feeds(request):
    """Two signed repositories (Hub feed: hub+client, Router feed: router), versions 1 and 2, one root."""
    base = Path.home() / '.orion-lifecycle-tests'  # never inside a real %LOCALAPPDATA%/Orion
    base.mkdir(parents=True, exist_ok=True)
    owned = tempfile.TemporaryDirectory(prefix='multi-feed-', dir=base)
    request.addfinalizer(owned.cleanup)
    tmp = Path(owned.name)
    signers = {role: [CryptoSigner.generate_ed25519()] for role in ('root', 'targets', 'snapshot', 'timestamp')}
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
    for role, keys in signers.items():
        root.signed.add_key(keys[0].public_key, role)
    root.sign(signers['root'][0])
    trusted = tmp / 'trusted-root.json'
    trusted.write_bytes(root.to_bytes())
    repositories, names = {}, {}
    for url, kinds in ((HUB, ('hub', 'client')), (ROUTER, ('router',))):
        for version in (1, 2):
            artifacts = tmp / f'{kinds[0]}-artifacts-{version}'
            artifacts.mkdir()
            for kind in kinds:
                names[(kind, version)] = package(artifacts, kind, version)
            out = publisher.publish(artifacts, tmp / f'{kinds[0]}-repo-{version}', trusted, signers, version=version)
            repositories.setdefault(url, {})[version] = out
    fetcher = Feeds({url: repos[1] for url, repos in repositories.items()})

    def client(source):
        return ReleaseClient(trusted_root=trusted, cache=tmp / source['cache'], metadata_url=source['metadata_url'],
                             targets_url=source['targets_url'], fetcher=fetcher)

    def source(version, kinds=('hub', 'client', 'router')):
        sources = []
        if {'hub', 'client'} & set(kinds):
            sources.append(feed(HUB, {k: names[(k, version)] for k in ('hub', 'client') if k in kinds}, 'release-cache'))
        if 'router' in kinds:
            sources.append(feed(ROUTER, {'router': names[('router', version)]}, 'release-cache-router'))
        return validate_source({'schema': 2, 'channel': 'stable', 'sources': sources})

    def release_to(version):
        fetcher.directories = {url: repos[version] for url, repos in repositories.items()}
    return type('Feeds', (), dict(tmp=tmp, trusted=trusted, fetcher=fetcher, names=names, client=staticmethod(client),
                                  source=staticmethod(source), release_to=staticmethod(release_to)))


@pytest.fixture
def world(feeds, monkeypatch):
    tmp, client, names = feeds.tmp, feeds.client, feeds.names

    def install(kind):
        source = {'hub': feed(HUB, {}, 'release-cache'), 'router': feed(ROUTER, {}, 'release-cache-router')}[kind]
        checker = client(source)
        _, targets = checker.check([names[(kind, 1)]])
        target = targets[names[(kind, 1)]]
        directory = tmp / kind.title()
        tree = checker.prepare(target, directory / 'releases' / target.custom['package_id'])
        data, state = directory / 'data', directory / 'state'
        data.mkdir()
        state.mkdir()
        return component_record(kind, tree, distribution='packaged', data_path=data, state_path=state,
                                ownership='owned', management='local', package=record_prepared_package(tree, state, target))

    store = Registry(tmp / 'install.json')
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(store.path))
    store.write(new_registry(tmp, [install('hub'), install('router')], preferences={'automatic_updates': True}))
    feeds.release_to(2)  # A new release round was published in both feeds.
    (store.path.parent / 'bootstrap').mkdir()
    (store.path.parent / 'bootstrap/release-source.json').write_text(json.dumps(feeds.source(2, ('hub', 'router'))))
    host = Host()
    now = [2_000_000_000.0]
    service = AutomaticUpdates(store, helper=UpdateHelper(store, host=host), client_factory=client, clock=lambda: now[0])
    AutomaticUpdates(store, helper=service.helper, scope='router').preferences(enabled=True)
    return service, feeds.fetcher, host


def test_hub_and_router_from_two_feeds_stage_as_one_transaction(world):
    service, fetcher, host = world
    service.check(with_router=True)
    tx = service.helper.status()
    assert tx['phase'] == 'staged' and sorted(tx['changes']) == ['hub', 'router']
    result = service.helper.resume()
    assert result['phase'] == 'completed', result.get('reason')
    assert {c['kind']: json.loads((Path(c['code_path']) / 'orion-release.json').read_text())['sequence']
            for c in service.registry.read()['components']} == {'hub': 2, 'router': 2}


def test_unreachable_router_feed_does_not_block_the_hub_feed(world):
    service, fetcher, host = world
    fetcher.offline.add(ROUTER)
    service.check(with_router=True)
    state = json.loads((service.base / 'automatic.json').read_text())
    assert state['error'] is None and state['feed_errors']
    assert service.helper.status()['changes'] == ['hub']


def test_every_feed_failing_is_a_failed_check(world):
    service, fetcher, host = world
    fetcher.offline.update({HUB, ROUTER})
    service.check(with_router=True)
    assert service.helper.status() is None
    assert service.status()['error'] == 'UpdateError'


def router_scope(service):
    return AutomaticUpdates(service.registry, helper=service.helper, client_factory=service.client_factory,
                            clock=service.clock, scope='router')


def test_a_waiting_router_only_update_is_replaced_by_the_whole_newer_set(world):
    # Seen on the owner's PC: Router 0.2.5 waited for "Şimdi güncelle" while Hub/Client/Router 0.2.6 came out; every
    # check was skipped as "pending", then the Router arrived alone and Hub/Client one round later.
    service, fetcher, host = world
    fetcher.offline.add(HUB)
    router_scope(service).check(manual=True)
    waiting = service.helper.status()
    assert waiting['phase'] == 'staged' and waiting['changes'] == ['router']
    fetcher.offline.discard(HUB)
    service.check(now=True, with_router=True)
    tx = service.helper.status()
    assert tx['operation_id'] != waiting['operation_id'] and sorted(tx['changes']) == ['hub', 'router']
    assert tx['phase'] == 'staged'
    old = json.loads((service.helper.base / waiting['operation_id'] / 'journal.json').read_text(encoding='utf-8'))
    assert old['phase'] == 'failed' and old['reason'] == 'Superseded'
    assert service.helper.resume()['phase'] == 'completed'   # One transaction, one restart.


def test_a_waiting_update_that_is_already_the_newest_stays(world):
    service, fetcher, host = world
    service.check(with_router=True)
    waiting = service.helper.status()
    service.check(now=True, with_router=True)
    assert service.helper.status()['operation_id'] == waiting['operation_id']
    assert json.loads((service.base / 'automatic.json').read_text())['outcome'] == 'pending'


def test_a_router_only_check_never_drops_a_waiting_hub_and_router_set(world):
    service, fetcher, host = world
    service.check(with_router=True)
    waiting = service.helper.status()
    router_scope(service).check(manual=True)
    assert service.helper.status()['operation_id'] == waiting['operation_id']
    assert sorted(service.helper.status()['changes']) == ['hub', 'router']


def test_the_hub_check_covers_the_local_router_so_the_watcher_skips_the_router_only_check(world):
    service, fetcher, host = world
    assert service.covers_router()
    router_scope(service).preferences(enabled=False)   # Router updates off: Hub's check leaves it alone.
    assert not service.covers_router()


def test_router_only_scope_updates_from_the_router_feed_only(world):
    service, fetcher, host = world
    fetcher.offline.add(HUB)  # The Hub feed is irrelevant to the Router's own update check.
    AutomaticUpdates(service.registry, helper=service.helper, client_factory=service.client_factory,
                     clock=service.clock, scope='router').check(manual=True)
    assert service.helper.status()['changes'] == ['router']
