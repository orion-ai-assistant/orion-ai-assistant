"""Signed self-update of the retained bootstrap: real TUF signatures, fake bundle contents.

The bundle's interpreter is never started here; `bootstrap_probe` stands in for it
(default_probe is covered separately against a real interpreter).
"""
import contextlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

from test_update_helper import installed
from test_release_packages import repo, publisher
from services.shared import bootstrap_update, runtime_layer
from services.shared.automatic_updates import AutomaticUpdates, runtime_command, watch, record_check_failure
from services.shared.release_packages import BOOTSTRAP_CODE_TARGET, BOOTSTRAP_TARGET, sha256

POINTER = 'bootstrap/automatic-runtime.json'


def make_root(path, sequence=None, root_bytes=b'', managed=False):
    for name in bootstrap_update.REQUIRED_FILES:
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_bytes(b'x')
    (path / 'trusted-root.json').write_bytes(root_bytes)
    info = {'schema': 1, 'test_only': False, 'root_sha256': sha256(path / 'trusted-root.json')}
    if sequence is not None:
        info['sequence'] = sequence
    (path / 'bootstrap.json').write_text(json.dumps(info))
    if managed:
        (path / bootstrap_update.MARKER).write_text('')
    return path


class Feed:
    def __init__(self, installed_fixture, repo_fixture, monkeypatch):
        self.helper, self.client, self.hub_name, *_ = installed_fixture
        self.base, _, _, self.signers, self.root_file, self.artifacts, self.fetcher, self.make_client = repo_fixture
        self.version = 2
        self.registry = self.helper.registry
        self.folder = self.base / 'bootstrap'
        self.folder.mkdir(exist_ok=True)
        self.root_bytes = self.root_file.read_bytes()
        (self.folder / 'trusted-root.json').write_bytes(self.root_bytes)
        (self.folder / 'release-source.json').write_text(json.dumps({
            'schema': 1, 'channel': 'stable', 'metadata_url': 'https://release.example/metadata/',
            'targets_url': 'https://release.example/targets/', 'targets': {'hub': self.hub_name}}))
        self.probe_ok = True
        self.now = [2_000_000_000.0]
        monkeypatch.setattr('services.shared.automatic_updates.time.time', lambda: self.now[0])
        self.fetched, fetch = [], self.fetcher._fetch
        monkeypatch.setattr(self.fetcher, '_fetch', lambda url: (self.fetched.append(url.rsplit('/', 1)[1]), fetch(url))[1])

    def current(self, sequence=None, name='Bootstrap-Installer', managed=False, runtime=None):
        """`runtime`: the layer reference its bootstrap.json records (a bootstrap installed from a split release's
        combined bundle)."""
        root = make_root(self.base / name, sequence, self.root_bytes, managed)
        if runtime is not None:
            info = json.loads((root / 'bootstrap.json').read_text())
            (root / 'bootstrap.json').write_text(json.dumps({**info, 'runtime': runtime}))
        (self.folder / 'automatic-runtime.json').write_text(json.dumps({'schema': 1, 'root': str(root)}))
        return root

    def downloads(self):
        """Package files fetched (metadata left out)."""
        return [name.split('.', 1)[1] for name in self.fetched if name.endswith('.zip')]

    def publish(self, sequence, *, root_bytes=None, drop=None, tamper=False, split=False, runtime=b'x'):
        """`split`: the combined bundle plus its code package and runtime layer, as build_bootstrap.py writes them."""
        self.version += 1
        release = {'schema': 1, 'component': 'bootstrap', 'package_id': f'orion-bootstrap-0.1.{sequence}-windows-x86_64-cpu',
                   'version': f'0.1.{sequence}', 'sequence': sequence, 'platform': 'windows', 'arch': 'x86_64',
                   'profile': 'cpu', 'api': 1, 'requires': {}, 'capabilities': ['bootstrap.self-update.v1'],
                   'data_schema': 1, 'rollback': 'same-schema-only', 'entrypoint': 'bootstrap.json'}
        stage = self.base / f'bundle-{sequence}-{self.version}'
        make_root(stage, sequence, root_bytes if root_bytes is not None else self.root_bytes)
        (stage / 'runtime/python/python.exe').write_bytes(runtime)
        info = json.loads((stage / 'bootstrap.json').read_text())
        info['package_id'] = release['package_id']
        for stale in self.artifacts.glob('bootstrap-*.target.json'):
            stale.unlink()
        self.reference = None
        if split:
            layer = self.base / f'layer-{sequence}-{self.version}.zip'
            runtime_layer.write(stage, layer)
            digest = sha256(layer)
            self.reference = info['runtime'] = {'target': f'orion-bootstrap-runtime-{digest[:16]}.zip',
                                                'sha256': digest, 'length': layer.stat().st_size}
            shutil.copyfile(layer, self.artifacts / self.reference['target'])
            (self.artifacts / 'bootstrap-layer.target.json').write_text(json.dumps({
                'path': self.reference['target'], 'length': self.reference['length'], 'hashes': {'sha256': digest},
                'custom': {'schema': 1, 'layer': 'runtime', 'for': 'bootstrap', 'inputs': '0' * 64}}))
        (stage / 'bootstrap.json').write_text(json.dumps(info))
        (stage / 'orion-release.json').write_text(json.dumps(release))
        if drop:
            (stage / drop).unlink()
        archive = self.artifacts / BOOTSTRAP_TARGET
        archive.unlink(missing_ok=True)
        with zipfile.ZipFile(archive, 'w') as stream:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    stream.write(path, path.relative_to(stage).as_posix())
        (self.artifacts / 'bootstrap.target.json').write_text(json.dumps({
            'path': archive.name, 'length': archive.stat().st_size, 'hashes': {'sha256': sha256(archive)},
            'custom': release}))
        if split:
            code = self.artifacts / BOOTSTRAP_CODE_TARGET
            code.unlink(missing_ok=True)
            with zipfile.ZipFile(code, 'w') as stream:
                for path in sorted(stage.rglob('*')):
                    if path.is_file() and path.relative_to(stage).parts[0] != 'runtime':
                        stream.write(path, path.relative_to(stage).as_posix())
                stream.writestr('orion-runtime.json', json.dumps(self.reference))
            (self.artifacts / 'bootstrap-code.target.json').write_text(json.dumps({
                'path': code.name, 'length': code.stat().st_size, 'hashes': {'sha256': sha256(code)},
                'custom': release, 'runtime': self.reference}))
        output = publisher.publish(self.artifacts, self.base / f'feed-{self.version}', self.root_file,
                                   self.signers, version=self.version)
        if tamper:
            for name in (BOOTSTRAP_TARGET, BOOTSTRAP_CODE_TARGET):
                for path in (output / 'targets').glob('*.' + name):
                    path.write_bytes(path.read_bytes() + b'tampered')
        self.fetcher.directory = output
        return release

    def service(self, root, **extra):
        service = AutomaticUpdates(self.registry, helper=self.helper, clock=lambda: self.now[0],
                                   client_factory=lambda: self.make_client('update-cache'),
                                   bootstrap_root=root, bootstrap_probe=lambda tree: self.probe_ok, **extra)
        service.preferences(enabled=True)
        return service

    def pointer(self):
        return Path(json.loads((self.folder / 'automatic-runtime.json').read_text())['root'])

    def siblings(self):
        return sorted(p.name for p in self.base.iterdir() if p.name.startswith('Bootstrap-Installer'))


