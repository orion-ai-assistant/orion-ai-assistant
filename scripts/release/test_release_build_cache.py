"""Change detection, cache invalidation, layered ZIPs, acceptance planning and sandbox pruning.

No product is started and no key is used: everything runs on tiny temporary trees.
"""
from argparse import Namespace
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import zipfile

import pytest

import accept_packages
import build_cache
import build_package
import build_release
import publish_release
import sandbox_prune
import zip_layers


def git(root, *argv):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *argv], check=True,
                   capture_output=True)


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


# --- content hashing -------------------------------------------------------------------------

def test_hash_files_sees_content_name_addition_and_removal_but_not_timestamps(tmp_path):
    write(tmp_path / 'a.txt', 'one')
    write(tmp_path / 'b.txt', 'two')

    def digest():
        return build_cache.hash_files({'a.txt': tmp_path / 'a.txt', 'b.txt': tmp_path / 'b.txt'})
    base = digest()
    os.utime(tmp_path / 'a.txt', (1, 1))
    assert digest() == base  # touching a file is not a change
    write(tmp_path / 'a.txt', 'ONE')
    assert digest() != base
    write(tmp_path / 'a.txt', 'one')
    assert digest() == base
    assert build_cache.hash_files({'a.txt': tmp_path / 'a.txt', 'c.txt': tmp_path / 'b.txt'}) != base  # rename
    assert build_cache.hash_files({'a.txt': tmp_path / 'a.txt'}) != base  # removal


def test_combine_is_label_and_value_sensitive():
    one = build_cache.combine(('a', '1'), ('b', '2'))
    assert one == build_cache.combine(('a', '1'), ('b', '2'))
    assert one != build_cache.combine(('a', '2'), ('b', '1'))
    assert one != build_cache.combine(('a', '1'), ('b', '3'))
    assert build_cache.combine(('r', {'v': 1, 'w': 2})) == build_cache.combine(('r', {'w': 2, 'v': 1}))


# --- package key / cache invalidation --------------------------------------------------------

@pytest.fixture
def router_repo(tmp_path):
    repo = tmp_path / 'router'
    write(repo / 'core/a.py', 'a = 1\n')
    write(repo / 'main.py', 'print(1)\n')
    write(repo / 'notes.md', 'not packaged\n')
    write(repo / 'dashboard/out/index.html', '<html>1</html>')
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'one')
    return repo


RELEASE = {'schema': 1, 'component': 'router', 'package_id': 'orion-router-0.0.1-windows-x86_64-cpu'}


def key(repo, release=None):
    args = Namespace(requirements=None, inputs=Path('.'), postgres=Path('.'))
    return build_package.package_key('router', release or RELEASE, args, repo)[0]


def test_package_key_changes_exactly_when_an_input_changes(router_repo):
    base = key(router_repo)
    assert key(router_repo) == base
    # Not part of the package: an untracked file and a tracked file outside the source prefixes.
    write(router_repo / 'core/untracked.py', 'ignored until tracked')
    write(router_repo / 'notes.md', 'edited notes\n')
    assert key(router_repo) == base
    # Working-tree edit of a packaged file (the builder copies the working tree, so the key must see it).
    write(router_repo / 'core/a.py', 'a = 2\n')
    edited = key(router_repo)
    assert edited != base
    write(router_repo / 'core/a.py', 'a = 1\n')
    assert key(router_repo) == base
    # A newly tracked source file, a release json bump and a rebuilt dashboard each invalidate.
    git(router_repo, 'add', 'core/untracked.py')
    assert key(router_repo) != base
    git(router_repo, 'rm', '-q', '--cached', 'core/untracked.py')
    assert key(router_repo) == base
    assert key(router_repo, {**RELEASE, 'package_id': 'orion-router-0.0.2-windows-x86_64-cpu'}) != base
    write(router_repo / 'dashboard/out/index.html', '<html>2</html>')
    assert key(router_repo) != base


def test_package_key_covers_tooling_and_runtime_inputs(router_repo, monkeypatch):
    base = key(router_repo)
    monkeypatch.setattr(build_package, 'RUNTIME_RECIPE', build_package.RUNTIME_RECIPE + 1)
    assert key(router_repo) != base  # a changed runtime recipe must not reuse an old layer's ZIP
    monkeypatch.undo()
    assert key(router_repo) == base
    original = build_cache.text_digest
    monkeypatch.setattr(build_cache, 'text_digest', lambda path: 'changed' if Path(path).name == 'router-requirements.txt'
                        else original(path))
    assert key(router_repo) != base  # a changed dependency lock rebuilds the runtime and the package


