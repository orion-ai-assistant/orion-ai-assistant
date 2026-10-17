"""Create TUF metadata from final artifacts with explicitly provisioned role keys.

No production keys are generated here. root.json must be provisioned out of band;
rotated N.root.json files remain on the repository. Call once per monotonically
increasing repository version, inside a protected release environment.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import zipfile

from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.release_packages import sha256, validate_release, archive_name


DEFAULT_EXPIRY_DAYS = {'targets': 30, 'snapshot': 7, 'timestamp': 1}
# One manually signed key cannot refresh a one-day timestamp; republish yearly.
SINGLE_KEY_EXPIRY_DAYS = {'targets': 365, 'snapshot': 365, 'timestamp': 365}


def publish(artifacts, output, root_path, signers, *, version, now=None, expiry_days=None):
    now = now or datetime.now(timezone.utc)
    days = {**DEFAULT_EXPIRY_DAYS, **(expiry_days or {})}
    root = Metadata.from_file(str(root_path))
    if not isinstance(root.signed, Root) or root.signed.expires <= now or not root.signed.consistent_snapshot:
        raise ValueError('Valid consistent-snapshot TUF root required')
    root.verify_delegate('root', root)
    if type(version) is not int or version < 1:
        raise ValueError('Positive metadata version required')
    output = Path(output)
    if output.exists():
        raise ValueError('Publish a new staging repository, never overwrite an active repository')
    targets = Metadata(Targets(version=version, expires=now + timedelta(days=days['targets'])))
    paths = []
    for description in sorted(Path(artifacts).glob('*.target.json')):
        value = json.loads(description.read_text())
        name = str(archive_name(value['path']))
        path = Path(artifacts) / name
        if path.stat().st_size != value['length'] or sha256(path) != value['hashes']['sha256']:
            raise ValueError('Final artifact changed after build')
        with zipfile.ZipFile(path) as archive:
            embedded = archive.getinfo('orion-release.json')
            if embedded.file_size > 65536 or json.loads(archive.read(embedded)) != value['custom']:
                raise ValueError('Artifact manifest differs from target description')
        info = TargetFile.from_file(name, str(path), ['sha256'])
        info.unrecognized_fields['custom'] = validate_release(value['custom'])
        if name in targets.signed.targets:
            raise ValueError('Duplicate target identity')
        targets.signed.targets[name] = info
        paths.append((path, info))
    if not paths:
        raise ValueError('No final artifacts')

    def signed_bytes(role, metadata):
        for signer in signers[role]:
            metadata.sign(signer, append=True)
        root.verify_delegate(role, metadata)
        return metadata.to_bytes()

    target_bytes = signed_bytes('targets', targets)
    snapshot = Metadata(Snapshot(version=version, expires=now + timedelta(days=days['snapshot']),
        meta={'targets.json': MetaFile.from_data(version, target_bytes, ['sha256'])}))
    snapshot_bytes = signed_bytes('snapshot', snapshot)
    timestamp = Metadata(Timestamp(version=version, expires=now + timedelta(days=days['timestamp']),
        snapshot_meta=MetaFile.from_data(version, snapshot_bytes, ['sha256'])))
    timestamp_bytes = signed_bytes('timestamp', timestamp)
    # Complete all signature/threshold validation before publishing anything.
    (output / 'metadata').mkdir(parents=True)
    (output / 'targets').mkdir()
    (output / 'metadata' / f'{root.signed.version}.root.json').write_bytes(root.to_bytes())
    (output / 'metadata' / f'{version}.targets.json').write_bytes(target_bytes)
    (output / 'metadata' / f'{version}.snapshot.json').write_bytes(snapshot_bytes)
    (output / 'metadata/timestamp.json').write_bytes(timestamp_bytes)
    for path, info in paths:
        destination = output / 'targets' / str(Path(info.path).with_name(info.hashes['sha256'] + '.' + Path(info.path).name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Copy immutable inputs; the signed hash always refers to the final ZIP.
        import shutil
        shutil.copyfile(path, destination)
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--keys', type=Path, help='Private JSON role -> PEM paths (not logged)')
    parser.add_argument('--single-key', type=Path,
                        help='Encrypted key from release_keys.py init; requires its one-key public root')
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--channel', choices=['preview', 'beta', 'stable'], required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    args = parser.parse_args()
    from services.shared.release_acceptance import verify_acceptance
    verify_acceptance(args.artifacts, args.acceptance, args.channel)
    root = Metadata.from_file(str(args.root))
    if args.single_key is not None:
        from release_keys import load_signer, single_key_root
        if args.keys is not None or not single_key_root(root):
            raise ValueError('--single-key needs the one-key root and excludes --keys')
        signer = load_signer(args.single_key)
        signers = {role: [signer] for role in ('targets', 'snapshot', 'timestamp')}
        publish(args.artifacts, args.output, args.root, signers, version=args.version,
                expiry_days=SINGLE_KEY_EXPIRY_DAYS)
        sys.exit(0)
    if args.keys is None:
        raise ValueError('Provide --keys or --single-key')
    if root.signed.roles['root'].threshold < 2 or len(root.signed.roles['root'].keyids) < 3:
        raise ValueError('Production root policy requires at least 2-of-3 offline root keys')
    offline = set(root.signed.roles['root'].keyids)
    if any(offline.intersection(root.signed.roles[role].keyids) for role in ('targets', 'snapshot', 'timestamp')):
        raise ValueError('Offline root keys cannot be CI signing keys')
    keys = json.loads(args.keys.read_text())
    signers = {}
    for role in ('targets', 'snapshot', 'timestamp'):
        signers[role] = [CryptoSigner.from_priv_key_uri('file2:' + str(Path(path).resolve()), root.signed.keys[keyid])
                         for keyid, path in keys[role].items()]
    publish(args.artifacts, args.output, args.root, signers, version=args.version)
