"""Automatic version bumps for release.cmd: nobody edits `version`/`sequence`/`package_id` by hand.

After a build (no acceptance yet), each package's bytes are compared with what the live feed lists for it
(builds are byte-reproducible: the same commit and inputs give the same sha256):
  - Hub and Client against the Hub feed, Router against the Router feed;
  - the bootstrap is built for each feed (each bundles its own release-source.json) and compared with both.
A component whose bytes differ gets sequence = published sequence + 1 and the next patch version
(0.2.6 -> 0.2.7) with a matching package_id, its release json is rewritten and the bump is committed in that
component's repository (never pushed). A version or sequence already set by hand above the published one is
kept. An unchanged component is left exactly as it is. release.cmd then builds again: the build cache rebuilds
only the bumped packages (orion-release.json, and with it the package id, is part of their bytes).
"""
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]
import publish_release as P
from build_release import COMPONENTS
from services.shared.bootstrap_update import BOOTSTRAP_TARGET
from services.shared.release_packages import sha256

BOOTSTRAP_RELEASE = HERE / 'bootstrap.json'
DISPLAY = {'hub': 'Hub', 'router': 'Router', 'client': 'Client', 'bootstrap': 'bootstrap'}
FEED_OF = {component: feed for feed, value in P.FEEDS.items() for component in value['components']}

# digest: sha256 published under the candidate's own target name (None: not under that name);
# sequence/version: the newest entry among that name and the component's last combined package.
Published = namedtuple('Published', 'digest sequence version')
# state: bumped (json rewritten), kept (bytes changed, the json was already above the feed), unchanged, new
Change = namedtuple('Change', 'component state before after sequence')


def version_key(version):
    return tuple(int(part) for part in version.split('.'))


def bump_patch(version):
    parts = version.split('.')
    parts[-1] = str(int(parts[-1]) + 1)
    return '.'.join(parts)


def published_state(targets, name, others=()):
    """What a feed's targets metadata lists for one component, or None when it lists nothing for it."""
    listed = (targets or {}).get('signed', {}).get('targets', {})
    entries = [listed[n] for n in (name, *others) if n in listed]
    if not entries:
        return None
    newest = max(entries, key=lambda entry: entry['custom']['sequence'])['custom']
    digest = listed[name]['hashes']['sha256'] if name in listed else None
    return Published(digest, newest['sequence'], newest['version'])


def with_version(release, version, sequence):
    new = {**release, 'version': version, 'sequence': sequence}
    new['package_id'] = f"orion-{release['component']}-{version}-{release['platform']}-{release['arch']}-{release['profile']}"
    return new


def next_release(release, published):
    """The release json for changed bytes: past the published sequence and version, hand-set higher values kept."""
    version = release['version']
    if version_key(version) <= version_key(published.version):
        version = bump_patch(published.version)
    return with_version(release, version, max(release['sequence'], published.sequence + 1))


def parse_version(text):
    """`release.cmd --version 0.3.1`: three dot-separated numbers."""
    parts = text.split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f'--version must look like 0.3.1, not {text!r}')
    return '.'.join(str(int(part)) for part in parts)


def check_version(version, live):
    """Refuse a --version that is not above every component's current (newest sequence) published version."""
    newest = {}
    for targets in live.values():
        for entry in (targets or {}).get('signed', {}).get('targets', {}).values():
            custom = entry.get('custom') or {}
            if custom.get('component') in DISPLAY and 'version' in custom and 'sequence' in custom:
                if custom['sequence'] >= newest.get(custom['component'], {}).get('sequence', -1):
                    newest[custom['component']] = custom
    too_low = sorted(f"{component} {custom['version']}" for component, custom in newest.items()
                     if version_key(version) <= version_key(custom['version']))
    if too_low:
        raise ValueError(f'--version {version} must be above every published version; published: ' + ', '.join(too_low))


