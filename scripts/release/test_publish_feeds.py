"""publish_release.py publishes per feed: which components go where, per-feed sources, acceptance subset."""
import json
from pathlib import Path

import pytest

import publish_release as P
from services.shared.automatic_updates import normalize_sources, validate_source


def candidate(directory, component, sequence=1):
    custom = {'component': component, 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu',
              'package_id': f'orion-{component}-{sequence}.0.0-windows-x86_64-cpu'}
    archive = directory / (custom['package_id'] + '.zip')
    archive.write_bytes(component.encode() * 10)
    (directory / f'{component}.target.json').write_text(json.dumps({'path': archive.name, 'custom': custom}))
    return custom['package_id']


@pytest.fixture
def candidates(tmp_path):
    directory = tmp_path / 'candidates'
    directory.mkdir()
    return directory, {kind: candidate(directory, kind) for kind in ('hub', 'client', 'router')}


def test_each_feed_stages_only_its_own_components(candidates, tmp_path):
    directory, ids = candidates
    hub = P.stage_stable_names(directory, tmp_path / 'hub-stage', P.FEEDS['hub']['components'])
    router = P.stage_stable_names(directory, tmp_path / 'router-stage', P.FEEDS['router']['components'])
    assert set(hub) == {'hub', 'client'} and set(router) == {'router'}
    assert sorted(p.name for p in (tmp_path / 'router-stage').glob('*.zip')) == ['orion-router-windows-x86_64-cpu.zip']
    assert not list((tmp_path / 'hub-stage').glob('*router*'))


def test_hub_feed_source_lists_both_feeds_and_keeps_the_original_cache(candidates, tmp_path):
    directory, _ = candidates
    names = P.stage_stable_names(directory, tmp_path / 'stage', P.FEEDS['hub']['components'])
    router_name = P.stable_name({'component': 'router', 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu'})
    source = P.build_source('hub', names, repo=P.DEFAULT_REPO, tag='feed', router_repo=P.ROUTER_REPO,
                            router_target=router_name)
    feeds = normalize_sources(source)
    assert [f['cache'] for f in feeds] == ['release-cache', 'release-cache-router']
    assert feeds[0]['targets'] == names and feeds[0]['metadata_url'] == P.feed_url(P.DEFAULT_REPO, 'feed')
    assert feeds[1]['targets'] == {'router': router_name}
    assert feeds[1]['metadata_url'] == P.feed_url(P.ROUTER_REPO, 'feed') and 'orion-router' in feeds[1]['metadata_url']
    assert 'router' not in feeds[0]['targets']          # The Hub feed no longer lists a Router target.
    only_hub = P.build_source('hub', names, repo=P.DEFAULT_REPO, tag='feed')
    assert len(normalize_sources(only_hub)) == 1


def test_router_feed_source_is_router_only(candidates, tmp_path):
    directory, _ = candidates
    names = P.stage_stable_names(directory, tmp_path / 'stage', P.FEEDS['router']['components'])
    source = validate_source(P.build_source('router', names, repo=P.ROUTER_REPO, tag='feed',
                                            router_repo=P.DEFAULT_REPO))
    feeds = normalize_sources(source)
    assert len(feeds) == 1 and set(feeds[0]['targets']) == {'router'}   # Never points a Router-only install at the Hub feed.
    assert feeds[0]['cache'] == 'release-cache-router'


def test_acceptance_covers_a_feed_subset_but_never_an_unaccepted_package(candidates, tmp_path):
    directory, ids = candidates
    stage = tmp_path / 'router-stage'
    P.stage_stable_names(directory, stage, ('router',))
    accepted = tmp_path / 'result.json'
    accepted.write_text(json.dumps({'success': True, 'mode': 'full', 'artifacts': [{'package_id': i} for i in ids.values()]}))
    P.write_acceptance(stage, accepted, tmp_path / 'acceptance.json')
    value = json.loads((tmp_path / 'acceptance.json').read_text())
    assert set(value['packages']) == {ids['router']}
    accepted.write_text(json.dumps({'success': True, 'mode': 'full', 'artifacts': [{'package_id': ids['hub']}]}))
    with pytest.raises(ValueError, match='different packages'):
        P.write_acceptance(stage, accepted, tmp_path / 'acceptance2.json')
    accepted.write_text(json.dumps({'success': True, 'mode': 'fast', 'artifacts': []}))
    with pytest.raises(ValueError, match='full acceptance'):
        P.write_acceptance(stage, accepted, tmp_path / 'acceptance3.json')


def test_default_repositories_and_tags_are_per_feed():
    assert P.FEEDS['hub']['repo'] == 'orion-ai-assistant/orion-ai-assistant'
    assert P.FEEDS['router']['repo'] == 'orion-ai-assistant/orion-router'
    assert {f['tag'] for f in P.FEEDS.values()} == {'feed'}
    assert P.feed_url('orion-ai-assistant/orion-router', 'feed') == \
        'https://github.com/orion-ai-assistant/orion-router/releases/download/feed/'


def _staged(tmp_path, sequence, digest):
    name = 'orion-hub-windows-x86_64-cpu.zip'
    (tmp_path / (name[:-4] + '.target.json')).write_text(
        json.dumps({'hashes': {'sha256': digest}, 'custom': {'component': 'hub', 'sequence': sequence}}))
    return {'hub': name}


def test_package_gate_refuses_changed_bytes_or_a_lower_sequence_but_allows_new_ones(tmp_path):
    online = {'signed': {'targets': {'orion-hub-windows-x86_64-cpu.zip':
                                     {'hashes': {'sha256': 'aa'}, 'custom': {'sequence': 5}}}}}
    P.package_gate(online, tmp_path, _staged(tmp_path, 5, 'aa'))   # identical bytes: fine
    P.package_gate(online, tmp_path, _staged(tmp_path, 6, 'bb'))   # newer sequence: fine
    P.package_gate(None, tmp_path, _staged(tmp_path, 1, 'cc'))     # nothing online yet: fine
    with pytest.raises(ValueError, match='already published'):
        P.package_gate(online, tmp_path, _staged(tmp_path, 5, 'bb'))
    with pytest.raises(ValueError, match='below'):
        P.package_gate(online, tmp_path, _staged(tmp_path, 4, 'aa'))


def test_unchanged_plain_bootstrap_is_not_uploaded_again(tmp_path):
    bundle = tmp_path / P.BOOTSTRAP_ASSET
    bundle.write_bytes(b'bootstrap')
    digest = P.sha256(bundle)
    assert not P.needs_upload(bundle, {P.BOOTSTRAP_ASSET: digest})
    assert P.needs_upload(bundle, {P.BOOTSTRAP_ASSET: '0' * 64})       # Changed bytes under the stable name.
    assert P.needs_upload(bundle, {P.BOOTSTRAP_ASSET: None})           # No digest reported: upload to be safe.
    assert P.needs_upload(bundle, {})
    versioned = tmp_path / '7.targets.json'
    versioned.write_text('{}')
    assert not P.needs_upload(versioned, {'7.targets.json': None})
    assert P.needs_upload(versioned, {'7.targets.json': None}, 7)            # This version's own metadata.
    assert not P.needs_upload(versioned, {'7.targets.json': P.sha256(versioned)}, 7)
