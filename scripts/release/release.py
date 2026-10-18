"""One command for a release round: build (what changed), accept, sign and publish.

    .tmp\\stage05-tools\\Scripts\\python.exe scripts\\release\\release.py [--dry-run] [--no-publish]

1. `build_release.py` (build cache, parallel builds, full acceptance; reuses an identical earlier pass).
2. `publish_release.py` with the candidate directory and the acceptance result of step 1.
   The bootstrap bundle is reused when scripts/release/bootstrap.json still has the sequence of the
   newest staged bundle (its contents only differ because it bundles shared scripts); bump
   bootstrap.json to ship a new one.
The release key passphrase is asked once, by the publish step. Nothing is published when a step fails.
"""
import argparse
import json
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


def run_build(extra):
    command = [sys.executable, '-u', str(HERE / 'build_release.py'), '--accept', 'full',
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
    if candidates is None or result is None:
        raise SystemExit('Build finished but the candidate directory or the acceptance result was not reported.')
    return candidates, result


def staged_bootstrap():
    """Newest staged bootstrap bundle, with the sequence it was built for."""
    versions = sorted((p for p in (ROOT / '.tmp/release').glob('v*') if p.name[1:].isdigit()),
                      key=lambda p: int(p.name[1:]), reverse=True)
    for directory in versions:
        bundle = directory / 'artifacts' / BOOTSTRAP
        target = bundle.with_suffix('.target.json')
        if bundle.is_file() and target.is_file():
            return bundle, json.loads(target.read_text(encoding='utf-8'))['custom']['sequence']
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-publish', action='store_true', help='build and accept only')
    parser.add_argument('--dry-run', action='store_true', help='stage and sign everything, upload nothing')
    parser.add_argument('--force-accept', action='store_true', help='run the full acceptance again')
    args = parser.parse_args()
    candidates, result = run_build(['--force-accept'] if args.force_accept else [])
    print(f'\nBuilt and accepted: {candidates.name}\nAcceptance: {result}', flush=True)
    if args.no_publish:
        return
    command = [sys.executable, str(HERE / 'publish_release.py'), '--artifacts', str(candidates),
               '--accepted-result', str(result), '--inputs', str(ROOT / '.tmp/inputs')]
    bundle, sequence = staged_bootstrap()
    wanted = json.loads((HERE / 'bootstrap.json').read_text(encoding='utf-8-sig'))['sequence']
    if bundle is not None and sequence == wanted:
        print(f'Bootstrap unchanged (sequence {wanted}): reusing {bundle}', flush=True)
        command += ['--reuse-bootstrap', str(bundle)]
    else:
        print(f'New bootstrap (sequence {wanted}) will be built and published.', flush=True)
    if args.dry_run:
        command.append('--dry-run')
    raise SystemExit(subprocess.run(command, cwd=ROOT).returncode)


if __name__ == '__main__':
    main()