@pytest.fixture
def feed(installed, repo, monkeypatch):
    return Feed(installed, repo, monkeypatch)


def attempt(service):
    """Only the bootstrap step (a full check would also stage the Hub update and then wait)."""
    record = {}
    return service._adopt_bootstrap(service.source(), record), record.get('bootstrap_error')


def state(feed):
    return json.loads(bootstrap_update.state_path(feed.registry).read_text())


def test_newer_signed_bootstrap_is_adopted_beside_the_running_one(feed):
    old = feed.current()  # Built before self-update existed: no sequence.
    feed.publish(1)
    service = feed.service(old)
    service.check()
    new = feed.pointer()
    assert new != old and new.parent == old.parent and new.name.startswith('Bootstrap-Installer-s1-')
    assert old.is_dir() and (old / 'bootstrap.json').is_file()  # Never touched.
    assert bootstrap_update.bundle_sequence(new) == 1 and (new / bootstrap_update.MARKER).is_file()
    assert state(feed)['state'] == 'trial' and state(feed)['previous_root'] == str(old)
    assert service.status()['bootstrap'] == {'sequence': 1, 'state': 'trial', 'error': None}
    assert feed.helper.status() is None  # Components are checked by the new bootstrap.
    assert service.state_path.exists() and json.loads(service.state_path.read_text())['last_attempt'] == 0
    # The old watcher sees the switch and hands over; the new one proves it started.
    assert watch(service) == 'handover'
    newer = feed.service(new)
    newer.check(now=True)
    assert state(feed)['state'] == 'confirmed'
    assert feed.helper.status()['phase'] == 'staged'  # Hub update staged by the new bootstrap.
    assert old.is_dir()  # The original install folder is not ours to delete.


