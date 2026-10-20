"""One command to sign candidate packages and publish them to a GitHub Release feed.

Flow: stable target names -> acceptance binding -> single-key TUF metadata ->
installer bootstrap -> upload (targets first, timestamp.json last). Clients learn
"is there a newer version" from the signed `sequence`, so each changed component's
release json needs a higher `sequence`/`version`/`package_id`: release.cmd raises
them by itself (bump_versions.py) unless it runs with --no-bump. The bootstrap is a
signed target too (scripts/release/bootstrap.json), bumped the same way when its
bytes differ from the feed's; an unchanged bundle is passed with `--reuse-bootstrap`. It is signed twice under one
sequence: the combined bundle (installers, older updaters) and its code package + runtime layer (current updaters;
an unchanged layer is not uploaded again). `--dry-run` stages everything locally and uploads nothing.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]
from services.shared.release_packages import artifact_identity, is_layer_description, sha256

DEFAULT_REPO = 'orion-ai-assistant/orion-ai-assistant'
DEFAULT_TAG = 'feed'
ROUTER_REPO = 'orion-ai-assistant/orion-router'
# Two feeds, one key, one public root. Each has its own metadata version counter, its own TUF cache on
# the installed machine and its own bootstrap bundle. The Router package lives only in the Router feed.
FEEDS = {
    'hub': {'repo': DEFAULT_REPO, 'tag': DEFAULT_TAG, 'components': ('hub', 'client'), 'cache': 'release-cache'},
    'router': {'repo': ROUTER_REPO, 'tag': DEFAULT_TAG, 'components': ('router',), 'cache': 'release-cache-router'},
}
from services.shared.release_packages import BOOTSTRAP_CODE_TARGET, BOOTSTRAP_TARGET

# One stable name: the plain installer download (get-orion.ps1) and the signed TUF target.
BOOTSTRAP_ASSET = BOOTSTRAP_TARGET


def bootstrap_parts(bundle):
    """The combined bundle and, when build_bootstrap wrote them next to it, its code package and runtime layer:
    every file (each with its .target.json) that is published for one bootstrap."""
    bundle = Path(bundle)
    parts = [bundle]
    code = bundle.with_name(BOOTSTRAP_CODE_TARGET)
    if code.with_suffix('.target.json').is_file():
        runtime = json.loads(code.with_suffix('.target.json').read_text(encoding='utf-8'))['runtime']
        parts += [code, bundle.with_name(runtime['target'])]
    return parts


def copy_parts(parts, directory):
    for path in parts:
        for name in (path, path.with_suffix('.target.json')):
            shutil.copyfile(name, Path(directory) / name.name)


def feed_url(repo, tag):
    return f'https://github.com/{repo}/releases/download/{tag}/'


def stable_name(custom, split=False):
    """Version-free target name. A split Hub/Router code package (its runtime is a separate layer target) has its
    own name: bootstraps that predate split packages keep reading the combined name and never get a code-only ZIP."""
    middle = '-code' if split else ''
    return f"orion-{custom['component']}{middle}-{custom['platform']}-{custom['arch']}-{custom['profile']}.zip"


def legacy_names(components):
    """Combined-package names of these components that split packages replaced."""
    return {c: stable_name({'component': c, 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu'})
            for c in components if c in {'hub', 'router'}}


def stage_stable_names(artifacts, staging, components=None):
    """Copy candidate ZIPs under version-free target names; return component -> name.

    `components` limits the copy to this feed's packages (the candidate directory holds all three);
    the bootstrap candidate, if present, always belongs to every feed. Runtime layers keep their
    content-addressed names and are staged only when a staged code package references them."""
    staging = Path(staging)
    staging.mkdir(parents=True)
    names, layers, wanted = {}, {}, set()
    for description in sorted(Path(artifacts).glob('*.target.json')):
        value = json.loads(description.read_text(encoding='utf-8'))
        if is_layer_description(value):
            layers[value['path']] = value
            continue
        name = stable_name(value['custom'], split='runtime' in value)
        component = value['custom']['component']
        if components is not None and component not in components and component != 'bootstrap':
            continue
        if component in names:
            raise ValueError('Two candidates for ' + component)
        shutil.copyfile(Path(artifacts) / value['path'], staging / name)
        value['path'] = name
        (staging / (name[:-4] + '.target.json')).write_text(json.dumps(value), encoding='utf-8')
        names[component] = name
        if 'runtime' in value:
            wanted.add(value['runtime']['target'])
    for name in sorted(wanted):
        if name not in layers:
            raise ValueError('Runtime layer missing from the candidates: ' + name)
        shutil.copyfile(Path(artifacts) / name, staging / name)
        (staging / (name[:-4] + '.target.json')).write_text(json.dumps(layers[name]), encoding='utf-8')
    if not names:
        raise ValueError('No candidate packages')
    return names


def candidate_names(artifacts, components):
    """component -> (stable target name, target description) of the code packages of `components` in a candidate
    directory, without staging anything: what stage_stable_names would publish under which name."""
    found = {}
    for description in sorted(Path(artifacts).glob('*.target.json')):
        value = json.loads(description.read_text(encoding='utf-8'))
        if is_layer_description(value) or value['custom']['component'] not in components:
            continue
        found[value['custom']['component']] = (stable_name(value['custom'], split='runtime' in value), value)
    return found


def default_source(feed, names):
    """release-source.json of a feed's bootstrap with the default repositories (what release.cmd publishes)."""
    router = stable_name({'component': 'router', 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu'}, split=True)
    return build_source(feed, names, repo=FEEDS[feed]['repo'], tag=FEEDS[feed]['tag'], router_repo=ROUTER_REPO,
                        router_tag='feed', router_target=router)


def staged_descriptions(staging):
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(staging).glob('*.target.json'))]


