"""One command to sign candidate packages and publish them to a GitHub Release feed.

Flow: stable target names -> acceptance binding -> single-key TUF metadata ->
installer bootstrap -> upload (targets first, timestamp.json last). Clients learn
"is there a newer version" from the signed `sequence`, so bump `sequence` and
`version` in each component's release json before building. `--dry-run` stages
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
BOOTSTRAP_ASSET = 'orion-bootstrap-windows-x64.zip'


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
    packages = {}
    for path in Path(staging).glob('*.target.json'):
        target = json.loads(path.read_text(encoding='utf-8'))
        packages[target['custom']['package_id']] = sha256(Path(staging) / target['path'])
    accepted = {item['package_id'] for item in result.get('artifacts', [])}
    if accepted != set(packages):
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
    write_acceptance(work / 'artifacts', args.accepted_result, work / 'acceptance.json')
    source = {'schema': 1, 'channel': 'stable', 'metadata_url': base, 'targets_url': base, 'targets': names}
    (work / 'release-source.json').write_text(json.dumps(source), encoding='utf-8')
    subprocess.run([sys.executable, str(HERE / 'publish_metadata.py'), '--artifacts', str(work / 'artifacts'),
                    '--output', str(work / 'repository'), '--root', str(args.public_root), '--single-key', str(args.key),
                    '--version', str(version), '--channel', 'preview', '--acceptance', str(work / 'acceptance.json')],
                   check=True)
    subprocess.run([sys.executable, str(HERE / 'build_bootstrap.py'), '--trusted-root', str(args.public_root),
                    '--inputs', str(args.inputs), '--output', str(work / BOOTSTRAP_ASSET), '--single-key',
                    '--release-source', str(work / 'release-source.json')], check=True)
    repository = work / 'repository'
    versioned = sorted(p for p in (repository / 'targets').iterdir()) + \
        sorted(p for p in (repository / 'metadata').iterdir() if p.name != 'timestamp.json')
    assets = [*versioned, work / BOOTSTRAP_ASSET]
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