def test_split_release_downloads_only_the_code_when_the_runtime_is_already_installed(feed):
    # Installed from that runtime's combined bundle (get-orion.ps1, or adopted by an older updater): the PC has the
    # runtime files, not the layer ZIP. It rebuilds the ZIP from them, so only the ~1 MB code package is downloaded.
    feed.publish(1, split=True)
    current = feed.current(runtime=feed.reference)
    feed.fetched.clear()
    assert attempt(feed.service(current)) == (True, None)
    new = feed.pointer()
    assert feed.downloads() == [BOOTSTRAP_CODE_TARGET]
    assert (new / 'runtime/python/python.exe').read_bytes() == b'x' and (new / 'orion-runtime.json').is_file()
    assert bootstrap_update.bundle_sequence(new) == 1
    # The next code-only release: the layer is in the download cache now.
    feed.publish(2, split=True)
    feed.fetched.clear()
    bootstrap_update.confirm(feed.registry, new)
    assert attempt(feed.service(new)) == (True, None)
    assert feed.downloads() == [BOOTSTRAP_CODE_TARGET] and bootstrap_update.bundle_sequence(feed.pointer()) == 2


def test_split_release_with_another_runtime_downloads_its_layer_once(feed):
    current = feed.current(runtime={'target': 'orion-bootstrap-runtime-0000000000000000.zip', 'sha256': '0' * 64,
                                    'length': 1})
    feed.publish(1, split=True, runtime=b'new python')
    feed.fetched.clear()
    assert attempt(feed.service(current)) == (True, None)
    assert sorted(feed.downloads()) == sorted([BOOTSTRAP_CODE_TARGET, feed.reference['target']])
    assert (feed.pointer() / 'runtime/python/python.exe').read_bytes() == b'new python'


def test_rebuilt_layer_counts_only_when_it_is_the_signed_one(feed):
    feed.publish(1, split=True)
    current = feed.current(runtime=feed.reference)
    (current / 'runtime/python/python.exe').write_bytes(b'changed on this PC')  # Same record, other bytes.
    feed.fetched.clear()
    assert attempt(feed.service(current)) == (True, None)
    assert feed.reference['target'] in feed.downloads()
    assert (feed.pointer() / 'runtime/python/python.exe').read_bytes() == b'x'