def write_unaccepted(staging, out):
    """Explicit developer choice (release.cmd --no-accept): bind the exact package set, with no acceptance run."""
    packages = {artifact_identity(t): sha256(Path(staging) / t['path']) for t in staged_descriptions(staging)}
    print('WARNING: publishing WITHOUT the automated package acceptance (--skip-acceptance).', flush=True)
    Path(out).write_text(json.dumps({'schema': 1, 'channel': 'preview', 'test_only': False, 'packages': packages,
                                     'checks': {}, 'acceptance_skipped': True}), encoding='utf-8')


def write_acceptance(staging, accepted_result, out):
    """Bind the automated isolated acceptance run to this exact package set."""
    result = json.loads(Path(accepted_result).read_text(encoding='utf-8'))
    if result.get('success') is not True:
        raise ValueError('The acceptance run did not succeed')
    if result.get('mode', 'full') != 'full':
        # accept_packages.py --mode fast is an iteration pre-check; only a full run of the exact set counts.
        raise ValueError('Only a full acceptance run (accept_packages.py --mode full) can be published')
    descriptions = staged_descriptions(staging)
    packages = {artifact_identity(t): sha256(Path(staging) / t['path']) for t in descriptions}
    accepted = {item['package_id'] for item in result.get('artifacts', [])}
    # The bootstrap bundle is signed with the set but is not a product package of the acceptance run; a runtime
    # layer is pinned (name, sha256) by the accepted code package that was installed together with it.
    other = {artifact_identity(t) for t in descriptions
             if is_layer_description(t) or t['custom']['component'] == 'bootstrap'}
    # A feed publishes a subset of the accepted set (Hub+Client or Router); never a package that was not accepted.
    if not set(packages) - other or not set(packages) - other <= accepted:
        raise ValueError('Acceptance result is for different packages; rerun accept_packages.py')
    Path(out).write_text(json.dumps({'schema': 1, 'channel': 'preview', 'test_only': False,
                                     'packages': packages, 'checks': {}}), encoding='utf-8')


def build_source(feed, names, *, repo, tag, router_repo=None, router_tag='feed', router_target=None):
    """release-source.json shipped inside this feed's bootstrap bundle.

    Hub feed: the Hub/Client feed (keeps the long-standing `release-cache`) plus, unless `router_repo` is
    None, the Router feed, so an installed Hub learns where its Router comes from.
    Router feed: only the Router feed (a Router-only installation)."""
    own = {'metadata_url': feed_url(repo, tag), 'targets_url': feed_url(repo, tag),
           'targets': {k: v for k, v in names.items() if k != 'bootstrap'}, 'cache': FEEDS[feed]['cache']}
    sources = [own]
    if feed == 'hub' and router_repo:
        sources.append({'metadata_url': feed_url(router_repo, router_tag), 'targets_url': feed_url(router_repo, router_tag),
                        'targets': {'router': router_target}, 'cache': FEEDS['router']['cache']})
    from services.shared.automatic_updates import validate_source
    return validate_source({'schema': 2, 'channel': 'stable', 'sources': sources})