def read_release(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_release(path, release):
    Path(path).write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8', newline='\n')


def commit(repository, files, message):
    """Commit exactly these files (other staged or modified files stay as they are). Never pushes."""
    subprocess.run(['git', '-C', str(repository), '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', message,
                    '--', *(str(Path(f).resolve().relative_to(Path(repository).resolve()).as_posix()) for f in files)],
                   check=True)


def decide(component, release, published, changed, version=None):
    """(Change, new release json or None). `version` (release.cmd --version) aligns the component to that version
    with sequence = published + 1 whether or not its bytes changed."""
    if published is None:
        if version is None or version == release['version']:
            return Change(component, 'new', None, release['version'], release['sequence']), None
        new = with_version(release, version, release['sequence'])
        return Change(component, 'bumped', None, version, release['sequence']), new
    if version is not None:
        new = with_version(release, version, published.sequence + 1)
    elif not changed:
        return Change(component, 'unchanged', published.version, release['version'], release['sequence']), None
    else:
        new = next_release(release, published)
    if new == release:
        return Change(component, 'kept', published.version, release['version'], release['sequence']), None
    return Change(component, 'bumped', published.version, new['version'], new['sequence']), new


def message(changes, version=None):
    reason = (f'release.cmd --version {version}: versions aligned' if version else
              'Version bumped by release.cmd: the package bytes differ from what the live feed lists.')
    return 'release: ' + ', '.join(f"{DISPLAY[c.component]} {c.after} (sequence {c.sequence})" for c in changes) + \
        '\n\n' + reason


def bump_packages(candidates, live, components=COMPONENTS, apply=True, version=None, feeds=tuple(P.FEEDS)):
    """Compare Hub/Router/Client candidates with their live feed; bump and commit the changed ones. With `version`,
    every component of the published `feeds` is set to it (the others still get the automatic bump)."""
    changes, commits = [], {}
    for feed, value in P.FEEDS.items():
        aligned = version if feed in feeds else None
        for component, (name, description) in sorted(P.candidate_names(candidates, value['components']).items()):
            config = components[component]
            release = read_release(config['release'])
            if release['package_id'] != description['custom']['package_id']:
                raise ValueError(f"{component}: the candidate {description['custom']['package_id']} was not built from "
                                 f"{config['release']} ({release['package_id']})")
            published = published_state(live.get(feed), name, P.legacy_names([component]).values())
            change, new = decide(component, release, published,
                                 published is not None and published.digest != description['hashes']['sha256'],
                                 aligned)
            changes.append(change)
            if new is not None and apply:
                write_release(config['release'], new)
                commits.setdefault(Path(config['source']), []).append((change, config['release']))
    for repository, items in commits.items():
        commit(repository, [path for _, path in items], message([change for change, _ in items], version))
    return sorted(changes, key=lambda change: list(components).index(change.component))


def build_bootstrap(source, output):
    """The feed's bootstrap bundle exactly as publish_release.py builds it (same inputs = same bytes)."""
    if output.parent.exists():
        shutil.rmtree(output.parent)
    output.parent.mkdir(parents=True)
    source_file = output.parent / 'release-source.json'
    source_file.write_text(json.dumps(source), encoding='utf-8')
    result = subprocess.run([sys.executable, str(HERE / 'build_bootstrap.py'),
                             '--trusted-root', str(HERE / 'trusted-root.json'), '--inputs', str(ROOT / '.tmp/inputs'),
                             '--output', str(output), '--single-key', '--release-source', str(source_file)],
                            capture_output=True, text=True, errors='replace')
    if result.returncode:
        raise RuntimeError(f'Building the bootstrap failed (exit {result.returncode}):\n{result.stdout}{result.stderr}')
    return output


def bump_bootstrap(candidates, live, work, build=build_bootstrap, release_path=BOOTSTRAP_RELEASE, repository=ROOT,
                   apply=True, version=None):
    """Build each feed's bootstrap, compare it with that feed's; on any difference bump bootstrap.json (one
    sequence for both feeds; to `version` when given) and build both again. An unchanged bootstrap keeps its
    version even with `version`. Returns (Change, {feed: bundle to publish unchanged})."""
    sources = {feed: P.default_source(feed, {c: n for c, (n, _) in
                                             P.candidate_names(candidates, value['components']).items()})
               for feed, value in P.FEEDS.items()}

    def build_all():
        with ThreadPoolExecutor(len(sources)) as pool:
            futures = {feed: pool.submit(build, source, Path(work) / feed / P.BOOTSTRAP_ASSET)
                       for feed, source in sources.items()}
            return {feed: future.result() for feed, future in futures.items()}

    bundles = build_all()
    release = read_release(release_path)
    states = {feed: published_state(live.get(feed), BOOTSTRAP_TARGET) for feed in sources}
    listed = [state for state in states.values() if state is not None]
    if not listed:
        return Change('bootstrap', 'new', None, release['version'], release['sequence']), bundles
    newest = max(listed, key=lambda state: state.sequence)
    changed = any(state is not None and state.digest != sha256(bundles[feed]) for feed, state in states.items())
    change, new = decide('bootstrap', release, newest, changed, version if changed else None)
    if new is not None and apply:
        write_release(release_path, new)
        bundles = build_all()
        commit(repository, [release_path], message([change], version if changed else None))
    return change, bundles


def live_targets(feed):
    base = P.feed_url(P.FEEDS[feed]['repo'], P.FEEDS[feed]['tag'])
    return P.published_targets(base, P.current_feed_version(base))


def summary(changes, arrow='→'):
    parts = []
    for c in changes:
        if c.state == 'bumped' and c.before is None:
            parts.append(f'{c.component} {c.after} (seq {c.sequence}, not on its feed yet)')
        elif c.state == 'bumped':
            parts.append(f'{c.component} {c.before} {arrow} {c.after} (seq {c.sequence})')
        elif c.state == 'kept':
            parts.append(f'{c.component} {c.before} {arrow} {c.after} (seq {c.sequence}, as set by hand)')
        elif c.state == 'unchanged':
            parts.append(f'{c.component} unchanged')
        else:
            parts.append(f'{c.component} {c.after} (seq {c.sequence}, not on its feed yet)')
    return ', '.join(parts)


def read_live(fetch=live_targets, version=None):
    """Targets metadata of both live feeds, read before the build: a network problem or a --version that is not
    above every published version stops release.cmd before minutes of building."""
    try:
        live = {feed: fetch(feed) for feed in P.FEEDS}
    except OSError as error:
        raise SystemExit(f'Could not read the live feeds to compare the packages with ({error}). '
                         'Retry, or pass --no-bump to use the release jsons as they are.')
    if version is not None:
        try:
            check_version(version, live)
        except ValueError as error:
            raise SystemExit(str(error))
    return live


def bump_round(candidates, work, live, version=None, feeds=tuple(P.FEEDS)):
    """release.cmd's step between the build and the acceptance. Returns the bootstrap bundle of each feed."""
    changes = bump_packages(candidates, live, version=version, feeds=feeds)
    bootstrap, bundles = bump_bootstrap(candidates, live, work, version=version)
    try:
        '→'.encode(sys.stdout.encoding or 'ascii')
        arrow = '→'
    except (LookupError, UnicodeEncodeError):
        arrow = '->'  # e.g. a cp1254 console
    print('Versions: ' + summary([*changes, bootstrap], arrow), flush=True)
    return [*changes, bootstrap], bundles