def test_updater_that_predates_split_bootstraps_adopts_the_combined_bundle(feed):
    # Installed 0.2.13 reads only the combined name; the bootstrap it adopts reads the code package of the same
    # sequence from the same download cache (the two names are immutable on their own, no rollback alarm).
    release = feed.publish(1, split=True)
    old = feed.make_client('update-cache')
    _, found = old.check([BOOTSTRAP_TARGET])
    tree = old.prepare(found[BOOTSTRAP_TARGET], feed.base / 'Bootstrap-Installer-s1-old')
    bootstrap_update.validate_bundle(tree, release, installed_root=feed.folder / 'trusted-root.json',
                                     current_root=tree, probe=lambda tree: True)
    assert (tree / 'runtime/python/python.exe').is_file() and not (tree / 'orion-runtime.json').exists()
    assert json.loads((tree / 'bootstrap.json').read_text())['runtime'] == feed.reference
    current = feed.current()
    feed.fetched.clear()
    assert attempt(feed.service(current)) == (True, None)
    assert BOOTSTRAP_CODE_TARGET in feed.downloads() and BOOTSTRAP_TARGET not in feed.downloads()


def test_managed_previous_bootstrap_is_pruned_only_after_a_successful_check(feed):
    first = feed.current(1, 'Bootstrap-Installer-s1-aaaa', managed=True)
    feed.publish(2)
    service = feed.service(first)
    service.check()
    new = feed.pointer()
    assert first.is_dir()  # Kept through the trial.
    feed.service(new).check(now=True)
    assert not first.exists() and new.is_dir()
    assert state(feed)['state'] == 'confirmed' and state(feed)['discard'] == []


def test_a_previous_bootstrap_still_running_is_kept_until_it_ends(feed, monkeypatch):
    # Real PC, 2026-10-20: the new bootstrap's first check deleted the previous bundle while the previous watcher
    # still ran from it; its handover then failed to import and no watcher started. The locked interpreter also
    # left the folder half deleted, without its marker.
    first = feed.current(1, 'Bootstrap-Installer-s1-aaaaaaaa', managed=True)
    feed.publish(2)
    feed.service(first).check()
    new = feed.pointer()
    running = {first}
    monkeypatch.setattr(bootstrap_update, '_in_use', lambda path: Path(path) in running)
    feed.service(new).check(now=True)
    assert first.is_dir() and (first / 'bootstrap.json').exists()  # Untouched while in use.
    assert state(feed)['state'] == 'confirmed' and state(feed)['discard'] == [str(first)]
    (first / bootstrap_update.MARKER).unlink()  # As a half deletion left it: no marker any more.
    running.clear()
    bootstrap_update.prune(feed.registry, new)
    assert not first.exists() and state(feed)['discard'] == []


def test_in_use_sees_a_process_running_from_the_folder():
    assert bootstrap_update._in_use(Path(sys.executable).parent)
    assert not bootstrap_update._in_use(Path(sys.executable).parent / 'no-such-folder')


@pytest.mark.parametrize('installed_sequence,published', [(2, 2), (5, 3), (4, 4)])
def test_same_or_older_sequence_is_not_adopted(feed, installed_sequence, published):
    current = feed.current(installed_sequence)
    feed.publish(published)
    feed.service(current).check()
    assert feed.pointer() == current and feed.siblings() == ['Bootstrap-Installer']


def test_tampered_bundle_is_rejected_and_leaves_nothing_behind(feed):
    current = feed.current()
    feed.publish(1, tamper=True)
    service = feed.service(current)
    service.check()
    assert feed.pointer() == current and feed.siblings() == ['Bootstrap-Installer']
    assert json.loads(service.state_path.read_text())['bootstrap_error']
    # The component check still ran after the failed bootstrap update.
    assert feed.helper.status()['phase'] == 'staged'


def test_update_is_refused_while_a_transaction_is_staged(feed):
    current = feed.current()
    feed.publish(1)
    service = feed.service(current)
    feed.helper.prepare(feed.client.fresh(), [feed.hub_name])
    assert feed.helper.status()['phase'] == 'staged'
    assert bootstrap_update.adopt(service, service.source()) is None
    service.check(now=True)
    assert feed.pointer() == current and feed.siblings() == ['Bootstrap-Installer']
    assert not bootstrap_update.state_path(feed.registry).exists()


