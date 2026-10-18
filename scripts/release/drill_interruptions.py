"""Interruption drill with real packages: an update cut off at its worst moments, then a normal relaunch.

Run on demand (not part of every release); it takes a few minutes. It installs an older accepted package set
in a fresh sandbox under the scratch base (never %LOCALAPPDATA%\\Orion), writes a real PostgreSQL row, publishes
a newer set in a local test-only TUF repository and then, per scenario:

  1. runs the real update helper (retained bootstrap runtime, real Hub/Router/PostgreSQL processes),
  2. hard-kills it at the chosen point - the helper's whole process tree and then every process running from
     the sandbox (power loss / Task Manager / a closed window),
  3. relaunches Orion through its registered entry (bootstrap/Orion.ps1 -Background) exactly like the user,
  4. asserts: Orion started; the update either completed (new version) or rolled back / did not apply (old
     version); never repair_required; the PostgreSQL row written before the update is still there; and a
     normal quit takes a few seconds.

Scenarios (--scenarios, default all):
  clean         not interrupted: the plain apply must complete (baseline)
  download      killed while the new packages are downloaded (prepare)
  backing_up    killed after the Hub data backup, before the other components' backups / the switch
  switching     killed right after the registry points at the new version, before its health check
  health_check  killed while the new version is being health-checked (its services are starting)
  admitting     killed after a passing health check, the new version still running but not yet admitted

Between scenarios the sandbox returns to the same staged state by copying back a snapshot (immutable package
folders are not copied). Example:

  python scripts/release/drill_interruptions.py --previous-artifacts .tmp/build-work/20261018-081717
      --update-artifacts .tmp/build-work/20261018-085633 --inputs .tmp/inputs
      --scratch-base C:/Users/<you>/.orion-lifecycle-tests
"""
import argparse
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
import psutil

from accept_packages import FixtureFetcher, run
import build_cache
from publish_metadata import publish
from services.shared.installation_registry import Registry
from services.shared.release_packages import is_layer_description
from timing import Timings

SCENARIOS = {
    'clean': {'action': 'resume', 'expect': {'completed'}},  # No interruption: the baseline apply.
    # pause: helper fault label (`#n` = its n-th occurrence) where the helper freezes and is then killed;
    # phase/delay: kill `delay` seconds after the journal reaches `phase` (long phases).
    'download': {'action': 'prepare', 'pause': 'after:journal:downloading#2', 'expect': {'failed'}},
    'backing_up': {'action': 'resume', 'pause': 'after:backup:hub', 'expect': {'completed', 'rolled_back'}},
    'switching': {'action': 'resume', 'pause': 'after:registry', 'expect': {'completed', 'rolled_back'}},
    'health_check': {'action': 'resume', 'phase': 'health_check', 'delay': 6, 'expect': {'completed', 'rolled_back'}},
    # Health passed, the new version keeps running, admission still closed: the last moment before it opens.
    'admitting': {'action': 'resume', 'pause': 'before:journal:completed', 'expect': {'completed', 'rolled_back'}},
}
PAUSE_TIMEOUT = 600
QUIT_BUDGET = 15  # seconds; a normal quit is expected to take a few


def flat_artifacts(source, destination):
    """build-work keeps one folder per component; publish() wants every target + ZIP in one folder."""
    source = Path(source)
    if any(source.glob('*.target.json')):
        return source
    destination.mkdir(parents=True)
    for description in source.glob('*/*.target.json'):
        build_cache.link_or_copy(description, destination / description.name)
        archive = description.parent / json.loads(description.read_text(encoding='utf-8'))['path']
        build_cache.link_or_copy(archive, destination / archive.name)
    return destination


FIXTURE = '''import json, os, sys, time
from pathlib import Path
from tuf.ngclient.fetcher import FetcherInterface
from tuf.api.exceptions import DownloadHTTPError
from services.shared.release_packages import ReleaseClient
from services.shared.update_helper import UpdateHelper
from services.shared.installation_registry import Registry
''' + inspect.getsource(FixtureFetcher) + '''
c = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
action = sys.argv[2]
label, _, nth = (os.environ.get('ORION_DRILL_PAUSE') or '').partition('#')
seen = {'n': 0}
def fault(current):
    if label and current == label:
        seen['n'] += 1
        if seen['n'] == int(nth or 1):
            Path(c['paused']).write_text(current, encoding='utf-8')
            time.sleep(3600)  # Frozen here until the drill kills the whole tree.
registry = Registry(c['registry'])
helper = UpdateHelper(registry, fault=fault)
if action == 'prepare':
    client = ReleaseClient(trusted_root=registry.path.parent / 'bootstrap/trusted-root.json',
        cache=registry.path.parent / 'drill-update-cache', metadata_url='https://fixture.invalid/metadata/',
        targets_url='https://fixture.invalid/targets/', fetcher=FixtureFetcher(Path(c['repository'])))
    tx = helper.prepare(client, c['names'])
else:
    tx = helper.resume()
Path(c['result']).write_text(json.dumps({'phase': tx['phase'], 'reason': tx.get('reason')}), encoding='utf-8')
'''


