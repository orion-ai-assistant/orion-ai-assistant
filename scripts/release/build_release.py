"""One command for a whole release round: web builds, three packages, acceptance, with timings.

    python scripts/release/build_release.py [--accept full|fast|none] [--serial] [--no-cache]

What it does (and what it skips, safely):
  1. Flutter web build and Router dashboard build, concurrently. Each is skipped when its source
     files and the previous build output are byte-for-byte what the last good build used.
  2. Hub, Router and Client packages, concurrently once their inputs exist. A package whose
     tracked sources, release json, pinned inputs, lock file and build tooling are unchanged is
     NOT rebuilt: the earlier ZIP is reused byte-for-byte (same hash, which the updater needs).
     A changed input always rebuilds. The big runtime part (Python wheels, PostgreSQL, Redis,
     FFmpeg) is prepared once and reused (see build_package.py).
  3. Acceptance (accept_packages.py): `full` is the publish gate for the exact set (skipped only
     if this exact set already passed with unchanged acceptance tooling), `fast` is a quick
     pre-check of what changed, `none` stops after the build.
Every step prints its wall-clock time; a table at the end shows where the minutes went.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]
import build_cache
from timing import Timings

def _peers():
    """Folder holding the Router and Flutter repositories: next to this checkout, or next to the main
    checkout when this one is a git worktree (e.g. .claude/worktrees/<name>)."""
    if (ROOT.parent / 'orion-router').is_dir():
        return ROOT.parent
    common = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--path-format=absolute',
                                      '--git-common-dir'], text=True).strip()
    return Path(common).parent.parent


PEERS = _peers()
COMPONENTS = {
    'hub': {'source': ROOT, 'release': HERE / 'hub.json'},
    'router': {'source': PEERS / 'orion-router', 'release': PEERS / 'orion-router/release/package.json'},
    'client': {'source': PEERS / 'orion-ai-assistant-flutter',
               'release': PEERS / 'orion-ai-assistant-flutter/release/package.json'},
}
# name -> (repo, source prefixes, working dir, output dir, command)
WEB = {
    'flutter web': dict(
        repo='client', prefixes=['main_app', 'packages'], cwd='main_app/app', output='main_app/app/build/web',
        command=['flutter', 'build', 'web', '--release', '--no-wasm-dry-run', '--pwa-strategy=none'], env={}),
    'router dashboard': dict(
        repo='router', prefixes=['dashboard'], cwd='dashboard', output='dashboard/out',
        command=['npm', 'run', 'build'], env={'NEXT_TELEMETRY_DISABLED': '1'}),
}
class Priority:
    """The Flutter web build (dart2js, ~1 min) is the critical path of a round that rebuilds it. While it
    compiles, the other steps (Next.js build, packaging) run at below-normal CPU priority: on an idle
    machine they still use the free cores, on a busy one they no longer slow dart2js down. When Flutter
    is reused from the cache every step keeps normal priority."""
    BELOW_NORMAL = 0x00004000  # Windows BELOW_NORMAL_PRIORITY_CLASS; children (node, python) inherit it

    def __init__(self):
        self.decided = threading.Event()
        self.flutter_builds = False

    def decide(self, builds):
        self.flutter_builds = builds
        self.decided.set()

    def flags(self, name):
        if name == 'flutter web' or os.name != 'nt':
            return 0
        self.decided.wait()
        return self.BELOW_NORMAL if self.flutter_builds else 0


def web_key(step, repo_root):
    """Key of every file the web build can read: tracked and new unignored files, local edits included.

    Ignored build output (build/, node_modules/, .next/, out/) is excluded by git itself, so the
    key cannot depend on the thing it guards."""
    names = build_cache.git_files(repo_root, step['prefixes'], untracked=True)
    return build_cache.combine(('sources', build_cache.hash_files({n: repo_root / n for n in names})),
                               ('command', step['command']))


def run_web(name, cache, timings, *, use_cache, log_dir, priority=None):
    priority = priority or Priority()
    try:
        return _run_web(name, cache, timings, use_cache=use_cache, log_dir=log_dir, priority=priority)
    finally:
        if name == 'flutter web':
            priority.decide(False)  # done, skipped or failed: later steps run at normal priority, none waits


def _run_web(name, cache, timings, *, use_cache, log_dir, priority):
    step = WEB[name]
    repo_root = COMPONENTS[step['repo']]['source']
    output = repo_root / step['output']
    record_name = 'web-' + name.replace(' ', '-') + '.json'
    with timings.step(name + ': cache key'):
        key = web_key(step, repo_root)
    record = cache.read_record(record_name)
    if use_cache and record and record.get('key') == key and (output / 'index.html').is_file():
        # The output directory must still be exactly what that build produced.
        if build_cache.hash_tree(output) == record.get('output'):
            timings.skipped(name, f'sources and output unchanged ({key[:12]})')
            return False
    if name == 'flutter web':
        priority.decide(True)
    executable = shutil.which(step['command'][0])
    if not executable:
        raise RuntimeError(f"{step['command'][0]} is not on PATH")
    log = log_dir / (name.replace(' ', '-') + '.log')
    with timings.step(name, 'log ' + str(log)):
        with log.open('wb') as stream:
            result = subprocess.run([executable, *step['command'][1:]], cwd=repo_root / step['cwd'],
                                    env={**os.environ, **step['env']}, stdout=stream, stderr=subprocess.STDOUT,
                                    creationflags=priority.flags(name))
        if result.returncode:
            raise RuntimeError(f'{name} failed (exit {result.returncode}); see {log}')
    cache.write_record(record_name, {'key': key, 'output': build_cache.hash_tree(output)})
    return True


def package_command(kind, output, args):
    config = COMPONENTS[kind]
    return [sys.executable, str(HERE / 'build_package.py'), '--source', str(config['source']),
            '--release', str(config['release']), '--output', str(output), '--inputs', str(args.inputs),
            '--postgres', str(args.inputs / 'postgresql-16.15-1-windows-x64-binaries.zip'),
            *(['--cache', str(args.cache)] if args.use_cache else [])]


def run_package(kind, work, args, timings, log_dir, after, priority=None):
    if after is not None:
        after.result()  # the web build this package embeds
    flags = priority.flags(f'package {kind}') if priority and kind != 'client' else 0
    output = work / kind
    log = log_dir / f'package-{kind}.log'
    with timings.step(f'package {kind}', 'log ' + str(log)):
        with log.open('wb') as stream:
            result = subprocess.run(package_command(kind, output, args), stdout=stream, stderr=subprocess.STDOUT,
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, creationflags=flags)
    summary = log.read_text(encoding='utf-8', errors='replace').split('--- build ')[-1] if log.exists() else ''
    if result.returncode:
        raise RuntimeError(f'package {kind} failed (exit {result.returncode}); see {log}')
    reused = 'reused identical ZIP' in log.read_text(encoding='utf-8', errors='replace')
    return kind, output, reused, summary


def release_ids():
    ids = {}
    for kind, config in COMPONENTS.items():
        value = json.loads(Path(config['release']).read_text(encoding='utf-8'))
        ids[kind] = value['package_id']
    return ids


def finalize(work, output):
    """Move the three finished (zip, target.json) pairs into the candidate directory."""
    output.mkdir(parents=True, exist_ok=True)
    for kind in COMPONENTS:
        for path in sorted((work / kind).glob('orion-*')):
            destination = output / path.name
            if destination.exists():
                if build_cache.file_digest(destination) != build_cache.file_digest(path):
                    # A local, not yet published candidate of an earlier round: the newer build wins.
                    # publish_release refuses changed bytes under an already published sequence.
                    print(f'Replacing the earlier candidate {destination.name} (the contents changed)', flush=True)
                    os.replace(path, destination)
                continue
            os.replace(path, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, help='candidate directory (default .tmp/candidates/<package ids>)')
    parser.add_argument('--inputs', type=Path, default=ROOT / '.tmp/inputs')
    parser.add_argument('--cache', type=Path, default=ROOT / '.tmp/build-cache')
    parser.add_argument('--accept', choices=['full', 'fast', 'none'], default='full')
    parser.add_argument('--serial', action='store_true', help='one step at a time (also --jobs 1)')
    parser.add_argument('--jobs', type=int, default=3, help='concurrent steps (default 3)')
    parser.add_argument('--no-cache', action='store_true', help='ignore every cache: rebuild everything')
    parser.add_argument('--force-accept', action='store_true', help='run the full acceptance even if this set already passed')
    parser.add_argument('--no-prune', action='store_true', help='do not prune old acceptance sandboxes')
    parser.add_argument('--scratch-base', type=Path,
                        default=Path.home() / '.orion-lifecycle-tests')  # never inside a real %LOCALAPPDATA%/Orion
    args = parser.parse_args(argv)
    args.use_cache = not args.no_cache
    args.inputs, args.cache = args.inputs.resolve(), args.cache.resolve()
    jobs = 1 if args.serial else max(1, args.jobs)
    ids = release_ids()
    output = (args.output or ROOT / '.tmp/candidates' / '__'.join(ids[k] for k in ('hub', 'router', 'client'))).resolve()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    work = ROOT / '.tmp/build-work' / stamp
    log_dir = work / 'logs'
    log_dir.mkdir(parents=True)
    cache = build_cache.Cache(args.cache)
    timings = Timings('release build')
    print(f"Release round: {', '.join(ids.values())}\nOutput: {output}\n"
          f"Mode: {'serial' if jobs == 1 else f'parallel x{jobs}'}, cache {'ON' if args.use_cache else 'OFF'}, "
          f"acceptance {args.accept}\nLogs: {log_dir}", flush=True)
    results = {}
    try:
        with ThreadPoolExecutor(jobs) as pool:
            priority = Priority()
            flutter = pool.submit(run_web, 'flutter web', cache, timings, use_cache=args.use_cache, log_dir=log_dir,
                                  priority=priority)
            dashboard = pool.submit(run_web, 'router dashboard', cache, timings, use_cache=args.use_cache,
                                    log_dir=log_dir, priority=priority)
            futures = [pool.submit(run_package, 'hub', work, args, timings, log_dir, None, priority),
                       pool.submit(run_package, 'client', work, args, timings, log_dir, flutter),
                       pool.submit(run_package, 'router', work, args, timings, log_dir, dashboard, priority)]
            for future in [flutter, dashboard]:
                future.result()
            for future in futures:
                kind, _, reused, summary = future.result()
                results[kind] = reused
                print(f'--- package {kind}: ' + ('REUSED identical ZIP (inputs unchanged)' if reused else 'rebuilt'), flush=True)
                if summary.strip():
                    print('    ' + summary.strip().replace('\n', '\n    '), flush=True)
        finalize(work, output)
    except BaseException:
        timings.summary()
        raise
    finally:
        pass
    print(f"Candidates ready in {output}: " + ', '.join(f"{k}={'reused' if v else 'rebuilt'}" for k, v in sorted(results.items())),
          flush=True)
    code = 0
    if args.accept != 'none':
        argv = [sys.executable, str(HERE / 'accept_packages.py'), '--artifacts', str(output), '--inputs', str(args.inputs),
                '--scratch-base', str(args.scratch_base), '--cache', str(args.cache), '--mode', args.accept]
        if args.no_prune:
            argv.append('--no-prune')
        if not args.force_accept and args.accept == 'full':
            argv.append('--reuse-accepted')
        with timings.step(f'acceptance ({args.accept})'):
            code = subprocess.run(argv).returncode
    timings.summary()
    timings.write(work / 'timings.json')
    shutil.rmtree(work / 'runtime', ignore_errors=True)
    if code:
        raise SystemExit(code)


if __name__ == '__main__':
    main()