def test_update_is_refused_while_recovery_blocks_admission(feed):
    current = feed.current()
    feed.publish(1)
    service = feed.service(current)
    active = feed.registry.path.parent / 'updates/active.json'
    active.parent.mkdir(exist_ok=True)
    active.write_text('garbage')  # An unreadable pointer keeps admission closed.
    assert bootstrap_update.adopt(service, None) is None
    assert feed.pointer() == current


def test_bundle_with_a_different_trusted_root_is_refused(feed):
    current = feed.current()
    feed.publish(1, root_bytes=b'{"another": "root"}')
    service = feed.service(current)
    service.check()
    assert feed.pointer() == current and feed.siblings() == ['Bootstrap-Installer']
    assert json.loads(service.state_path.read_text())['bootstrap_error'] == 'RootMismatch'


def test_incomplete_bundle_and_bundle_that_does_not_start_are_refused(feed):
    current = feed.current()
    feed.publish(1, drop='scripts/release/update_automatic.py')
    service = feed.service(current)
    assert attempt(service) == (False, 'BundleIncomplete')
    feed.publish(2)
    feed.probe_ok = False
    assert attempt(service) == (False, 'BundleDoesNotStart')
    assert feed.pointer() == current and feed.siblings() == ['Bootstrap-Installer']


def test_new_bootstrap_that_vanishes_falls_back_to_the_previous_one(feed):
    current = feed.current()
    feed.publish(1)
    service = feed.service(current)
    assert attempt(service) == (True, None)
    new = feed.pointer()
    shutil.rmtree(new / 'runtime')
    command = runtime_command(feed.registry)
    assert str(current / 'runtime/python/python.exe') in command[0]
    assert feed.pointer() == current
    assert state(feed)['state'] == 'rolled_back' and state(feed)['failed_sequence'] == 1
    # The failed sequence is not retried; a newer one is.
    assert attempt(service) == (False, None) and feed.pointer() == current
    feed.publish(2)
    assert attempt(service)[0] is True
    assert bootstrap_update.bundle_sequence(feed.pointer()) == 2


def test_old_watcher_rolls_back_when_the_new_watcher_never_starts(feed):
    current = feed.current()
    feed.publish(1)
    feed.service(current).check()
    spawned, clock = [], iter(range(0, 1000, 20))
    ok = bootstrap_update.supervise_handover(feed.registry, lambda: spawned.append(1), wait=60,
                                             sleep=lambda s: None, clock=lambda: next(clock))
    assert ok is False and spawned and feed.pointer() == current
    assert state(feed)['state'] == 'rolled_back' and state(feed)['error'] == 'NewBootstrapDidNotStart'


def test_old_watcher_stays_out_of_the_way_once_the_new_watcher_started(feed):
    current = feed.current()
    feed.publish(1)
    feed.service(current).check()
    new = feed.pointer()

    def start_new():
        bootstrap_update.note_started(feed.registry, new)
    assert bootstrap_update.supervise_handover(feed.registry, start_new, wait=60, sleep=lambda s: None) is True
    assert feed.pointer() == new and state(feed)['state'] == 'trial' and state(feed)['started_at']


def test_a_confirmed_bootstrap_is_not_proof_that_its_watcher_runs(feed):
    # Real PC, 2026-10-20: a manual check from the new bootstrap confirmed it, the old watcher saw "confirmed",
    # left, and no watcher ran: "Şimdi kur" did nothing until Orion was opened again.
    current = feed.current()
    feed.publish(1)
    feed.service(current).check()
    new = feed.pointer()
    bootstrap_update.confirm(feed.registry, new)
    clock = iter(range(0, 1000, 20))
    assert bootstrap_update.supervise_handover(feed.registry, lambda: None, wait=60, sleep=lambda s: None,
                                               clock=lambda: next(clock), alive=lambda: False) is False
    assert state(feed)['state'] == 'confirmed' and feed.pointer() == new  # Not rolled back: started again.
    running = iter([False, False, True])
    assert bootstrap_update.supervise_handover(feed.registry, lambda: None, wait=60, sleep=lambda s: None,
                                               alive=lambda: next(running)) is True


