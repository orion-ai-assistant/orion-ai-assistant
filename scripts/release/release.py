"""One command for a release round: build (what changed), accept, sign and publish.

    .tmp\\stage05-tools\\Scripts\\python.exe scripts\\release\\release.py [--dry-run] [--no-publish]

1. `build_release.py` (build cache, parallel builds, full acceptance; reuses an identical earlier pass).
2. `publish_release.py --feed router` and then `--feed hub` (two feeds, same key) with the candidate directory
   and the acceptance result of step 1. Each feed's bootstrap bundle is reused when scripts/release/bootstrap.json still has the sequence of the
   newest staged bundle of that feed (its contents only differ because it bundles shared scripts); bump
   bootstrap.json to ship a new one.
The release key passphrase is asked once, first (a wrong one fails before the build). Nothing is published when a step fails.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BOOTSTRAP = 'orion-bootstrap-windows-x64.zip'
# Outside %LOCALAPPDATA%\Orion: an installed Orion must not be touched by acceptance runs, and a packaged
# desktop host would redirect that folder into a private store.
SCRATCH = Path.home() / '.orion-lifecycle-tests'


def run_build(extra, accept='full'):
    # Child output may hold characters the console code page (e.g. cp1254) cannot show: never crash on them.
    sys.stdout.reconfigure(errors='replace')
    command = [sys.executable, '-u', str(HERE / 'build_release.py'), '--accept', accept,
               '--scratch-base', str(SCRATCH), *extra]
    candidates = result = None
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace')
    for line in process.stdout:
        print(line, end='', flush=True)
        found = re.match(r'Candidates ready in (.+?): ', line)
        if found:
            candidates = Path(found.group(1))
        found = re.search(r'\(publishable\): (.+result\.json)\s*$', line) or re.search(r'Result: (.+result\.json)\s', line)
        if found:
            result = Path(found.group(1))
    if process.wait():
        raise SystemExit('Build or acceptance failed: nothing was published.')
    if candidates is None or (result is None and accept != 'none'):
        raise SystemExit('Build finished but the candidate directory or the acceptance result was not reported.')
    return candidates, result


def staged_bootstrap(feed):
    """Newest staged bootstrap bundle of one feed, with the sequence it was built for."""
    versions = sorted((p for p in (ROOT / '.tmp/release').glob(f'{feed}-v*') if p.name.rsplit('v', 1)[1].isdigit()),
                      key=lambda p: int(p.name.rsplit('v', 1)[1]), reverse=True)
    for directory in versions:
        bundle = directory / 'artifacts' / BOOTSTRAP
        target = bundle.with_suffix('.target.json')
        if bundle.is_file() and target.is_file():
            return bundle, json.loads(target.read_text(encoding='utf-8'))['custom']['sequence']
    return None, None


def publish(feed, candidates, result, dry_run, deferred=None):
    command = [sys.executable, str(HERE / 'publish_release.py'), '--feed', feed, '--artifacts', str(candidates),
               '--inputs', str(ROOT / '.tmp/inputs')]
    command += ['--accepted-result', str(result)] if result else ['--skip-acceptance']
    if deferred is not None:
        command += ['--defer-timestamp', str(deferred)]
    bundle, sequence = staged_bootstrap(feed)
    wanted = json.loads((HERE / 'bootstrap.json').read_text(encoding='utf-8-sig'))['sequence']
    if bundle is not None and sequence == wanted:
        print(f'[{feed}] bootstrap unchanged (sequence {wanted}): reusing {bundle}', flush=True)
        command += ['--reuse-bootstrap', str(bundle)]
    else:
        print(f'[{feed}] new bootstrap (sequence {wanted}) will be built and published.', flush=True)
    if dry_run:
        command.append('--dry-run')
    code = subprocess.run(command, cwd=ROOT).returncode
    if code:
        raise SystemExit(f'Publishing the {feed} feed failed (exit {code}).')


def changed_feeds(candidates):
    """Feeds that have a package which is not online yet is decided by publish_release (it skips online assets),
    so every feed is offered; `--feeds` narrows it."""
    return ['router', 'hub']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-publish', action='store_true', help='build and accept only')
    parser.add_argument('--dry-run', action='store_true', help='stage and sign everything, upload nothing')
    parser.add_argument('--force-accept', action='store_true', help='run the full acceptance again')
    parser.add_argument('--no-accept', action='store_true',
                        help='skip the automated package acceptance (fast developer iterations; nothing proves the '
                             'packages install and start - use the default for anything users depend on)')
    parser.add_argument('--feeds', nargs='+', choices=['router', 'hub'], default=['router', 'hub'],
                        help='feeds to publish, in this order (default: router, then hub)')
    args = parser.parse_args()
    if not args.no_publish:
        # Ask first, once: a wrong passphrase must not cost a 4 minute build, and every publish step below
        # (Router feed, Hub feed) gets it through its environment instead of asking again.
        sys.path.insert(0, str(HERE))
        import release_keys
        passphrase = release_keys._passphrase(enforce_length=False)
        release_keys.load_signer(Path.home() / '.orion-release/release-key.pem', passphrase)
        os.environ[release_keys.PASSPHRASE_ENV] = passphrase.decode('utf-8')
    if args.no_accept:
        print('WARNING: --no-accept: the packages are published without the automated install/start acceptance.',
              flush=True)
    candidates, result = run_build(['--force-accept'] if args.force_accept else [],
                                   accept='none' if args.no_accept else 'full')
    print(f'\nBuilt: {candidates.name}\nAcceptance: {result or "SKIPPED (--no-accept)"}', flush=True)
    if args.no_publish:
        return
    # Every feed's packages and metadata go up first; the timestamps that make them visible follow back to back,
    # so a check in between cannot see a Router-only (or Hub-only) half of the release.
    deferred = []
    for feed in args.feeds:  # The Router feed first: the Hub feed no longer lists a Router target.
        marker = ROOT / '.tmp/release' / f'{feed}-timestamp.json'
        marker.unlink(missing_ok=True)
        publish(feed, candidates, result, args.dry_run, None if args.dry_run else marker)
        if marker.exists():
            deferred.append(json.loads(marker.read_text(encoding='utf-8')))
            marker.unlink()
    for item in deferred:
        subprocess.run(['gh', 'release', 'upload', item['tag'], item['file'], '--repo', item['repo'], '--clobber'],
                       check=True)
    if deferred:
        print('Published: ' + ', '.join(d['repo'] for d in deferred) + ' (timestamps uploaded back to back).')


if __name__ == '__main__':
    main()