def test_runtime_key_ignores_checkout_line_endings(tmp_path):
    # A core.autocrlf checkout (CRLF) and an LF one share runtime layers; any content change still counts.
    crlf, lf, other = tmp_path / 'a.txt', tmp_path / 'b.txt', tmp_path / 'c.txt'
    crlf.write_bytes(b'x==1\r\ny==2\r\n')
    lf.write_bytes(b'x==1\ny==2\n')
    other.write_bytes(b'x==1\ny==3\n')
    assert build_cache.text_digest(crlf) == build_cache.text_digest(lf) != build_cache.text_digest(other)


def test_cache_reuses_exactly_the_stored_bytes_and_never_for_another_key(tmp_path):
    cache = build_cache.Cache(tmp_path / 'cache')
    archive, target = tmp_path / 'p.zip', tmp_path / 'p.target.json'
    archive.write_bytes(b'zip bytes')
    target.write_text('{}')
    assert cache.lookup_package('router', 'k1') is None
    cache.store_package('router', 'k1', archive, target)
    zip_path, target_path = cache.lookup_package('router', 'k1')
    assert zip_path.read_bytes() == b'zip bytes' and target_path.read_text() == '{}'
    assert cache.lookup_package('router', 'k2') is None
    assert cache.lookup_package('hub', 'k1') is None
    # Tampering with the stored ZIP (size or timestamp) turns the hit into a miss.
    zip_path.write_bytes(b'zip bytes!')
    assert cache.lookup_package('router', 'k1') is None
    # A bounded number of entries is kept (a revert or a parallel worktree still hits); never unbounded growth.
    keys = ['k%d' % i for i in range(2, 2 + build_cache.KEEP_PACKAGES)]
    for key in keys:
        cache.store_package('router', key, archive, target)
    assert not cache.package_dir('router', 'k1').exists()
    assert all(cache.lookup_package('router', key) for key in keys)
    # Write-once: storing other bytes under a key that is already cached keeps the first bytes.
    other = tmp_path / 'other.zip'
    other.write_bytes(b'different bytes')
    cache.store_package('router', keys[-1], other, target)
    assert cache.lookup_package('router', keys[-1])[0].read_bytes() != b'different bytes'


# --- layered ZIPs ----------------------------------------------------------------------------