def current_feed_version(base):
    try:
        with urllib.request.urlopen(base + 'timestamp.json', timeout=30) as response:
            return json.loads(response.read())['signed']['version']
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 0
        raise


def bootstrap_gate(previous_targets, custom, digest, name=BOOTSTRAP_TARGET):
    """Refuse a bundle that would contradict what the feed already published (`name`: the combined bundle or the
    code package; each is immutable under its sequence on its own).

    Clients reject changed bytes under an already-seen sequence (immutable target) and
    never accept a lower one, so catch both here instead of publishing a dead update.
    """
    previous = (previous_targets or {}).get('signed', {}).get('targets', {}).get(name)
    if previous is None:
        return
    sequence = previous['custom']['sequence']
    if custom['sequence'] < sequence:
        raise ValueError(f"Bootstrap sequence {custom['sequence']} is below the published {sequence}")
    if custom['sequence'] == sequence and previous['hashes']['sha256'] != digest:
        raise ValueError(f'Bootstrap contents changed but sequence {sequence} was already published: bump '
                         'sequence/version/package_id in scripts/release/bootstrap.json, or pass '
                         '--reuse-bootstrap <the published bundle> to keep it unchanged')


def package_gate(previous_targets, staging, names):
    """Same rule as the bootstrap's, for Hub/Client/Router: published bytes under a sequence never change and the
    sequence never goes down. This is what makes it safe for the build step to replace local, unpublished candidates.
    The first split package of a component must also pass its last combined package (installed PCs remember the
    newest sequence they saw per component)."""
    published = (previous_targets or {}).get('signed', {}).get('targets', {})
    for component, name in names.items():
        if component == 'bootstrap':
            continue
        staged = json.loads((Path(staging) / (name[:-4] + '.target.json')).read_text(encoding='utf-8'))
        legacy = published.get(legacy_names([component]).get(component))
        if legacy is not None and name != legacy_names([component])[component] \
                and staged['custom']['sequence'] <= legacy['custom']['sequence']:
            raise ValueError(f"{component} sequence {staged['custom']['sequence']} must be above the published "
                             f"combined package's {legacy['custom']['sequence']}")
        previous = published.get(name)
        if previous is None:
            continue
        sequence, published_sequence = staged['custom']['sequence'], previous['custom']['sequence']
        if sequence < published_sequence:
            raise ValueError(f'{component} sequence {sequence} is below the published {published_sequence}')
        if sequence == published_sequence and previous['hashes']['sha256'] != staged['hashes']['sha256']:
            raise ValueError(f'{component} contents changed but sequence {sequence} was already published: bump '
                             f'sequence/version/package_id in its release json (release.cmd does this unless --no-bump)')


def carried_targets(previous_targets, names, components):
    """Last combined Hub/Router targets, listed again unchanged while their split successors are published.

    Bootstraps that predate split packages read only those names: they keep seeing their current version (no
    update, no error) and adopt the new bootstrap, which reads the code targets. Nothing is uploaded for them."""
    published = (previous_targets or {}).get('signed', {}).get('targets', {})
    return {legacy: published[legacy] for component, legacy in legacy_names(components).items()
            if legacy in published and names.get(component) not in (None, legacy)}


def bootstrap_source_gate(bundle, names):
    """The bootstrap shipped with split packages must point at their names (it is what installs and updates them);
    a reused older bundle would keep every PC on the combined targets."""
    import zipfile
    from services.shared.automatic_updates import normalize_sources
    with zipfile.ZipFile(bundle) as archive:
        if 'release-source.json' not in archive.namelist():
            return
        source = json.loads(archive.read('release-source.json'))
    offered = {kind: name for feed in normalize_sources(source) for kind, name in feed['targets'].items()}
    stale = sorted(kind for kind, name in names.items() if kind in offered and offered[kind] != name)
    if stale:
        raise ValueError('The bootstrap bundle points at other target names for ' + ', '.join(stale) +
                         ' (packages are split into code + runtime layer now): bump sequence/version/package_id in '
                         'scripts/release/bootstrap.json so a new bootstrap is built')


