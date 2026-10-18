"""Split packages: a code target plus a content-addressed runtime layer, real TUF signatures.

The PC downloads a layer only when it does not have that exact one; both land in ONE tree."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import zipfile

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root

import publish_metadata as publisher
import publish_release as P
from services.shared.release_packages import PackageError, ReleaseClient, sha256
from test_release_packages import LocalFetcher, release


def layer(directory, files):
    archive = directory / 'layer.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        for name, data in files.items():
            stream.writestr(name, data)
    digest = sha256(archive)
    name = f'orion-hub-runtime-{digest[:16]}.zip'
    archive.rename(directory / name)
    description = {'path': name, 'length': (directory / name).stat().st_size, 'hashes': {'sha256': digest},
                   'custom': {'schema': 1, 'layer': 'runtime', 'for': 'hub', 'inputs': '0' * 64}}
    (directory / (name[:-4] + '.target.json')).write_text(json.dumps(description))
    return {'target': name, 'sha256': digest, 'length': description['length']}


def code(directory, sequence, runtime, *, embedded=None):
    manifest = {**release('hub'), 'sequence': sequence, 'version': f'0.9.{sequence}',
                'package_id': f'orion-hub-0.9.{sequence}-windows-x86_64-cpu'}
    archive = directory / (manifest['package_id'] + '.zip')
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('orion-release.json', json.dumps(manifest))
        stream.writestr('orion-runtime.json', json.dumps(embedded or runtime))
        stream.writestr(manifest['entrypoint'], f'code {sequence}')
    description = {'path': archive.name, 'length': archive.stat().st_size, 'hashes': {'sha256': sha256(archive)},
                   'custom': manifest, 'runtime': runtime}
    (directory / 'hub.target.json').write_text(json.dumps(description))
    return archive.name


@pytest.fixture
def feed(request):
    base = Path.home() / '.orion-lifecycle-tests'  # private, never inside a real Orion install
    base.mkdir(parents=True, exist_ok=True)
    owned = tempfile.TemporaryDirectory(prefix='stage05-split-', dir=base)
    request.addfinalizer(owned.cleanup)
    tmp = Path(owned.name)
    signers = {role: [CryptoSigner.generate_ed25519()] for role in ('root', 'targets', 'snapshot', 'timestamp')}
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
    for role, keys in signers.items():
        for signer in keys:
            root.signed.add_key(signer.public_key, role)
    root.sign(signers['root'][0])
    (tmp / 'root.json').write_bytes(root.to_bytes())
    fetcher = LocalFetcher(tmp)
    fetched = []
    original = fetcher._fetch
    fetcher._fetch = lambda url: (fetched.append(url.rsplit('/', 1)[1]), original(url))[1]

    def publish(version, build):
        artifacts = tmp / f'artifacts-{version}'
        artifacts.mkdir()
        name = build(artifacts)
        fetcher.directory = publisher.publish(artifacts, tmp / f'repository-{version}', tmp / 'root.json', signers,
                                              version=version)
        client = ReleaseClient(trusted_root=tmp / 'root.json', cache=tmp / 'cache',
                               metadata_url='https://release.example/metadata/',
                               targets_url='https://release.example/targets/', fetcher=fetcher)
        _, targets = client.check([name])
        return client, targets[name]
    return tmp, publish, fetched


def test_layer_is_downloaded_once_and_merged_into_one_tree(feed):
    tmp, publish, fetched = feed
    runtime = {'runtime/python/python.exe': b'interpreter', 'runtime/python/site-packages/lib.py': b'v1'}

    def first(directory):
        return code(directory, 31, layer(directory, runtime))
    client, target = publish(1, first)
    assert client.download_length(target) == target.length + target.unrecognized_fields['runtime']['length']
    tree = client.prepare(target, tmp / 'Hub/releases/one')
    assert (tree / 'runtime/python/site-packages/lib.py').read_bytes() == b'v1'
    assert (tree / 'services/hub/run_local.py').read_text() == 'code 31'
    assert sum('-runtime-' in name for name in fetched) == 1

    # Code-only change: same layer bytes, so the PC downloads only the small code ZIP.
    fetched.clear()
    client, target = publish(2, lambda d: code(d, 32, layer(d, runtime)))
    assert client.download_length(target) == target.length
    tree = client.prepare(target, tmp / 'Hub/releases/two')
    assert (tree / 'services/hub/run_local.py').read_text() == 'code 32' and (tree / 'runtime/python/python.exe').exists()
    assert not any('-runtime-' in name for name in fetched)

    # A new Python library: a new layer is downloaded, the old one leaves the download cache.
    fetched.clear()
    newer = {**runtime, 'runtime/python/site-packages/added.py': b'new library'}
    client, target = publish(3, lambda d: code(d, 33, layer(d, newer)))
    tree = client.prepare(target, tmp / 'Hub/releases/three')
    assert (tree / 'runtime/python/site-packages/added.py').exists()
    assert sum('-runtime-' in name for name in fetched) == 1
    assert [p.name for p in (tmp / 'cache/targets').glob('*-runtime-*')] == [target.unrecognized_fields['runtime']['target']]


def test_code_and_layer_must_agree_and_never_overlap(feed):
    tmp, publish, _ = feed

    def overlapping(directory):
        return code(directory, 31, layer(directory, {'runtime/python/python.exe': b'x', 'orion-release.json': b'{}'}))
    client, target = publish(1, overlapping)
    with pytest.raises(PackageError, match='Duplicate'):
        client.prepare(target, tmp / 'release')
    assert not (tmp / 'release').exists()

    def mismatched(directory):
        runtime = layer(directory, {'runtime/python/python.exe': b'x'})
        return code(directory, 32, runtime, embedded={**runtime, 'length': runtime['length'] + 1})
    with pytest.raises(ValueError, match='runtime reference'):
        publish(2, mismatched)

    def missing(directory):
        runtime = layer(directory, {'runtime/python/python.exe': b'x'})
        for path in directory.glob('*-runtime-*'):
            path.unlink()
        return code(directory, 32, runtime)
    with pytest.raises(ValueError, match='not among the artifacts'):
        publish(3, missing)


def test_publisher_stages_split_names_carries_combined_targets_and_gates_the_bootstrap(tmp_path):
    candidates = tmp_path / 'candidates'
    candidates.mkdir()
    runtime = layer(candidates, {'runtime/python/python.exe': b'x'})
    layer(candidates, {'runtime/python/python.exe': b'stale build'})       # unreferenced: never staged
    code(candidates, 40, runtime)
    names = P.stage_stable_names(candidates, tmp_path / 'stage', ('hub', 'client'))
    assert names == {'hub': 'orion-hub-code-windows-x86_64-cpu.zip'}
    assert sorted(p.name for p in (tmp_path / 'stage').glob('*-runtime-*.zip')) == [runtime['target']]
    combined = {'custom': {'component': 'hub', 'sequence': 39}, 'length': 1, 'hashes': {'sha256': '0' * 64}}
    online = {'signed': {'targets': {'orion-hub-windows-x86_64-cpu.zip': combined}}}
    assert P.carried_targets(online, names, ('hub', 'client')) == {'orion-hub-windows-x86_64-cpu.zip': combined}
    P.package_gate(online, tmp_path / 'stage', names)
    combined['custom']['sequence'] = 40
    with pytest.raises(ValueError, match='combined'):
        P.package_gate(online, tmp_path / 'stage', names)
    bundle = tmp_path / 'bootstrap.zip'
    with zipfile.ZipFile(bundle, 'w') as stream:
        stream.writestr('release-source.json', json.dumps(P.build_source(
            'hub', {'hub': 'orion-hub-windows-x86_64-cpu.zip'}, repo=P.DEFAULT_REPO, tag='feed')))
    with pytest.raises(ValueError, match='bump'):
        P.bootstrap_source_gate(bundle, names)