def naive_zip(tree, destination):
    """The pre-layering writer: one pass over the whole tree."""
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(tree.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(tree).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                stream.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


@pytest.mark.skipif(os.name != 'nt', reason='the original member order is the Windows path order')
def test_layered_zip_is_byte_identical_to_a_single_pass(tmp_path):
    runtime, sources, whole = tmp_path / 'runtime', tmp_path / 'sources', tmp_path / 'whole'
    for name in ('runtime/python/python.exe', 'runtime/python/Lib/a.py', '.local_db/postgres/bin/Z.exe',
                 'tools/ffmpeg/bin/ffmpeg.exe', 'runtime-inputs.json'):
        write(runtime / name, name * 400)
    for name in ('core/a.py', 'dashboard/out/index.html', 'Run.txt', 'orion-release.json', 'cli.py'):
        write(sources / name, name * 300)
    for tree in (runtime, sources):
        for path in tree.rglob('*'):
            if path.is_file():
                target = whole / path.relative_to(tree)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    zip_layers.write_layer(runtime, tmp_path / 'layer.zip')
    zip_layers.assemble(tmp_path / 'layered.zip', zip_layers.tree_members(sources), [tmp_path / 'layer.zip'])
    naive_zip(whole, tmp_path / 'naive.zip')
    assert (tmp_path / 'layered.zip').read_bytes() == (tmp_path / 'naive.zip').read_bytes()
    with zipfile.ZipFile(tmp_path / 'layered.zip') as archive:
        assert archive.testzip() is None  # CRCs of the copied members are intact


def test_layer_and_sources_must_not_overlap(tmp_path):
    runtime, sources = tmp_path / 'runtime', tmp_path / 'sources'
    write(runtime / 'shared.txt')
    write(sources / 'shared.txt')
    zip_layers.write_layer(runtime, tmp_path / 'layer.zip')
    with pytest.raises(ValueError, match='both provide'):
        zip_layers.assemble(tmp_path / 'out.zip', zip_layers.tree_members(sources), [tmp_path / 'layer.zip'])


# --- acceptance planning ---------------------------------------------------------------------

def candidates(tmp_path, **digests):
    directory = tmp_path / 'candidates'
    directory.mkdir(exist_ok=True)
    for kind, digest in digests.items():
        (directory / f'{kind}.target.json').write_text(json.dumps({
            'path': f'{kind}.zip', 'hashes': {'sha256': digest}, 'custom': {'component': kind, 'package_id': f'{kind}-1'}}))
    return directory


@pytest.fixture
def tooling(monkeypatch):
    state = {'key': 'tooling-1'}
    monkeypatch.setattr(accept_packages, 'acceptance_tooling_key', lambda: state['key'])
    return state


def record_full(tmp_path, packages, tooling_key='tooling-1', success=True):
    result = tmp_path / 'sandbox' / 'result.json'
    write(result, json.dumps({'success': success, 'mode': 'full'}))
    build_cache.Cache(tmp_path / 'cache').write_record('acceptance.json', {
        'mode': 'full', 'packages': packages, 'tooling': tooling_key, 'result': str(result), 'finished': 'then'})


def test_fast_without_a_previous_full_run_becomes_full(tmp_path, tooling):
    artifacts = candidates(tmp_path, hub='h1', router='r1', client='c1')
    assert accept_packages.plan(artifacts, 'fast', tmp_path / 'cache') == ('full', None)


def test_fast_exercises_only_what_changed_and_its_peers(tmp_path, tooling):
    record_full(tmp_path, {'hub': 'h1', 'router': 'r1', 'client': 'c1'})
    cache = tmp_path / 'cache'
    assert accept_packages.plan(candidates(tmp_path, hub='h1', router='r2', client='c1'), 'fast', cache) == ('fast', {'router'})
    assert accept_packages.plan(candidates(tmp_path, hub='h1', router='r1', client='c2'), 'fast', cache) == ('fast', {'client'})
    assert accept_packages.plan(candidates(tmp_path, hub='h2', router='r1', client='c1'), 'fast', cache) == ('fast', {'hub'})
    assert accept_packages.plan(candidates(tmp_path, hub='h1', router='r1', client='c1'), 'fast', cache) == ('fast', set())
    assert accept_packages.fast_scope({'router'}) == {'router'}
    assert accept_packages.fast_scope({'client'}) == {'hub', 'client'}
    assert accept_packages.fast_scope({'hub'}) == {'hub', 'client'}
    assert accept_packages.fast_scope({'hub', 'router'}) == {'hub', 'client', 'router'}
    assert accept_packages.fast_scope({'router', 'client'}) == {'hub', 'client', 'router'}


def test_full_is_skipped_only_for_the_identical_set_and_tooling(tmp_path, tooling):
    record_full(tmp_path, {'hub': 'h1', 'router': 'r1', 'client': 'c1'})
    cache = tmp_path / 'cache'
    same = candidates(tmp_path, hub='h1', router='r1', client='c1')
    assert accept_packages.plan(same, 'full', cache, reuse_accepted=True)[0] == 'skip'
    assert accept_packages.plan(same, 'full', cache)[0] == 'full'  # skipping is opt-in
    tooling['key'] = 'tooling-2'  # the acceptance code or the bootstrap sources changed
    assert accept_packages.plan(same, 'full', cache, reuse_accepted=True)[0] == 'full'
    tooling['key'] = 'tooling-1'
    other = candidates(tmp_path, hub='h1', router='r2', client='c1')
    assert accept_packages.plan(other, 'full', cache, reuse_accepted=True)[0] == 'full'


def test_a_failed_or_missing_result_is_never_a_baseline(tmp_path, tooling):
    record_full(tmp_path, {'hub': 'h1', 'router': 'r1', 'client': 'c1'}, success=False)
    same = candidates(tmp_path, hub='h1', router='r1', client='c1')
    assert accept_packages.plan(same, 'full', tmp_path / 'cache', reuse_accepted=True)[0] == 'full'
    assert accept_packages.plan(same, 'fast', tmp_path / 'cache') == ('full', None)


def test_publish_refuses_a_fast_acceptance_result(tmp_path):
    result = tmp_path / 'result.json'
    staging = tmp_path / 'staging'
    staging.mkdir()
    write(result, json.dumps({'success': True, 'mode': 'fast', 'artifacts': []}))
    with pytest.raises(ValueError, match='full acceptance'):
        publish_release.write_acceptance(staging, result, tmp_path / 'acceptance.json')
    write(result, json.dumps({'success': False, 'fast_success': True, 'mode': 'fast', 'artifacts': []}))
    with pytest.raises(ValueError):
        publish_release.write_acceptance(staging, result, tmp_path / 'acceptance.json')


# --- web build change detection --------------------------------------------------------------

def test_web_key_follows_sources_but_not_ignored_build_output(tmp_path):
    repo = tmp_path / 'flutter'
    write(repo / 'main_app/app/lib/main.dart', 'void main() {}')
    write(repo / '.gitignore', 'build/\n')
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'one')
    step = {'prefixes': ['main_app'], 'command': ['flutter', 'build']}
    base = build_release.web_key(step, repo)
    write(repo / 'main_app/app/build/web/index.html', 'built output')  # ignored: must not matter
    assert build_release.web_key(step, repo) == base
    write(repo / 'main_app/app/lib/main.dart', 'void main() { print(1); }')  # uncommitted edit counts
    assert build_release.web_key(step, repo) != base
    write(repo / 'main_app/app/lib/main.dart', 'void main() {}')
    write(repo / 'main_app/app/lib/new.dart', 'class A {}')  # new untracked source file counts
    assert build_release.web_key(step, repo) != base
    assert build_release.web_key({**step, 'command': ['flutter', 'build', '--profile']}, repo) != base


