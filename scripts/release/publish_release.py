"""One command to sign candidate packages and publish them to a GitHub Release feed.

Flow: stable target names -> acceptance binding -> single-key TUF metadata ->
installer bootstrap -> upload (targets first, timestamp.json last). Clients learn
"is there a newer version" from the signed `sequence`, so bump `sequence` and
`version` in each component's release json before building. The bootstrap is a
signed target too (scripts/release/bootstrap.json): bump its `sequence`/`version`/
`package_id` whenever its contents (services/shared, scripts/release) changed and
installed bootstraps should adopt it; otherwise reuse the published bundle with
`--reuse-bootstrap`. `--dry-run` stages
everything locally and uploads nothing.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]
from services.shared.release_packages import sha256

DEFAULT_REPO = 'orion-ai-assistant/orion-ai-assistant'
DEFAULT_TAG = 'feed'
from services.shared.bootstrap_update import BOOTSTRAP_TARGET

# One stable name: the plain installer download (get-orion.ps1) and the signed TUF target.
BOOTSTRAP_ASSET = BOOTSTRAP_TARGET


def feed_url(repo, tag):
    return f'https://github.com/{repo}/releases/download/{tag}/'


def stable_name(custom):
    return f"orion-{custom['component']}-{custom['platform']}-{custom['arch']}-{custom['profile']}.zip"


def stage_stable_names(artifacts, staging):
    """Copy candidate ZIPs under version-free target names; return component -> name."""
    staging = Path(staging)
    staging.mkdir(parents=True)
    names = {}
    for description in sorted(Path(artifacts).glob('*.target.json')):
        value = json.loads(description.read_text(encoding='utf-8'))
        name = stable_name(value['custom'])
        component = value['custom']['component']
        if component in names:
            raise ValueError('Two candidates for ' + component)
        shutil.copyfile(Path(artifacts) / value['path'], staging / name)
        value['path'] = name
        (staging / (name[:-4] + '.target.json')).write_text(json.dumps(value), encoding='utf-8')
        names[component] = name
    if not names:
        raise ValueError('No candidate packages')
    return names


def write_acceptance(staging, accepted_result, out):
    """Bind the automated isolated acceptance run to this exact package set."""
    result = json.loads(Path(accepted_result).read_text(encoding='utf-8'))
    if result.get('success') is not True:
        raise ValueError('The acceptance run did not succeed')
    if result.get('mode', 'full') != 'full':
        # accept_packages.py --mode fast is an iteration pre-check; only a full run of the exact set counts.
        raise ValueError('Only a full acceptance run (accept_packages.py --mode full) can be published')
    packages = {}
    for path in Path(staging).glob('*.target.json'):
        target = json.loads(path.read_text(encoding='utf-8'))
        packages[target['custom']['package_id']] = sha256(Path(staging) / target['path'])
    accepted = {item['package_id'] for item in result.get('artifacts', [])}
    # The bootstrap bundle is signed with the set but is not a product package of the acceptance run.
    bootstrap = {t['custom']['package_id'] for t in (json.loads(p.read_text(encoding='utf-8'))
                 for p in Path(staging).glob('*.target.json')) if t['custom']['component'] == 'bootstrap'}
    if accepted != set(packages) - bootstrap:
        raise ValueError('Acceptance result is for different packages; rerun accept_packages.py')
    Path(out).write_text(json.dumps({'schema': 1, 'channel': 'preview', 'test_only': False,
                                     'packages': packages, 'checks': {}}), encoding='utf-8')


def current_feed_version(base):
    try:
        with urllib.request.urlopen(base + 'timestamp.json', timeout=30) as response:
            return json.loads(response.read())['signed']['version']
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 0
        raise


def bootstrap_gate(previous_targets, custom, digest):
    """Refuse a bundle that would contradict what the feed already published.

    Clients reject changed bytes under an already-seen sequence (immutable target) and
    never accept a lower one, so catch both here instead of publishing a dead update.
    """
    previous = (previous_targets or {}).get('signed', {}).get('targets', {}).get(BOOTSTRAP_TARGET)
    if previous is None:
        return
    sequence = previous['custom']['sequence']
    if custom['sequence'] < sequence:
        raise ValueError(f"Bootstrap sequence {custom['sequence']} is below the published {sequence}")
    if custom['sequence'] == sequence and previous['hashes']['sha256'] != digest:
        raise ValueError(f'Bootstrap contents changed but sequence {sequence} was already published: bump '
                         'sequence/version/package_id in scripts/release/bootstrap.json, or pass '
                         '--reuse-bootstrap <the published bundle> to keep it unchanged')


def published_targets(base, version):
    if version < 1:
        return None
    with urllib.request.urlopen(base + f'{version}.targets.json', timeout=30) as response:
        return json.loads(response.read())


def gh(*argv, check=True):
    return subprocess.run(['gh', *argv], check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--accepted-result', type=Path, required=True, help='result.json of accept_packages.py')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--key', type=Path, default=Path.home() / '.orion-release/release-key.pem')
    parser.add_argument('--public-root', type=Path, default=HERE / 'trusted-root.json')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--tag', default=DEFAULT_TAG)
    parser.add_argument('--work', type=Path, default=ROOT / '.tmp/release')
    parser.add_argument('--version', type=int, help='Metadata version; default is feed version + 1')
    parser.add_argument('--reuse-bootstrap', type=Path,
                        help='Previously built orion-bootstrap-windows-x64.zip (with its .target.json) to publish unchanged')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    base = feed_url(args.repo, args.tag)
    # Unlock once up front so a wrong passphrase fails before anything is staged;
    # the child signing processes get it only through their own environment.
    import release_keys
    passphrase = release_keys._passphrase(enforce_length=False)
    release_keys.load_signer(args.key, passphrase)
    os.environ[release_keys.PASSPHRASE_ENV] = passphrase.decode('utf-8')
    version = args.version or current_feed_version(base) + 1
    work = args.work / f'v{version}'
    if work.exists():
        raise ValueError(f'{work} exists; remove it or choose another --version')
    names = stage_stable_names(args.artifacts, work / 'artifacts')
    source = {'schema': 1, 'channel': 'stable', 'metadata_url': base, 'targets_url': base, 'targets': names}
    (work / 'release-source.json').write_text(json.dumps(source), encoding='utf-8')
    bundle = work / 'artifacts' / BOOTSTRAP_ASSET
    if args.reuse_bootstrap:
        shutil.copyfile(args.reuse_bootstrap, bundle)
        shutil.copyfile(args.reuse_bootstrap.with_suffix('.target.json'), bundle.with_suffix('.target.json'))
    else:
        subprocess.run([sys.executable, str(HERE / 'build_bootstrap.py'), '--trusted-root', str(args.public_root),
                        '--inputs', str(args.inputs), '--output', str(bundle), '--single-key',
                        '--release-source', str(work / 'release-source.json')], check=True)
    description = json.loads(bundle.with_suffix('.target.json').read_text(encoding='utf-8'))
    if description['hashes']['sha256'] != sha256(bundle) or description['custom']['component'] != 'bootstrap':
        raise ValueError('Bootstrap bundle does not match its target description')
    bootstrap_gate(published_targets(base, current_feed_version(base)), description['custom'], description['hashes']['sha256'])
    write_acceptance(work / 'artifacts', args.accepted_result, work / 'acceptance.json')
    subprocess.run([sys.executable, str(HERE / 'publish_metadata.py'), '--artifacts', str(work / 'artifacts'),
                    '--output', str(work / 'repository'), '--root', str(args.public_root), '--single-key', str(args.key),
                    '--version', str(version), '--channel', 'preview', '--acceptance', str(work / 'acceptance.json')],
                   check=True)
    repository = work / 'repository'
    versioned = sorted(p for p in (repository / 'targets').iterdir()) + \
        sorted(p for p in (repository / 'metadata').iterdir() if p.name != 'timestamp.json')
    assets = [*versioned, bundle]  # the plain asset is the unsigned-name copy get-orion.ps1 downloads
    print(f'Staged feed version {version} in {work}')
    for path in [*assets, repository / 'metadata/timestamp.json']:
        print(' ', path.name, f'{path.stat().st_size / 1e6:.1f} MB')
    if args.dry_run:
        print('Dry run: nothing uploaded.')
        return
    if gh('release', 'view', args.tag, '--repo', args.repo, check=False).returncode != 0:
        gh('release', 'create', args.tag, '--repo', args.repo, '--title', 'Orion release feed',
           '--notes', 'Signed package feed read by the Orion installer and updater. Do not edit by hand.')
    existing = set(subprocess.run(['gh', 'release', 'view', args.tag, '--repo', args.repo, '--json', 'assets',
                                   '--jq', '.assets[].name'], capture_output=True, text=True, check=True).stdout.split())
    for path in assets:
        # Hash-prefixed packages and versioned metadata never change under the same name:
        # skip what is already online (e.g. an unchanged Client) instead of re-uploading 30-170 MB.
        if path.name in existing and path.name != BOOTSTRAP_ASSET:
            print('Already published, skipping:', path.name, flush=True)
            continue
        gh('release', 'upload', args.tag, str(path), '--repo', args.repo, '--clobber')
    # Publishing the timestamp last makes the new version visible atomically.
    gh('release', 'upload', args.tag, str(repository / 'metadata/timestamp.json'), '--repo', args.repo, '--clobber')
    print('Published. Installer:', base + BOOTSTRAP_ASSET)


if __name__ == '__main__':
    main()
