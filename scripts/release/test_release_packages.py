"""Real TUF signatures and adversarial archives; no installed product is used."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.exceptions import DownloadHTTPError, DownloadError, RepositoryError
from tuf.api.metadata import Metadata, Root
from tuf.ngclient.fetcher import FetcherInterface

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.release_packages import (PackageError, ReleaseClient, compatible, extract_zip, sha256,
    validate_release, archive_name, record_prepared_package, verify_registered_package)
from services.shared.runtime_layout import data_root, state_root

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).with_name('publish_metadata.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def release(kind='hub'):
    path = {'hub': ROOT / 'scripts/release/hub.json',
            'router': ROOT.parent / 'orion-router/release/package.json',
            'client': ROOT.parent / 'orion-ai-assistant-flutter/release/package.json'}[kind]
    return json.loads(path.read_text())


class LocalFetcher(FetcherInterface):
    def __init__(self, directory):
        self.directory = directory
        self.offline = False

    def _fetch(self, url):
        if self.offline:
            raise DownloadError('Offline fixture')
        path = self.directory / url.split('https://release.example/', 1)[1]
        if not path.is_file():
            raise DownloadHTTPError('Missing fixture', 404)
        yield path.read_bytes()


@pytest.fixture
def repo(tmp_path, request):
    if os.name == 'nt':
        # Windows TEMP may grant group writes. Executable package staging must
        # use a private Orion area, just like the real installer.
        base = Path(os.environ['LOCALAPPDATA']) / 'Orion/lifecycle-tests'
        base.mkdir(parents=True, exist_ok=True)
        owned = tempfile.TemporaryDirectory(prefix='stage05-tuf-', dir=base)
        request.addfinalizer(owned.cleanup)
        tmp_path = Path(owned.name)
    signers = {role: [CryptoSigner.generate_ed25519()] for role in ('root', 'targets', 'snapshot', 'timestamp')}
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
    for role, keys in signers.items():
        for signer in keys:
            root.signed.add_key(signer.public_key, role)
    root.sign(signers['root'][0])
    bootstrap = tmp_path / 'trusted-root.json'
    bootstrap.write_bytes(root.to_bytes())
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    manifest = release()
    name = manifest['package_id'] + '.zip'
    package = artifacts / name
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('orion-release.json', json.dumps(manifest))
        archive.writestr(manifest['entrypoint'], 'fixture entrypoint, never executed')
    description = {'path': name, 'length': package.stat().st_size,
                   'hashes': {'sha256': sha256(package)}, 'custom': manifest}
    (artifacts / 'hub.target.json').write_text(json.dumps(description))
    output = publisher.publish(artifacts, tmp_path / 'repository', bootstrap, signers, version=1)
    fetcher = LocalFetcher(output)

    def client(cache='cache'):
        return ReleaseClient(trusted_root=bootstrap, cache=tmp_path / cache,
            metadata_url='https://release.example/metadata/', targets_url='https://release.example/targets/', fetcher=fetcher)
    return tmp_path, output, name, signers, bootstrap, artifacts, fetcher, client


def test_signed_package_preparation_never_changes_data_or_models(repo):
    tmp, _, name, _, _, _, _, client = repo
    persistent = tmp / 'data/files/security'
    persistent.mkdir(parents=True)
    for filename in ('router-access-seed', 'tls.key', 'router-access-r-u.json', 'router-access-r-u.manual'):
        (persistent / filename).write_bytes(b'preserved-' + filename.encode())
    model = tmp / 'Models/weight.gguf'
    model.parent.mkdir()
    model.write_bytes(b'existing model')
    before = {p: sha256(p) for p in tmp.rglob('*') if p.is_file()}
    checker = client()
    status, targets = checker.check([name])
    assert status['status'] == 'checked'
    tree = checker.prepare(targets[name], tmp / 'Hub/releases/immutable')
    assert (tree / 'orion-release.json').exists()
    assert all(sha256(path) == digest for path, digest in before.items())
    with pytest.raises(PackageError, match='already exists'):
        checker.prepare(targets[name], tree)


def test_hash_failure_never_publishes_tree(repo):
    tmp, output, name, _, _, _, _, client = repo
    checker = client()
    _, targets = checker.check([name])
    next((output / 'targets').iterdir()).write_bytes(b'corrupt')
    with pytest.raises((DownloadError, RepositoryError)):
        checker.prepare(targets[name], tmp / 'release')
    assert not (tmp / 'release').exists()


def test_cache_replacement_after_verification_cannot_change_extracted_bytes(repo, monkeypatch):
    import services.shared.release_packages as packages
    tmp, _, name, _, _, _, _, client = repo
    checker = client()
    _, targets = checker.check([name])
    target = targets[name]
    cached = Path(checker.updater.download_target(target))
    original_extract = packages.extract_zip
    def replace_cache(verified_stream, destination):
        cached.write_bytes(b'concurrent cache replacement')
        assert hasattr(verified_stream, 'read')
        return original_extract(verified_stream, destination)
    monkeypatch.setattr(packages, 'extract_zip', replace_cache)
    tree = checker.prepare(target, tmp / 'private-release')
    assert json.loads((tree / 'orion-release.json').read_text()) == target.custom


def test_bad_signature_and_expired_metadata_are_unavailable(repo):
    _, output, name, signers, _, _, _, client = repo
    timestamp = output / 'metadata/timestamp.json'
    original = timestamp.read_bytes()
    broken = Metadata.from_bytes(original)
    broken.sign(CryptoSigner.generate_ed25519())
    timestamp.write_bytes(broken.to_bytes())
    assert client('bad-signature').check([name])[0]['status'] == 'check-unavailable'
    expired = Metadata.from_bytes(original)
    expired.signed.expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired.sign(signers['timestamp'][0])
    timestamp.write_bytes(expired.to_bytes())
    assert client('expired').check([name])[0]['status'] == 'check-unavailable'


def test_offline_preserves_last_success_but_cannot_prepare(repo):
    tmp, _, name, _, _, _, fetcher, client = repo
    checker = client()
    successful, targets = checker.check([name])
    fetcher.offline = True
    status, unavailable = client().check([name])
    assert unavailable is None and status['status'] == 'check-unavailable'
    assert status['last_success'] == successful['last_success']
    fetcher.offline = False
    checker = client()
    with pytest.raises(PackageError, match='fresh'):
        checker.prepare(targets[name], tmp / 'release')


def test_metadata_rollback_is_rejected(repo):
    tmp, output, name, signers, bootstrap, artifacts, fetcher, client = repo
    newer = publisher.publish(artifacts, tmp / 'newer', bootstrap, signers, version=2)
    fetcher.directory = newer
    assert client().check([name])[0]['status'] == 'checked'
    fetcher.directory = output
    assert client().check([name])[0]['status'] == 'check-unavailable'


def test_newer_signed_metadata_cannot_substitute_same_package(repo):
    tmp, _, name, signers, bootstrap, artifacts, fetcher, client = repo
    assert client().check([name])[0]['status'] == 'checked'
    package = artifacts / name
    with zipfile.ZipFile(package, 'a') as archive:
        archive.writestr('unexpected-change.txt', b'different final bytes')
    description = artifacts / 'hub.target.json'
    value = json.loads(description.read_text())
    value.update(length=package.stat().st_size, hashes={'sha256': sha256(package)})
    description.write_text(json.dumps(value))
    fetcher.directory = publisher.publish(artifacts, tmp / 'substituted', bootstrap, signers, version=2)
    status, targets = client().check([name])
    assert targets is None and status['status'] == 'check-unavailable'
    assert status['reason'] == 'PackageError'


def test_product_downgrade_in_fresh_signed_metadata_rejected(repo):
    tmp, output, name, signers, bootstrap, artifacts, fetcher, client = repo
    description = artifacts / 'hub.target.json'
    original = json.loads(description.read_text())
    newer = deepcopy(original)
    newer['custom']['sequence'] = original['custom']['sequence'] + 1
    newer['custom']['version'] = '9.0.1'
    newer['custom']['package_id'] = 'orion-hub-9.0.1-windows-x86_64-cpu'
    newer['path'] = newer['custom']['package_id'] + '.zip'
    newer_artifacts = tmp / 'new-artifacts'
    newer_artifacts.mkdir()
    package = newer_artifacts / newer['path']
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('orion-release.json', json.dumps(newer['custom']))
        archive.writestr(newer['custom']['entrypoint'], b'new fixture')
    newer.update(length=package.stat().st_size, hashes={'sha256': sha256(package)})
    (newer_artifacts / 'hub.target.json').write_text(json.dumps(newer))
    fetcher.directory = publisher.publish(newer_artifacts, tmp / 'newer-product', bootstrap, signers, version=2)
    assert client().check([newer['path']])[0]['status'] == 'checked'
    # TUF metadata version grows, so this specifically tests product rollback.
    fetcher.directory = publisher.publish(artifacts, tmp / 'fresh-old-product', bootstrap, signers, version=3)
    status, targets = client().check([name])
    assert targets is None and status['reason'] == 'PackageError'


def test_root_rotation_requires_old_and_new_thresholds(repo):
    _, output, name, signers, bootstrap, _, _, client = repo
    assert client().check([name])[0]['status'] == 'checked'
    old = Metadata.from_file(str(bootstrap))
    new_signer = CryptoSigner.generate_ed25519()
    rotated = deepcopy(old)
    rotated.signed.version = 2
    rotated.signed.revoke_key(signers['root'][0].public_key.keyid, 'root')
    rotated.signed.add_key(new_signer.public_key, 'root')
    rotated.sign(new_signer)
    path = output / 'metadata/2.root.json'
    path.write_bytes(rotated.to_bytes())
    assert client('bad-rotation').check([name])[0]['status'] == 'check-unavailable'
    rotated.sign(signers['root'][0], append=True)
    path.write_bytes(rotated.to_bytes())
    assert client('good-rotation').check([name])[0]['status'] == 'checked'


@pytest.mark.parametrize('name', ['../escaped', '/absolute', 'C:/escape', 'dir\\escape', 'a/../b',
    'a//b', 'file:stream', 'CON.txt', 'COM¹.txt', 'CONOUT$', 'a./file', 'trailing ', 'dir/./file'])
def test_archive_traversal_windows_aliases_rejected(tmp_path, name):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        info = zipfile.ZipInfo('placeholder')
        info.filename = name  # bypass Windows ZipInfo's writer normalization
        archive.writestr(info, b'bad')
    with pytest.raises(PackageError):
        extract_zip(path, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_nul_archive_path_rejected():
    with pytest.raises(PackageError):
        archive_name('\x00bad')


@pytest.mark.parametrize('members', [[('A', b'a'), ('a', b'b')], [('file', b'a'), ('file/sub', b'b')],
                                    [('A/x', b'a'), ('a/y', b'b')]])
def test_archive_collisions_rejected(tmp_path, members):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members:
            archive.writestr(name, data)
    with pytest.raises(PackageError):
        extract_zip(path, tmp_path / 'output')


def test_symlink_and_bomb_limits(tmp_path):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        info = zipfile.ZipInfo('link')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b'../outside')
    with pytest.raises(PackageError):
        extract_zip(path, tmp_path / 'output')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('large', b'x' * 100)
    with pytest.raises(PackageError, match='limit'):
        extract_zip(path, tmp_path / 'output', max_bytes=50)
    with pytest.raises(PackageError, match='limit'):
        extract_zip(path, tmp_path / 'output', max_files=0)


def test_independent_components_unchanged_peers_mobile_and_downgrade():
    hub, router, client = (release(k) for k in ('hub', 'router', 'client'))
    for changed in (hub, router, client):
        changed['sequence'] = 2
        compatible([hub, router, client], installed_sequences={k: 1 for k in ('hub', 'router', 'client')}, mobile_api=1)
        changed['sequence'] = 1
    compatible([router])
    for bad in (0, 2):
        with pytest.raises(PackageError):
            compatible([hub, client], mobile_api=bad)
    client['api'] = 2
    with pytest.raises(PackageError, match='Incompatible'):
        compatible([hub, client])
    with pytest.raises(PackageError, match='downgrade'):
        compatible([router], installed_sequences={'router': 2})
    router['capabilities'] = []
    with pytest.raises(PackageError, match='endpoints'):
        compatible([hub, router])


@pytest.mark.parametrize('field,value', [('arch', 'arm64'), ('profile', 'cuda'), ('platform', 'linux'),
    ('version', '1.0'), ('sequence', True), ('entrypoint', '../evil')])
def test_unsupported_builds_fail_closed(field, value):
    metadata = release()
    metadata[field] = value
    with pytest.raises(PackageError):
        validate_release(metadata)


def test_registry_mapping_keeps_package_immutable_and_source_unchanged(tmp_path, monkeypatch):
    code = tmp_path / 'Hub/releases/one'
    code.mkdir(parents=True)
    (code / 'orion-release.json').write_text(json.dumps(release()))
    monkeypatch.delenv('ORION_INSTALLATION_REGISTRY', raising=False)
    with pytest.raises(RuntimeError, match='explicit'):
        data_root(code)
    data, state = tmp_path / 'Hub/data', tmp_path / 'Hub/state'
    registry = tmp_path / 'install.json'
    registry.write_text(json.dumps({'schema_version': 1, 'components': [{'distribution': 'packaged',
        'code_path': str(code), 'data_path': str(data), 'state_path': str(state)}]}))
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(registry))
    assert data_root(code) == data and state_root(code) == state
    source = tmp_path / 'development'
    assert data_root(source) == source and state_root(source) == source / '.orion-lifecycle'


def test_changed_final_artifact_and_wrong_role_keys_cannot_publish(repo):
    tmp, _, _, signers, bootstrap, artifacts, _, _ = repo
    bad_signers = {**signers, 'targets': [CryptoSigner.generate_ed25519()]}
    with pytest.raises(RepositoryError):
        publisher.publish(artifacts, tmp / 'bad-key', bootstrap, bad_signers, version=2)
    next(artifacts.glob('*.zip')).write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        publisher.publish(artifacts, tmp / 'bad-artifact', bootstrap, signers, version=2)
    assert not (tmp / 'bad-key').exists() and not (tmp / 'bad-artifact').exists()


def test_production_signing_cli_loads_role_pem_with_two_of_three_offline_root(repo):
    tmp, _, _, signers, _, artifacts, _, _ = repo
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
    offline = [CryptoSigner.generate_ed25519() for _ in range(3)]
    for signer in offline:
        root.signed.add_key(signer.public_key, 'root')
    root.signed.roles['root'].threshold = 2
    paths = {}
    for role in ('targets', 'snapshot', 'timestamp'):
        signer = signers[role][0]
        root.signed.add_key(signer.public_key, role)
        pem = tmp / (role + ' Türkçe fixture.pem')
        pem.write_bytes(signer.private_bytes)  # ephemeral test role keys only
        paths[role] = {signer.public_key.keyid: str(pem)}
    for signer in offline[:2]:
        root.sign(signer, append=True)
    trusted = tmp / 'production-policy-TEST-root.json'
    trusted.write_bytes(root.to_bytes())
    keys = tmp / 'TEST-role-paths.json'
    keys.write_text(json.dumps(paths))
    output = tmp / 'cli-signed'
    evidence = tmp / 'acceptance'
    evidence.mkdir()
    checks = {}
    for name in ('clean_windows_vm', 'upgrade_from_previous', 'failed_release_recovery', 'uninstall_preserves_data'):
        (evidence / (name + '.txt')).write_text('TEST evidence ' + name)
        checks[name] = {'passed': True, 'report': name + '.txt', 'sha256': sha256(evidence / (name + '.txt'))}
    packages = {}
    for path in artifacts.glob('*.target.json'):
        target = json.loads(path.read_text(encoding='utf-8'))
        packages[target['custom']['package_id']] = target['hashes']['sha256']
    acceptance = evidence / 'acceptance.json'
    acceptance.write_text(json.dumps({'schema': 1, 'channel': 'beta', 'test_only': False,
                                      'packages': packages, 'checks': checks}))
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('publish_metadata.py')),
        '--artifacts', str(artifacts), '--output', str(output), '--root', str(trusted),
        '--keys', str(keys), '--version', '2', '--channel', 'beta', '--acceptance', str(acceptance)],
        capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    timestamp = Metadata.from_file(str(output / 'metadata/timestamp.json'))
    root.verify_delegate('timestamp', timestamp)


def test_installed_package_provenance_detects_code_and_receipt_tampering(repo, monkeypatch):
    from services.shared.installation_registry import Registry, new_registry, component_record
    tmp, _, name, _, _, _, _, client = repo
    checker = client()
    _, targets = checker.check([name])
    tree = checker.prepare(targets[name], tmp / 'Hub/releases/one')
    data, state = tmp / 'Hub/data', tmp / 'Hub/state'
    data.mkdir()
    state.mkdir()
    provenance = record_prepared_package(tree, state, targets[name])
    registry = Registry(tmp / 'install.json')
    registry.write(new_registry(tmp, [component_record('hub', tree, distribution='packaged',
        data_path=data, state_path=state, ownership='owned', management='local', package=provenance)]))
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(registry.path))
    assert verify_registered_package(tree)['component'] == 'hub'
    from services.shared.process_lifecycle import Runtime
    with Runtime(tree, 'hub') as runtime:
        assert runtime.read()['release'] == release()['package_id']
    assert verify_registered_package(tree)['component'] == 'hub'
    entrypoint = tree / release()['entrypoint']
    original = entrypoint.read_bytes()
    entrypoint.write_bytes(b'tampered')
    with pytest.raises(PackageError, match='hash'):
        verify_registered_package(tree)
    entrypoint.write_bytes(original)
    (state / 'installed-package.json').write_text('{}')
    with pytest.raises(PackageError, match='inventory'):
        verify_registered_package(tree)


def test_quick_registered_check_skips_rehash_but_full_check_and_changes_still_hash(repo, monkeypatch):
    from services.shared import release_packages
    from services.shared.installation_registry import Registry, new_registry, component_record
    tmp, _, name, _, _, _, _, client = repo
    checker = client()
    _, targets = checker.check([name])
    tree = checker.prepare(targets[name], tmp / 'Hub/releases/quick')
    data, state = tmp / 'Hub/data', tmp / 'Hub/state'
    data.mkdir()
    state.mkdir()
    provenance = record_prepared_package(tree, state, targets[name])
    assert (state / release_packages.VERIFIED_CACHE).is_file()
    registry = Registry(tmp / 'install.json')
    record = new_registry(tmp, [component_record('hub', tree, distribution='packaged',
        data_path=data, state_path=state, ownership='owned', management='local', package=provenance)])
    registry.write(record)
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(registry.path))
    real = release_packages.sha256
    calls = []
    monkeypatch.setattr(release_packages, 'sha256', lambda path: calls.append(path) or real(path))
    assert verify_registered_package(tree)['component'] == 'hub'
    assert [p for p in calls if Path(p).is_relative_to(tree)] == []  # quick: no package file hashed
    release_packages.verify_package_record(registry.read()['components'][0])  # explicit full check
    assert any(Path(p).is_relative_to(tree) for p in calls)
    entrypoint = tree / release()['entrypoint']
    original = entrypoint.read_bytes()
    entrypoint.write_bytes(original + b' ')  # size change must be caught by the fallback hash
    with pytest.raises(PackageError, match='hash'):
        verify_registered_package(tree)
    (state / release_packages.VERIFIED_CACHE).unlink()
    entrypoint.write_bytes(original)
    assert verify_registered_package(tree)['component'] == 'hub'  # no cache: hashes, then re-caches
    assert (state / release_packages.VERIFIED_CACHE).is_file()