# --- sandbox pruning -------------------------------------------------------------------------

def sandbox(base, name, age_hours, size=10):
    path = base / name
    write(path / 'data/file.bin', 'x' * size)
    (path / 'data/file.bin').chmod(stat.S_IREAD)  # read-only files must not stop the cleanup
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_prune_keeps_newest_pinned_and_the_accepted_run(tmp_path):
    base = tmp_path / 'lifecycle-tests'
    base.mkdir()
    runs = [sandbox(base, f'stage05-aaaaaaa{i}', age_hours=2 + i) for i in range(6)]  # 0 newest .. 5 oldest
    write(runs[4] / 'PINNED', 'keep me')
    write(tmp_path / 'noise.json', '{}')
    write(base / 'notes.json', '{}')
    other = sandbox(base, 'product-bbbbbbbb', age_hours=100)
    young = sandbox(base, 'stage05-cccccccc', age_hours=0.1)
    build_cache.Cache(tmp_path / 'cache').write_record('acceptance.json', {'result': str(runs[5] / 'result.json')})
    kept, doomed, freed = sandbox_prune.prune(base, keep=2, cache=tmp_path / 'cache', say=lambda *a: None)
    survivors = {p.name for p in base.iterdir() if p.is_dir()}
    assert survivors == {young.name, runs[0].name,      # newest two of the family (the young run counts)
                         runs[4].name, runs[5].name,    # PINNED and the last accepted run
                         other.name}                    # other families are untouched by default
    assert {p.name for p in doomed} == {runs[1].name, runs[2].name, runs[3].name}
    assert freed >= 30 and (base / 'notes.json').exists()


def test_prune_dry_run_deletes_nothing_and_all_prefixes_applies_the_rule_to_every_family(tmp_path):
    base = tmp_path / 'lifecycle-tests'
    base.mkdir()
    old = [sandbox(base, f'stage05-dddddd{i:02d}', age_hours=10 + i) for i in range(4)]
    product = [sandbox(base, f'product-eeeeee{i:02d}', age_hours=10 + i) for i in range(4)]
    sandbox_prune.prune(base, keep=1, dry_run=True, say=lambda *a: None)
    assert all(p.exists() for p in old + product)
    sandbox_prune.prune(base, keep=1, all_prefixes=True, say=lambda *a: None)
    assert [p.exists() for p in old] == [True, False, False, False]
    assert [p.exists() for p in product] == [True, False, False, False]