def published_targets(base, version):
    if version < 1:
        return None
    with urllib.request.urlopen(base + f'{version}.targets.json', timeout=30) as response:
        return json.loads(response.read())


def online_assets(repo, tag):
    """name -> sha256 hex (None when GitHub did not report a digest) of the assets already on the release."""
    out = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'assets'],
                         capture_output=True, text=True, check=True).stdout
    assets = {}
    for asset in json.loads(out).get('assets', []):
        digest = asset.get('digest') or ''
        assets[asset['name']] = digest[len('sha256:'):] if digest.startswith('sha256:') else None
    return assets


def needs_upload(path, existing, version=None):
    """Hash-prefixed packages never change under the same name: skip what is already online (e.g. an unchanged
    Client) instead of re-uploading 30-170 MB. Two names can hold other bytes and are uploaded unless the online
    copy has exactly these (GitHub reports each asset's sha256): the plain bootstrap asset (one name across
    versions) and this version's own metadata (an earlier run that stopped before its timestamp may have left a
    differently signed copy, which would break every client's hash check against the new timestamp)."""
    path = Path(path)
    if path.name not in existing:
        return True
    if path.name != BOOTSTRAP_ASSET and not (version is not None and path.name.startswith(f'{version}.')):
        return False
    return existing[path.name] != sha256(path)


