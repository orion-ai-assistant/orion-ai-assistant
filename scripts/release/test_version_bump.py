"""bump_versions.py: release.cmd raises version/sequence/package_id of exactly the packages whose bytes changed."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import bump_versions as B
import publish_release as P
from services.shared.bootstrap_update import BOOTSTRAP_TARGET


def release(component, version, sequence):
    return {'schema': 1, 'component': component, 'package_id': f'orion-{component}-{version}-windows-x86_64-cpu',
            'version': version, 'sequence': sequence, 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu',
            'api': 1, 'requires': {'hub': [1, 1]}, 'capabilities': ['a.v1']}


def git(repository, *argv):
    return subprocess.run(['git', '-C', str(repository), *argv], check=True, capture_output=True, text=True).stdout


def repository(path, files):
    path.mkdir(parents=True)
    git(path, 'init', '-q')
    git(path, 'config', 'user.email', 'test@example.invalid')
    git(path, 'config', 'user.name', 'Test')
    for name, value in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        B.write_release(path / name, value)
    git(path, 'add', '.')
    git(path, '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'init')
    return path


def candidate(directory, custom, content, split=False):
    """A candidate package like build_package.py writes it: zip + .target.json (sha256 of the zip)."""
    archive = directory / (custom['package_id'] + '.zip')
    archive.write_bytes(content)
    description = {'path': archive.name, 'length': len(content), 'hashes': {'sha256': B.sha256(archive)},
                   'custom': custom}
    if split:
        description['runtime'] = {'target': 'orion-runtime-x.zip', 'sha256': '0' * 64}
    archive.with_suffix('.target.json').write_text(json.dumps(description), encoding='utf-8')
    return description


def feed(*entries):
    """Targets metadata of a live feed: (target name, custom, sha256 hex)."""
    return {'signed': {'targets': {name: {'length': 1, 'hashes': {'sha256': digest}, 'custom': custom}
                                   for name, custom, digest in entries}}}


@pytest.fixture
def world(tmp_path):
    """Three repositories at 0.2.6 (all published with these bytes) and a candidate directory of their build."""
    hub = repository(tmp_path / 'hub', {'scripts/release/hub.json': release('hub', '0.2.6', 22),
                                        'scripts/release/bootstrap.json': release('bootstrap', '0.2.6', 14)})
    router = repository(tmp_path / 'router', {'release/package.json': release('router', '0.2.6', 22)})
    client = repository(tmp_path / 'client', {'release/package.json': release('client', '0.2.6', 20)})
    components = {'hub': {'source': hub, 'release': hub / 'scripts/release/hub.json'},
                  'router': {'source': router, 'release': router / 'release/package.json'},
                  'client': {'source': client, 'release': client / 'release/package.json'}}
    candidates = tmp_path / 'candidates'
    candidates.mkdir()

    def build(changed=()):
        for path in candidates.iterdir():
            path.unlink()
        out = {}
        for kind, config in components.items():
            custom = B.read_release(config['release'])
            # Byte-reproducible: the bytes are the release json plus the "source"; changed source = other bytes.
            content = json.dumps(custom).encode() + (b'new source' if kind in changed else b'source')
            out[kind] = candidate(candidates, custom, content, split=kind != 'client')
        return out

    published = build()
    live = {'hub': feed(*((P.stable_name(published[k]['custom'], split=k != 'client'), published[k]['custom'],
                           published[k]['hashes']['sha256']) for k in ('hub', 'client'))),
            'router': feed((P.stable_name(published['router']['custom'], split=True), published['router']['custom'],
                            published['router']['hashes']['sha256']))}
    return {'components': components, 'candidates': candidates, 'build': build, 'live': live, 'hub': hub,
            'router': router, 'client': client}


def head(path):
    return git(path, 'log', '-1', '--format=%s').strip()


def test_version_helpers():
    assert B.bump_patch('0.2.6') == '0.2.7'
    assert B.bump_patch('0.2.9') == '0.2.10'
    assert B.version_key('0.2.10') > B.version_key('0.2.9')


def test_unchanged_tree_changes_nothing(world):
    world['build']()
    before = {k: c['release'].read_bytes() for k, c in world['components'].items()}
    changes = B.bump_packages(world['candidates'], world['live'], world['components'])
    assert {c.component: c.state for c in changes} == {'hub': 'unchanged', 'client': 'unchanged', 'router': 'unchanged'}
    assert {k: c['release'].read_bytes() for k, c in world['components'].items()} == before
    assert all(head(world[k]) == 'init' for k in ('hub', 'router', 'client'))


def test_only_the_changed_component_is_bumped_and_committed(world):
    world['build'](changed={'router'})
    hub_before = world['components']['hub']['release'].read_bytes()
    changes = B.bump_packages(world['candidates'], world['live'], world['components'])
    assert {c.component: c.state for c in changes} == {'hub': 'unchanged', 'client': 'unchanged', 'router': 'bumped'}
    value = B.read_release(world['components']['router']['release'])
    assert (value['version'], value['sequence'], value['package_id']) == \
        ('0.2.7', 23, 'orion-router-0.2.7-windows-x86_64-cpu')
    assert value['capabilities'] == ['a.v1'] and value['requires'] == {'hub': [1, 1]}  # nothing else touched
    assert head(world['router']).startswith('release: Router 0.2.7 (sequence 23)')
    assert git(world['router'], 'status', '--porcelain') == ''
    assert world['components']['hub']['release'].read_bytes() == hub_before
    assert head(world['hub']) == 'init' and head(world['client']) == 'init'
    assert B.summary(changes) == 'hub unchanged, router 0.2.6 → 0.2.7 (seq 23), client unchanged'


def test_bumped_json_keeps_the_file_format(world):
    world['build'](changed={'client'})
    B.bump_packages(world['candidates'], world['live'], world['components'])
    text = world['components']['client']['release'].read_bytes()
    assert text.endswith(b'}\n') and b'\r\n' not in text and text.startswith(b'{\n  "schema": 1,')


def test_commit_takes_only_the_release_json(world):
    """Other uncommitted work in that repository is neither committed nor lost."""
    (world['client'] / 'wip.txt').write_text('work in progress')
    git(world['client'], 'add', 'wip.txt')
    world['build'](changed={'client'})
    B.bump_packages(world['candidates'], world['live'], world['components'])
    assert git(world['client'], 'show', '--name-only', '--format=', 'HEAD').split() == ['release/package.json']
    assert git(world['client'], 'status', '--porcelain').strip() == 'A  wip.txt'


def test_two_components_in_one_repository_share_one_commit_per_repository(world):
    world['build'](changed={'hub', 'client'})
    changes = B.bump_packages(world['candidates'], world['live'], world['components'])
    assert [c.state for c in changes if c.component in ('hub', 'client')] == ['bumped', 'bumped']
    assert head(world['hub']).startswith('release: Hub 0.2.7 (sequence 23)')
    assert head(world['client']).startswith('release: Client 0.2.7 (sequence 21)')


def test_hand_set_higher_version_is_kept_and_only_the_sequence_fixed(world):
    path = world['components']['hub']['release']
    B.write_release(path, {**B.read_release(path), 'version': '0.3.0',
                           'package_id': 'orion-hub-0.3.0-windows-x86_64-cpu'})
    world['build']()
    changes = B.bump_packages(world['candidates'], world['live'], world['components'])
    value = B.read_release(path)
    assert (value['version'], value['sequence'], value['package_id']) == \
        ('0.3.0', 23, 'orion-hub-0.3.0-windows-x86_64-cpu')
    assert [c.state for c in changes if c.component == 'hub'] == ['bumped']


def test_json_already_above_the_feed_is_left_as_it_is(world):
    path = world['components']['hub']['release']
    B.write_release(path, {**B.read_release(path), 'version': '0.3.0', 'sequence': 30,
                           'package_id': 'orion-hub-0.3.0-windows-x86_64-cpu'})
    before = path.read_bytes()
    world['build']()
    changes = B.bump_packages(world['candidates'], world['live'], world['components'])
    assert path.read_bytes() == before and head(world['hub']) == 'init'
    assert 'hub 0.2.6 → 0.3.0 (seq 30, as set by hand)' in B.summary(changes)


def test_sequence_passes_the_last_combined_package_too():
    """The first split package must also pass the sequence of the combined package old bootstraps still read."""
    combined = P.legacy_names(['hub'])['hub']
    split = P.stable_name({'component': 'hub', 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu'}, split=True)
    live = feed((combined, release('hub', '0.2.9', 25), 'a' * 64), (split, release('hub', '0.2.6', 22), 'b' * 64))
    state = B.published_state(live, split, [combined])
    assert state == B.Published('b' * 64, 25, '0.2.9')
    assert B.next_release(release('hub', '0.2.6', 22), state)['sequence'] == 26
    assert B.next_release(release('hub', '0.2.6', 22), state)['version'] == '0.2.10'
    assert B.published_state(live, 'missing.zip') is None


def test_component_not_on_its_feed_yet_is_left_alone(world):
    world['build'](changed={'router'})
    changes = B.bump_packages(world['candidates'], {**world['live'], 'router': None}, world['components'])
    assert [c.state for c in changes if c.component == 'router'] == ['new']
    assert head(world['router']) == 'init'


def test_candidate_from_another_json_is_refused(world):
    world['build']()
    path = world['components']['router']['release']
    B.write_release(path, release('router', '0.2.9', 30))
    with pytest.raises(ValueError, match='was not built from'):
        B.bump_packages(world['candidates'], world['live'], world['components'])


def bootstrap_world(world, tmp_path, published_bytes):
    """bootstrap.json in the hub repository and a fake builder whose bytes follow the json and the feed's source."""
    path = world['hub'] / 'scripts/release/bootstrap.json'
    built = []

    def build(source, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(path.read_bytes() + json.dumps(source, sort_keys=True).encode() + published_bytes['code'])
        built.append(output)
        return output

    world['build']()
    sources = {f: P.default_source(f, {c: n for c, (n, _) in P.candidate_names(world['candidates'],
                                                                               v['components']).items()})
               for f, v in P.FEEDS.items()}
    digest = lambda f: hashlib.sha256(path.read_bytes() + json.dumps(sources[f], sort_keys=True).encode() + b'v1').hexdigest()
    custom = B.read_release(path)
    live = {f: feed((BOOTSTRAP_TARGET, custom, digest(f))) for f in P.FEEDS}
    return path, build, built, live


def test_bootstrap_unchanged_is_reused_from_the_check(world, tmp_path):
    code = {'code': b'v1'}
    path, build, built, live = bootstrap_world(world, tmp_path, code)
    before = path.read_bytes()
    change, bundles = B.bump_bootstrap(world['candidates'], live, tmp_path / 'check', build, path, world['hub'])
    assert change.state == 'unchanged' and path.read_bytes() == before and len(built) == 2
    assert set(bundles) == {'hub', 'router'} and all(b.is_file() for b in bundles.values())


def test_bootstrap_changed_is_bumped_once_for_both_feeds_and_rebuilt(world, tmp_path):
    code = {'code': b'v1'}
    path, build, built, live = bootstrap_world(world, tmp_path, code)
    code['code'] = b'v2'  # shared scripts changed
    change, bundles = B.bump_bootstrap(world['candidates'], live, tmp_path / 'check', build, path, world['hub'])
    value = B.read_release(path)
    assert change.state == 'bumped' and (value['version'], value['sequence']) == ('0.2.7', 15)
    assert value['package_id'] == 'orion-bootstrap-0.2.7-windows-x86_64-cpu'
    assert len(built) == 4  # checked, then built again with the new json
    assert all(b'"sequence": 15' in bundles[f].read_bytes() for f in bundles)
    assert head(world['hub']).startswith('release: bootstrap 0.2.7 (sequence 15)')


def test_version_option_aligns_every_component_of_the_round_changed_or_not(world):
    world['build'](changed={'router'})
    changes = B.bump_packages(world['candidates'], world['live'], world['components'], version='0.3.1')
    assert [(c.component, c.state, c.after, c.sequence) for c in changes] == \
        [('hub', 'bumped', '0.3.1', 23), ('router', 'bumped', '0.3.1', 23), ('client', 'bumped', '0.3.1', 21)]
    for kind in ('hub', 'router', 'client'):
        value = B.read_release(world['components'][kind]['release'])
        assert value['package_id'] == f'orion-{kind}-0.3.1-windows-x86_64-cpu'
    assert head(world['client']).startswith('release: Client 0.3.1 (sequence 21)')
    assert 'release.cmd --version 0.3.1' in git(world['client'], 'log', '-1', '--format=%B')
    assert B.summary(changes).startswith('hub 0.2.6 → 0.3.1 (seq 23)')


def test_version_option_takes_published_plus_one_even_over_a_hand_set_sequence(world):
    path = world['components']['hub']['release']
    B.write_release(path, {**B.read_release(path), 'sequence': 40})
    world['build']()
    B.bump_packages(world['candidates'], world['live'], world['components'], version='0.3.1')
    assert B.read_release(path)['sequence'] == 23


def test_version_option_leaves_feeds_outside_the_round_to_the_automatic_bump(world):
    world['build']()
    changes = B.bump_packages(world['candidates'], world['live'], world['components'], version='0.3.1',
                              feeds=('router',))
    assert {c.component: c.state for c in changes} == {'hub': 'unchanged', 'router': 'bumped', 'client': 'unchanged'}
    assert B.read_release(world['components']['hub']['release'])['version'] == '0.2.6'


def test_version_must_be_above_every_published_version(world):
    live = {**world['live'], 'router': feed((BOOTSTRAP_TARGET, release('bootstrap', '0.3.1', 14), 'c' * 64))}
    B.check_version('0.3.2', live)
    with pytest.raises(ValueError, match='bootstrap 0.3.1'):
        B.check_version('0.3.1', live)
    with pytest.raises(ValueError, match='client 0.2.6, hub 0.2.6'):
        B.check_version('0.2.6', world['live'])
    with pytest.raises(SystemExit, match='must be above'):
        B.read_live(lambda f: world['live'][f], version='0.2.5')
    assert B.parse_version('0.03.1') == '0.3.1'
    for bad in ('0.3', 'v0.3.1', '0.3.1-rc1', ''):
        with pytest.raises(ValueError):
            B.parse_version(bad)


def test_version_check_uses_the_newest_sequence_not_an_old_higher_label():
    """The version label once went down (1.1.0 -> 0.2.0); only the current published version counts."""
    live = {'hub': feed(('old.zip', release('hub', '1.1.0', 5), 'a' * 64), ('new.zip', release('hub', '0.2.6', 22), 'b' * 64))}
    B.check_version('0.3.0', live)


def test_version_option_aligns_the_bootstrap_even_when_unchanged(world, tmp_path):
    code = {'code': b'v1'}
    path, build, built, live = bootstrap_world(world, tmp_path, code)
    change, _ = B.bump_bootstrap(world['candidates'], live, tmp_path / 'a', build, path, world['hub'], version='0.3.1')
    value = B.read_release(path)
    assert change.state == 'bumped' and (value['version'], value['sequence']) == ('0.3.1', 15)
    assert value['package_id'] == 'orion-bootstrap-0.3.1-windows-x86_64-cpu'


@pytest.mark.parametrize('states, publishes', [(['unchanged', 'unchanged'], False), (['unchanged', 'bumped'], True)])
def test_release_cmd_publishes_nothing_when_nothing_changed(monkeypatch, states, publishes):
    # Nothing new for anyone: no acceptance, no upload, and the feed version stays where it is.
    import sys
    import release as R
    import release_keys
    calls = []
    monkeypatch.setattr(sys, 'argv', ['release.py'])
    monkeypatch.setattr(B, 'read_live', lambda version=None: {'hub': None, 'router': None})
    monkeypatch.setattr(B, 'bump_round', lambda *a, **k: ([B.Change(f'c{i}', s, '0.1.0', '0.1.0', 1)
                                                          for i, s in enumerate(states)], {}))
    monkeypatch.setattr(release_keys, '_passphrase', lambda **k: b'x')
    monkeypatch.setattr(release_keys, 'load_signer', lambda *a, **k: None)
    monkeypatch.setattr(release_keys, 'renew_if_needed', lambda *a, **k: None)
    monkeypatch.setattr(R, 'run_build', lambda extra, accept='full': (calls.append(accept), (Path('c'), None))[1])
    monkeypatch.setattr(R, 'publish', lambda *a, **k: calls.append('publish'))
    monkeypatch.setattr(R.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.delenv(release_keys.PASSPHRASE_ENV, raising=False)
    R.main()
    monkeypatch.delenv(release_keys.PASSPHRASE_ENV, raising=False)
    assert calls == (['none', 'full', 'publish', 'publish'] if publishes else ['none'])