def test_a_failing_spawn_is_logged(feed):
    current = feed.current()
    feed.publish(1)
    feed.service(current).check()

    def spawn():
        raise ValueError('spawn broke')
    clock = iter(range(0, 1000, 20))
    bootstrap_update.supervise_handover(feed.registry, spawn, wait=60, sleep=lambda s: None,
                                        clock=lambda: next(clock), alive=lambda: False)
    log = (Path(feed.registry.path).parent / 'updates/watcher.log').read_text(encoding='utf-8')
    assert 'handover.spawn' in log


def test_unadopted_trial_is_not_stacked_with_a_second_adoption(feed):
    current = feed.current()
    feed.publish(1)
    service = feed.service(current)
    service.check()
    first = feed.pointer()
    feed.publish(2)
    service.check(now=True)
    assert feed.pointer() == first


def test_check_process_failure_is_recorded_as_a_safe_class(feed):
    service = feed.service(None)
    record_check_failure(feed.registry, 2)
    assert service.status()['error'] == 'BootstrapIncompatible'
    record_check_failure(feed.registry, 1)
    assert service.status()['error'] == 'CheckProcessFailed'
    # A later successful check clears it.
    service.check(now=True)
    assert service.status()['error'] is None


def test_default_probe_runs_a_real_interpreter_against_the_bundle(tmp_path):
    root = tmp_path / 'bundle'
    (root / 'runtime/python').mkdir(parents=True)
    shutil.copyfile(sys.executable, root / 'runtime/python/python.exe')
    assert bootstrap_update.default_probe(root) is False  # Interpreter alone cannot import the bundle.


def _published(sequence, digest, name=BOOTSTRAP_TARGET):
    return {'signed': {'targets': {name: {'custom': {'sequence': sequence}, 'hashes': {'sha256': digest}}}}}


def test_publish_gate_refuses_changed_bytes_under_a_published_sequence():
    import publish_release as pr
    custom = {'sequence': 3}
    pr.bootstrap_gate(None, custom, 'a')  # First publication.
    pr.bootstrap_gate(_published(3, 'a'), custom, 'a')  # Unchanged bundle republished.
    pr.bootstrap_gate(_published(2, 'a'), custom, 'b')  # Bumped.
    with pytest.raises(ValueError, match='bump'):
        pr.bootstrap_gate(_published(3, 'a'), custom, 'b')
    with pytest.raises(ValueError, match='below'):
        pr.bootstrap_gate(_published(4, 'a'), custom, 'a')
    # The code package is gated on its own name: the same sequence as the combined bundle, other bytes.
    pr.bootstrap_gate(_published(3, 'a'), custom, 'c', BOOTSTRAP_CODE_TARGET)
    with pytest.raises(ValueError, match='bump'):
        pr.bootstrap_gate(_published(3, 'c', BOOTSTRAP_CODE_TARGET), custom, 'd', BOOTSTRAP_CODE_TARGET)


def test_reused_bootstrap_brings_its_code_package_and_layer(tmp_path):
    import publish_release as pr
    built = tmp_path / 'built'
    built.mkdir()
    layer = 'orion-bootstrap-runtime-0123456789abcdef.zip'
    for name in (BOOTSTRAP_TARGET, BOOTSTRAP_CODE_TARGET, layer):
        (built / name).write_bytes(name.encode())
        description = {'path': name, **({'runtime': {'target': layer}} if name == BOOTSTRAP_CODE_TARGET else {})}
        (built / name).with_suffix('.target.json').write_text(json.dumps(description))
    parts = pr.bootstrap_parts(built / BOOTSTRAP_TARGET)
    assert [p.name for p in parts] == [BOOTSTRAP_TARGET, BOOTSTRAP_CODE_TARGET, layer]
    (tmp_path / 'staged').mkdir()
    pr.copy_parts(parts, tmp_path / 'staged')
    assert len(list((tmp_path / 'staged').iterdir())) == 6
    (built / BOOTSTRAP_CODE_TARGET).with_suffix('.target.json').unlink()  # Built before split bootstraps.
    assert pr.bootstrap_parts(built / BOOTSTRAP_TARGET) == [built / BOOTSTRAP_TARGET]