def journal(registry):
    base = Path(registry).parent / 'updates'
    try:
        identity = json.loads((base / 'active.json').read_text(encoding='utf-8'))['operation_id']
        return json.loads((base / identity / 'journal.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError):
        return None


def kill_everything(scratch, root_pid=None):
    """Power loss: the helper's tree first (Task Manager 'end process tree'), then whatever still runs
    from the sandbox (services, PostgreSQL, the watcher). Returns how many processes were killed."""
    victims = []
    if root_pid is not None:
        try:
            parent = psutil.Process(root_pid)
            victims += [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            pass
    marker = str(scratch).lower()
    me = os.getpid()
    for process in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            if process.pid == me:
                continue
            text = ' '.join([process.info['exe'] or '', *(process.info['cmdline'] or [])]).lower()
            if marker in text and process not in victims:
                victims.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in victims:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(victims, timeout=10)
    return len(victims)


def robocopy(source, destination, excluded, excluded_files=()):
    command = ['robocopy', str(source), str(destination), '/MIR', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS',
               '/NP', '/XJ']
    if excluded:
        command += ['/XD', *map(str, excluded)]
    if excluded_files:
        command += ['/XF', *map(str, excluded_files)]
    code = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if code >= 8:
        raise RuntimeError(f'robocopy failed ({code}): {source} -> {destination}')


class JournalReader:
    """Reads the journal every 50 ms like the "Orion güncelleniyor" window and the entry script do on a real
    PC (the sandbox runs without that window). Without it the drill missed a PermissionError rollback."""
    def __init__(self, registry):
        import threading
        self.registry, self.stop = Path(registry), threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(.05):
            base = self.registry.parent / 'updates'
            try:
                identity = json.loads((base / 'active.json').read_text(encoding='utf-8'))['operation_id']
                with (base / identity / 'journal.json').open('rb') as stream:
                    stream.read()
                    time.sleep(.01)
            except (OSError, ValueError, KeyError):
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(5)


def drill(update_artifacts, scenarios, timings, report):
    def callback(scratch, signers, trusted_root, installer, env):
        env = {**env, 'ORION_UPDATE_UI': 'off'}
        registry_path = scratch / 'install.json'
        registry = Registry(registry_path)
        settings = json.loads((scratch / 'ports.json').read_text(encoding='utf-8'))
        before = registry.read()
        installed = {c['kind']: json.loads((Path(c['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))
                     for c in before['components']}
        names = []
        for description in Path(update_artifacts).glob('*.target.json'):
            value = json.loads(description.read_text(encoding='utf-8'))
            if is_layer_description(value):
                continue
            current = installed.get(value['custom']['component'])
            if current and value['custom']['sequence'] > current['sequence']:
                names.append(value['path'])
        if not names:
            raise RuntimeError('The update set has nothing newer than the installed set')
        with timings.step('publish the newer set (test-only TUF, version 2)'):
            repository = publish(update_artifacts, scratch / 'update-repository', trusted_root, signers, version=2)
        config = {'registry': str(registry_path), 'repository': str(repository), 'names': names,
                  'paused': str(scratch / 'drill-paused.txt'), 'result': str(scratch / 'drill-result.json')}
        (scratch / 'drill.json').write_text(json.dumps(config), encoding='utf-8')
        (scratch / 'drill_fixture.py').write_text(FIXTURE, encoding='utf-8')
        entry = [Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe',
                 '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 scratch / 'bootstrap/Orion.ps1', '-Background', '-NonInteractive']
        log = (scratch / 'drill.log').open('ab')

        def call(argv, cwd, timeout=600, extra=None):
            started = time.monotonic()
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env={**env, **{k: str(v) for k, v in (extra or {}).items()}},
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=timeout,
                                    creationflags=0x08000000)
            return result.returncode, round(time.monotonic() - started, 1)

        def interrupted_run(spec):
            """Start the helper, kill everything at the chosen point. Returns what was cut."""
            Path(config['paused']).unlink(missing_ok=True)
            Path(config['result']).unlink(missing_ok=True)
            pause = spec.get('pause', '')
            if not pause and not spec.get('phase'):
                code, _ = call([installer / 'runtime/python/python.exe', '-B', scratch / 'drill_fixture.py',
                                scratch / 'drill.json', spec['action']], installer)
                outcome = Path(config['result']).read_text(encoding='utf-8') if Path(config['result']).exists() else 'no result'
                if code or json.loads(outcome)['phase'] not in spec['expect']:
                    raise RuntimeError('Uninterrupted apply did not complete: ' + outcome)
                return 'not interrupted', 0
            process = subprocess.Popen(
                [str(installer / 'runtime/python/python.exe'), '-B', str(scratch / 'drill_fixture.py'),
                 str(scratch / 'drill.json'), spec['action']], cwd=installer,
                env={**env, 'ORION_DRILL_PAUSE': pause}, stdout=log, stderr=log, creationflags=0x08000000)
            deadline = time.monotonic() + PAUSE_TIMEOUT
            reached = None
            while time.monotonic() < deadline:
                if pause and Path(config['paused']).exists():
                    reached = 'paused at ' + Path(config['paused']).read_text(encoding='utf-8')
                    break
                if spec.get('phase') and (journal(registry_path) or {}).get('phase') == spec['phase']:
                    time.sleep(spec['delay'])
                    reached = f"{spec['delay']} s into {spec['phase']}"
                    break
                if process.poll() is not None:
                    break
                time.sleep(.05)
            if reached is None:
                kill_everything(scratch, process.pid)
                raise RuntimeError('Interruption point never reached (helper ended or timed out): '
                                   + (Path(config['result']).read_text(encoding='utf-8') if Path(config['result']).exists() else 'no result'))
            killed = kill_everything(scratch, process.pid)
            return reached, killed

        def hub_python():
            hub = next(c for c in registry.read()['components'] if c['kind'] == 'hub')
            return Path(hub['code_path']), Path(hub['code_path']) / 'runtime/python/python.exe'

        def verify_and_quit(name, spec, row):
            code, relaunch = call(entry, scratch)
            if code:
                raise AssertionError(f'{name}: Orion did not start after the interruption (see launcher.log)')
            tx = journal(registry_path) or {}
            phase = tx.get('phase')
            if phase not in spec['expect']:
                raise AssertionError(f'{name}: update ended in {phase!r} ({tx.get("reason")}), expected {sorted(spec["expect"])}')
            hub_root, python = hub_python()
            version = json.loads((hub_root / 'orion-release.json').read_text(encoding='utf-8'))['version']
            code, _ = call([python, '-c', "from services.shared.native_services import hub_database; "
                            "from services.shared.environment import get_env; import sys; db=hub_database('.',get_env); "
                            "assert db.ready(); assert db.sql(\"SELECT value FROM drill_probe WHERE id=1\") == sys.argv[1]", row],
                           hub_root, extra=settings['hub'])
            if code:
                raise AssertionError(f'{name}: the PostgreSQL row written before the update is missing')
            code, quit_seconds = call([python, '-m', 'services.shared.launcher', '--registry', registry_path, 'stop'], hub_root)
            if code:
                raise AssertionError(f'{name}: quit failed')
            left = kill_everything(scratch)
            return {'outcome': phase, 'reason': tx.get('reason'), 'hub_version': version,
                    'relaunch_seconds': relaunch, 'quit_seconds': quit_seconds, 'left_after_quit': left}

        def write_row(value):
            hub_root, python = hub_python()
            code, _ = call(entry, scratch)
            assert code == 0, 'Orion did not start before the drill'
            program = ("from services.shared.native_services import hub_database; from services.shared.environment import get_env; "
                       "import sys; db=hub_database('.',get_env); assert db.ready(); "
                       "db.sql('CREATE TABLE IF NOT EXISTS drill_probe(id int primary key, value text)'); "
                       "db.sql(\"INSERT INTO drill_probe VALUES (1, '\" + sys.argv[1] + \"') ON CONFLICT (id) DO UPDATE SET value=excluded.value\")")
            assert call([python, '-c', program, value], hub_root, extra=settings['hub'])[0] == 0
            assert call([python, '-m', 'services.shared.launcher', '--registry', registry_path, 'stop'], hub_root)[0] == 0
            kill_everything(scratch)

        snapshot = scratch.parent / (scratch.name + '-snapshot')
        def immutable():
            # Immutable or disposable: package code, runtimes, test repositories and download caches.
            folders = {scratch / 'bootstrap/runtime', scratch / 'bootstrap/cache', scratch / 'repository',
                       scratch / 'update-repository', scratch / 'cache', scratch / 'drill-update-cache',
                       scratch / 'artifacts-subset'}
            for record in filter(None, [registry.read(), (journal(registry_path) or {}).get('proposed')]):
                folders |= {Path(c['code_path']).parent for c in record['components']}
            return sorted(folders)

        try:
            for name in [s for s in SCENARIOS if s in scenarios]:
                spec = SCENARIOS[name]
                row = f'drill {name} Türkçe 中文'
                with timings.step('scenario ' + name):
                    if spec['action'] == 'resume':
                        if not snapshot.exists():
                            with timings.step('stage the update once (download + verify) and snapshot it'):
                                Path(config['paused']).unlink(missing_ok=True)
                                code, _ = call([installer / 'runtime/python/python.exe', '-B', scratch / 'drill_fixture.py',
                                                scratch / 'drill.json', 'prepare'], installer)
                                assert code == 0 and (journal(registry_path) or {}).get('phase') == 'staged', 'staging failed'
                                robocopy(scratch, snapshot, immutable(), ['*.zip', 'drill.log', 'product.log'])
                        else:
                            with timings.step('restore the staged snapshot'):
                                kill_everything(scratch)
                                robocopy(snapshot, scratch, immutable(), ['*.zip', 'drill.log', 'product.log'])
                    write_row(row)
                    started = time.monotonic()
                    with JournalReader(registry_path):
                        reached, killed = interrupted_run(spec)
                    result = verify_and_quit(name, spec, row)
                    result.update(scenario=name, interrupted=reached, killed_processes=killed,
                                  seconds=round(time.monotonic() - started, 1))
                    report.append(result)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            log.close()
            kill_everything(scratch)
            shutil.rmtree(snapshot, ignore_errors=True)
    return callback


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--previous-artifacts', type=Path, required=True)
    parser.add_argument('--update-artifacts', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--scratch-base', type=Path, default=Path.home() / '.orion-lifecycle-tests')
    parser.add_argument('--scenarios', default=','.join(SCENARIOS))
    args = parser.parse_args()
    base = args.scratch_base.resolve()
    local = Path(os.environ.get('LOCALAPPDATA', '')).resolve() / 'Orion'
    if base == local or local in base.parents:
        parser.error('The drill never runs inside the real Orion installation folder')
    scenarios = [s.strip() for s in args.scenarios.split(',') if s.strip()]
    if not scenarios or set(scenarios) - set(SCENARIOS):
        parser.error('Scenarios: ' + ', '.join(SCENARIOS))
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='drill-inputs-', dir=base))
    previous = flat_artifacts(args.previous_artifacts.resolve(), work / 'previous')
    update = flat_artifacts(args.update_artifacts.resolve(), work / 'update')
    timings = Timings('interruption drill')
    report = []
    try:
        # Fast scope: everything installed, standalone-Router acceptance skipped; a private cache keeps the
        # release's own acceptance record untouched.
        run(previous, base, args.inputs.resolve(), after_acceptance=drill(update, scenarios, timings, report),
            mode='fast', kinds={'hub', 'client', 'router'}, changed={'hub', 'client'}, cache=work / 'cache',
            prune=False, timings=timings)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        timings.summary()
        print('\nInterruption drill:')
        for row in report:
            print(f"  {row['scenario']:<13} cut {row['interrupted']:<34} -> {row['outcome']:<11} Hub {row['hub_version']:<8}"
                  f" relaunch {row['relaunch_seconds']:>5}s  quit {row['quit_seconds']:>5}s  (total {row['seconds']}s)")
        slow = [row['scenario'] for row in report if row['quit_seconds'] > QUIT_BUDGET]
        if slow:
            print(f'  quit took longer than {QUIT_BUDGET}s in: ' + ', '.join(slow))
    if len(report) != len(scenarios):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