def gh(*argv, check=True):
    return subprocess.run(['gh', *argv], check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--accepted-result', type=Path, help='result.json of accept_packages.py')
    parser.add_argument('--skip-acceptance', action='store_true',
                        help='publish without the automated package acceptance (developer iterations only)')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--key', type=Path, default=Path.home() / '.orion-release/release-key.pem')
    parser.add_argument('--public-root', type=Path, default=HERE / 'trusted-root.json')
    parser.add_argument('--feed', choices=sorted(FEEDS), default='hub',
                        help="hub: Hub + Client feed (default). router: the Router's own feed")
    parser.add_argument('--repo', help='GitHub repository of this feed (default depends on --feed)')
    parser.add_argument('--tag', help="release tag of this feed (default 'feed')")
    parser.add_argument('--router-repo', default=ROUTER_REPO,
                        help="Hub feed only: repository of the Router feed the Hub bootstrap points at")
    parser.add_argument('--router-tag', default='feed')
    parser.add_argument('--no-router-source', action='store_true',
                        help='Hub feed only: bootstrap without a Router feed source')
    parser.add_argument('--work', type=Path, default=ROOT / '.tmp/release')
    parser.add_argument('--version', type=int, help='Metadata version; default is feed version + 1')
    parser.add_argument('--reuse-bootstrap', type=Path,
                        help='Previously built orion-bootstrap-windows-x64.zip (with its .target.json) to publish unchanged')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--defer-timestamp', type=Path,
                        help='upload everything except timestamp.json and record it in this JSON file instead; '
                             'release.py publishes the timestamps of all feeds back to back at the end')
    args = parser.parse_args()
    if bool(args.accepted_result) == bool(args.skip_acceptance):
        parser.error('Pass exactly one of --accepted-result <result.json> or --skip-acceptance')
    args.repo = args.repo or FEEDS[args.feed]['repo']
    args.tag = args.tag or FEEDS[args.feed]['tag']
    base = feed_url(args.repo, args.tag)
    # Unlock once up front so a wrong passphrase fails before anything is staged;
    # the child signing processes get it only through their own environment.
    import release_keys
    passphrase = release_keys._passphrase(enforce_length=False)
    release_keys.load_signer(args.key, passphrase)
    os.environ[release_keys.PASSPHRASE_ENV] = passphrase.decode('utf-8')
    version = args.version or current_feed_version(base) + 1
    work = args.work / f'{args.feed}-v{version}'  # one staging folder per feed (their versions count separately)
    if work.exists():
        # Staging of a version that is not online yet (the timestamp goes up last) is disposable: a run that
        # failed anywhere before that just starts over; uploads already done are skipped below.
        print(f'Discarding the unfinished staging of version {version}: {work}')
        if args.reuse_bootstrap and Path(args.reuse_bootstrap).resolve().is_relative_to(work.resolve()):
            # The bundle to reuse lives in exactly this unfinished staging: keep a copy before it is discarded.
            keep = Path(tempfile.mkdtemp(prefix='orion-bootstrap-reuse-'))
            copy_parts(bootstrap_parts(args.reuse_bootstrap), keep)
            args.reuse_bootstrap = keep / Path(args.reuse_bootstrap).name
        shutil.rmtree(work)
    names = stage_stable_names(args.artifacts, work / 'artifacts', FEEDS[args.feed]['components'])
    if not (set(names) - {'bootstrap'}):
        raise ValueError(f"No {'/'.join(FEEDS[args.feed]['components'])} candidate for the {args.feed} feed in {args.artifacts}")
    router_name = stable_name({'component': 'router', 'platform': 'windows', 'arch': 'x86_64', 'profile': 'cpu'},
                              split=True)
    source = build_source(args.feed, names, repo=args.repo, tag=args.tag,
                          router_repo=None if args.no_router_source else args.router_repo,
                          router_tag=args.router_tag, router_target=router_name)
    (work / 'release-source.json').write_text(json.dumps(source), encoding='utf-8')
    bundle = work / 'artifacts' / BOOTSTRAP_ASSET
    if args.reuse_bootstrap:
        if args.reuse_bootstrap.name != BOOTSTRAP_ASSET:
            raise ValueError('--reuse-bootstrap expects a ' + BOOTSTRAP_ASSET + ' written by build_bootstrap.py')
        copy_parts(bootstrap_parts(args.reuse_bootstrap), bundle.parent)
    else:
        subprocess.run([sys.executable, str(HERE / 'build_bootstrap.py'), '--trusted-root', str(args.public_root),
                        '--inputs', str(args.inputs), '--output', str(bundle), '--single-key',
                        '--release-source', str(work / 'release-source.json')], check=True)
    parts = bootstrap_parts(bundle)
    if len(parts) == 1:
        # Without its code package every installed bootstrap would download the whole embedded Python again.
        raise ValueError('The bootstrap has no code package next to it (built before split bootstraps): rebuild it')
    online = published_targets(base, current_feed_version(base))
    for part in parts[:2]:
        description = json.loads(part.with_suffix('.target.json').read_text(encoding='utf-8'))
        if description['hashes']['sha256'] != sha256(part) or description['custom']['component'] != 'bootstrap':
            raise ValueError('Bootstrap bundle does not match its target description')
        bootstrap_gate(online, description['custom'], description['hashes']['sha256'], part.name)
    bootstrap_source_gate(bundle, {k: v for k, v in names.items() if k != 'bootstrap'})
    package_gate(online, work / 'artifacts', names)
    carry = carried_targets(online, names, FEEDS[args.feed]['components'])
    (work / 'carry.json').write_text(json.dumps(carry), encoding='utf-8')
    if args.skip_acceptance:
        write_unaccepted(work / 'artifacts', work / 'acceptance.json')
    else:
        write_acceptance(work / 'artifacts', args.accepted_result, work / 'acceptance.json')
    subprocess.run([sys.executable, str(HERE / 'publish_metadata.py'), '--artifacts', str(work / 'artifacts'),
                    '--output', str(work / 'repository'), '--root', str(args.public_root), '--single-key', str(args.key),
                    '--version', str(version), '--channel', 'preview', '--acceptance', str(work / 'acceptance.json'),
                    '--carry', str(work / 'carry.json')],
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
    existing = online_assets(args.repo, args.tag)
    for path in assets:
        if not needs_upload(path, existing, version):
            print('Already published, skipping:', path.name, flush=True)
            continue
        gh('release', 'upload', args.tag, str(path), '--repo', args.repo, '--clobber')
    # Publishing the timestamp last makes the new version visible atomically.
    if args.defer_timestamp:
        args.defer_timestamp.write_text(json.dumps({'repo': args.repo, 'tag': args.tag,
                                                    'file': str(repository / 'metadata/timestamp.json')}), encoding='utf-8')
        print('Everything uploaded; timestamp.json deferred (the new version is not visible yet).')
        return
    gh('release', 'upload', args.tag, str(repository / 'metadata/timestamp.json'), '--repo', args.repo, '--clobber')
    print('Published. Installer:', base + BOOTSTRAP_ASSET)


if __name__ == '__main__':
    main()