def test_acceptance_binds_the_bootstrap_without_requiring_it_in_the_package_run(tmp_path):
    import publish_release as pr
    staging = tmp_path / 'artifacts'
    staging.mkdir()
    # The combined bootstrap bundle and its code package are one release (one package id), bound by name.
    for stem, component, package in (('hub', 'hub', 'orion-hub-1.0.0-windows-x86_64-cpu'),
                                     ('bootstrap', 'bootstrap', 'orion-bootstrap-0.1.0-windows-x86_64-cpu'),
                                     ('bootstrap-code', 'bootstrap', 'orion-bootstrap-0.1.0-windows-x86_64-cpu')):
        (staging / f'{stem}.zip').write_bytes(stem.encode())
        (staging / f'{stem}.target.json').write_text(json.dumps({
            'path': f'{stem}.zip', 'hashes': {'sha256': sha256(staging / f'{stem}.zip')}, 'custom': {'component': component, 'package_id': package}}))
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({'success': True, 'artifacts': [{'package_id': 'orion-hub-1.0.0-windows-x86_64-cpu'}]}))
    pr.write_acceptance(staging, result, tmp_path / 'acceptance.json')
    from services.shared.release_acceptance import verify_acceptance
    verify_acceptance(staging, tmp_path / 'acceptance.json', 'preview')


def test_watcher_that_adopted_a_bootstrap_hands_over_without_a_router_round(feed, monkeypatch):
    # Real PC, 2026-10-18: the old process went on with the Router feed and staged a Router-only update.
    old = feed.current()
    feed.publish(1)
    service = feed.service(old)
    scopes, check = [], AutomaticUpdates.check
    monkeypatch.setattr(feed.helper.host, 'stopped', lambda record: contextlib.nullcontext(False))  # Orion running.
    monkeypatch.setattr(AutomaticUpdates, 'check', lambda self, **kw: (scopes.append(self.scope), check(self, **kw))[1])
    assert watch(service) == 'handover'
    assert scopes == ['hub'] and service.replaced() and feed.helper.status() is None


def test_check_command_stops_after_handing_over(feed, monkeypatch):
    import update_automatic
    scopes = []

    class Fake:
        def __init__(self, registry, *, helper=None, scope='hub', **kw):
            self.scope, self.base, self.helper = scope, feed.helper.base, helper
            self.state_path = feed.helper.base / 'automatic.json'

        def check(self, **kw):
            scopes.append(self.scope)

        def replaced(self):
            return True
    monkeypatch.setattr(update_automatic, 'AutomaticUpdates', Fake)
    monkeypatch.setattr(sys, 'argv', ['update_automatic.py', '--registry', str(feed.registry.path), 'check', '--now'])
    assert update_automatic.run() == 0 and scopes == ['hub']
    assert 'check.handed-over' in (feed.helper.base / 'watcher.log').read_text(encoding='utf-8')


def test_a_waiting_update_is_checked_once_per_interval_not_every_poll(feed):
    # A downloaded update waiting to be applied no longer blocks checks (a newer release replaces it), but the
    # watcher's 5 s polls still do nothing between two scheduled checks, and nothing is logged for them.
    service = feed.service(feed.current())
    feed.helper.prepare(feed.client.fresh(), [feed.hub_name])
    waiting = feed.helper.status()['operation_id']
    service.state_path.write_text(json.dumps({'schema': 1, 'last_attempt': 0}))
    for _ in range(3):
        service.check()
    service.check(now=True)  # An explicit refresh checks again.
    log = (feed.helper.base / 'watcher.log').read_text(encoding='utf-8')
    assert log.count('check.start scope=hub') == 2 and 'outcome=pending' not in log
    assert json.loads(service.state_path.read_text())['outcome'] == 'pending'   # Still the newest: kept.
    assert feed.helper.status()['operation_id'] == waiting
