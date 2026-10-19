"""Actual candidate ZIP acceptance on a development Windows host, NOT a clean VM.

Ephemeral in-memory signing keys authorize only a local test repository. Every
product command uses bundled Python with an SDK-free PATH and isolated registry,
ports, AppData and data. Product/DB work is sequential. No GUI/logon is exercised.

Two modes (the output names the one that ran):
  --mode full  (default) installs and exercises the exact Hub+Router+Client set. Only this
               result is accepted by publish_release.py.
  --mode fast  a pre-check for iteration: installs and exercises only the components whose
               ZIP differs from the last passing full run (plus the peers they integrate
               with). Never publishable: its result.json says `mode: fast, success: false`.

Old sandboxes under the scratch base are pruned automatically (newest N, the last passing
full run and anything holding a PINNED file are kept); `--prune` runs that rule by hand.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import inspect
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from securesystemslib.signer import CryptoSigner
from tuf.api.exceptions import DownloadHTTPError
from tuf.api.metadata import Metadata, Root
from tuf.ngclient.fetcher import FetcherInterface
from services.shared.installation_registry import Registry
from services.shared.release_packages import ReleaseClient, is_layer_description, sha256
from publish_metadata import publish
from install_new import install
from build_bootstrap import build as build_bootstrap
from services.shared.release_packages import extract_zip
import build_cache
import sandbox_prune
from timing import Timings

DEFAULT_CACHE = ROOT / '.tmp/build-cache'
DEFAULT_KEEP = 3


class FixtureFetcher(FetcherInterface):
    def __init__(self, root):
        self.root = root

    def _fetch(self, url):
        path = self.root / url.split('https://fixture.invalid/', 1)[1]
        if not path.is_file():
            raise DownloadHTTPError('Fixture missing', 404)
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                yield block


LOOPBACK = {'ORION_PUBLIC_BIND_HOST': '127.0.0.1', 'ORION_MOBILE_BIND_HOST': '127.0.0.1',
            'ORION_ROUTER_TLS_HOST': '127.0.0.1'}


def port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


def fingerprint(root):
    # Hashing is I/O bound (virus scanner latency per file): a thread pool overlaps it.
    files = [p for p in Path(root).rglob('*') if p.is_file()]
    with ThreadPoolExecutor(8) as pool:
        digests = list(pool.map(sha256, files))
    return {p.relative_to(root).as_posix(): digest for p, digest in zip(files, digests)}


def zip_contents(archive, digest):
    """{member name: sha256} of a candidate ZIP whose own sha256 must be `digest` (its signed target).

    This is the immutability baseline: the installed code tree must equal the signed bytes, file by
    file, before and after every start. Hashing from the archive needs no installed file, so it runs
    while the installer works."""
    if sha256(archive) != digest:
        raise RuntimeError(f'Candidate changed after its target was written: {archive}')
    with zipfile.ZipFile(archive) as source:
        return {item.filename: hashlib.sha256(source.read(item)).hexdigest()
                for item in source.infolist() if not item.is_dir()}


def signed_contents(artifacts, description, digest):
    """zip_contents of a candidate package, plus those of its runtime layer for a split package: the installer
    extracts both into one tree."""
    value = json.loads(Path(description).read_text(encoding='utf-8'))
    contents = zip_contents(Path(artifacts) / value['path'], digest)
    if 'runtime' in value:
        layer = zip_contents(Path(artifacts) / value['runtime']['target'], value['runtime']['sha256'])
        if set(layer) & set(contents):
            raise RuntimeError('Package and runtime layer overlap: ' + value['path'])
        contents.update(layer)
    return contents


def artifact_set(artifacts):
    """{component: (sha256, package_id, target json path)} of a candidate directory (runtime layers are not
    components: each code package's ZIP pins its layer, so its sha256 covers the layer too)."""
    found = {}
    for path in sorted(Path(artifacts).glob('*.target.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        if is_layer_description(value):
            continue
        found[value['custom']['component']] = (value['hashes']['sha256'], value['custom']['package_id'], path)
    return found


def package_names(artifacts):
    """Target names to install from a candidate directory: the product packages, never their layers."""
    return [value['path'] for value in (json.loads(p.read_text(encoding='utf-8'))
            for p in sorted(Path(artifacts).glob('*.target.json'))) if not is_layer_description(value)]


def acceptance_tooling_key():
    """Everything besides the packages that decides what an acceptance run proves."""
    names = build_cache.git_files(ROOT, ['services/shared', 'scripts/release', 'installer/app'])
    names = [n for n in names if not Path(n).name.startswith('test_') and Path(n).name not in {'conftest.py', 'README.md'}]
    return build_cache.hash_files({n: ROOT / n for n in names})


def fast_scope(changed):
    """Components a fast run installs: the changed ones and what they need to run.

    Hub and Client start only together (Flutter health), so either pulls in both. The Router is
    installed only when it changed: an unchanged Router was fully accepted before, and the
    Hub-to-Router integration check belongs to the full run (or to a Router change)."""
    kinds = set()
    if 'router' in changed:
        kinds.add('router')
    if changed & {'hub', 'client'}:
        kinds |= {'hub', 'client'}
    return kinds


def last_full_acceptance(cache):
    record = build_cache.Cache(cache).read_record('acceptance.json')
    if not record or record.get('mode') != 'full':
        return None
    result = Path(record.get('result', ''))
    try:
        if json.loads(result.read_text(encoding='utf-8')).get('success') is not True:
            return None
    except (OSError, ValueError):
        return None
    return record


def plan(artifacts, mode, cache, *, reuse_accepted=False):
    """Decide what to run: ('skip', record) | ('full', None) | ('fast', changed kinds)."""
    current = artifact_set(artifacts)
    record = last_full_acceptance(cache)
    packages = {kind: item[0] for kind, item in current.items()}
    if record and reuse_accepted and mode == 'full' and record['packages'] == packages \
            and record['tooling'] == acceptance_tooling_key():
        return 'skip', record
    if mode == 'fast':
        if not record or set(record['packages']) != set(packages):
            return 'full', None  # nothing to compare against: the first run has to be the whole set
        changed = {kind for kind, digest in packages.items() if record['packages'].get(kind) != digest}
        if not changed:
            return 'fast', set()
        return 'fast', changed
    return 'full', None


def run(artifacts, base, inputs, *, after_acceptance=None, mode='full', kinds=None, changed=None, cache=None,
        keep=DEFAULT_KEEP, prune=True, timings=None, client_ready=None):
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='stage05-', dir=base))
    label = 'FULL acceptance (publishable)' if mode == 'full' else 'FAST pre-check (NOT publishable)'
    print(f'Package acceptance [{label}]:', scratch, flush=True)
    T = timings or Timings('acceptance ' + mode)
    # Needs git and the normal PATH: compute before the environment is replaced by the sandbox's.
    tooling = acceptance_tooling_key() if mode == 'full' else None
    if prune and os.environ.get('ORION_ACCEPT_PRUNE', '1') != '0':
        sandbox_prune.prune_in_background(base, keep=keep, protect=[scratch], cache=cache or DEFAULT_CACHE)
    report = {'success': False, 'mode': mode, 'trust': 'ephemeral-test-only', 'clean_machine': False,
              'checks': [], 'artifacts': []}
    registry = scratch / 'install.json'
    processes = []
    # This runner only ever starts commands inside its fresh isolated installation.
    env = os.environ.copy()
    for name in ('LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP'):
        env[name] = str(scratch / name)
        Path(env[name]).mkdir()
    env.update(ORION_INSTALLATION_REGISTRY=str(registry), ORION_SKIP_GLOBAL_REGISTRATION='1',
               ORION_FLUTTER_OPEN_WINDOW='0', ORION_ROUTER_MDNS='0', ORION_HUB_MDNS='0',
               # Loopback only, as run_product_acceptance does: a LAN listener of each new scratch python.exe
               # made Windows Firewall ask "allow Python?" twice per release (Hub's and Router's runtime).
               **LOOPBACK, PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
    log = (scratch / 'product.log').open('wb')
    command_env = env
    try:
        available = artifact_set(artifacts)
        later = {'client'} if client_ready is not None else set()
        if mode == 'full':
            # With `client_ready` the Client package is still being built: Hub+Router go first, a Client
            # candidate of an earlier round in the same directory is ignored until the new one is ready.
            kinds = set(available) - later
            if kinds | later != {'hub', 'router', 'client'}:
                raise RuntimeError('Acceptance needs Hub, Router and Flutter candidates')
            first = {kind: available[kind][0] for kind in kinds}
        else:
            kinds = set(kinds or ())
            if not kinds or not kinds <= set(available):
                raise RuntimeError('Fast acceptance needs at least one candidate component')
        report['scope'] = sorted(kinds | later)
        report['install'] = ('Hub+Router fresh install, then the Client through the add path (TUF metadata v2) '
                             'while it was still being built' if later else
                             'one fresh install of ' + '+'.join(sorted(kinds)))
        signers = {role: [CryptoSigner.generate_ed25519()] for role in ('root', 'targets', 'snapshot', 'timestamp')}
        root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=1)))
        for role, keys in signers.items():
            for signer in keys:
                root.signed.add_key(signer.public_key, role)
        root.sign(signers['root'][0])
        bootstrap = scratch / 'TEST-ONLY-root.json'
        bootstrap.write_bytes(root.to_bytes())
        with T.step('test TUF repository + bootstrap bundle'):
            # Fast mode signs only the components under test.
            subset = Path(artifacts)
            if kinds != set(available):
                subset = scratch / 'artifacts-subset'
                subset.mkdir()
                for kind in kinds:
                    target = available[kind][2]
                    value = json.loads(target.read_text())
                    build_cache.link_or_copy(Path(artifacts) / value['path'], subset / value['path'])
                    build_cache.link_or_copy(target, subset / target.name)
                    if 'runtime' in value:  # its runtime layer
                        layer = Path(artifacts) / value['runtime']['target']
                        build_cache.link_or_copy(layer, subset / layer.name)
                        build_cache.link_or_copy(layer.with_suffix('.target.json'), subset / (layer.stem + '.target.json'))
            repository = publish(subset, scratch / 'repository', bootstrap, signers, version=1)
            installer_archive = build_bootstrap(bootstrap, inputs, scratch / 'TEST-ONLY-bootstrap.zip', test_only=True)
            installer = extract_zip(installer_archive, scratch / 'bootstrap')
        names = package_names(subset)
        hashing = ThreadPoolExecutor(len(kinds))
        signed = {kind: hashing.submit(signed_contents, artifacts, available[kind][2], available[kind][0]) for kind in kinds}
        hashing.shutdown(wait=False)
        client = ReleaseClient(trusted_root=bootstrap, cache=scratch / 'cache',
            metadata_url='https://fixture.invalid/metadata/', targets_url='https://fixture.invalid/targets/',
            fetcher=FixtureFetcher(repository))
        settings = {'hub': {'HUB_PORT': port(), 'ORION_PUBLIC_PORT': port(), 'ORION_LOCAL_PORT': port(),
            'ORION_MOBILE_PORT': port(), 'ORION_FLUTTER_PORT': port(), 'ORION_BROWSER_BRIDGE_PORT': port(),
            'POSTGRES_PORT': port(), 'REDIS_PORT': port(), 'POSTGRES_HOST': '127.0.0.1',
            'REDIS_HOST': '127.0.0.1', 'POSTGRES_DB': 'orion', 'POSTGRES_USER': 'orion',
            'POSTGRES_PASSWORD': 'orion', 'ORION_LOCAL_SHORTCUTS': '0', **LOOPBACK},
            'router': {'POSTGRES_PORT': port(), 'POSTGRES_DEV_PORT': port(), 'ORION_ROUTER_TLS_PORT': port(),
                       'ORION_ROUTER_LOCAL_HTTP_PORT': port(), 'ORION_ROUTER_MDNS': '0', **LOOPBACK}}
        (scratch / 'ports.json').write_text(json.dumps(settings, indent=2))
        config = {'root': str(scratch / 'Türkçe İıŞşĞğ 中文 Orion'), 'registry': str(registry),
                  'repository': str(repository), 'settings': settings, 'names': names, 'bootstrap': str(installer)}
        (scratch / 'fixture.json').write_text(json.dumps(config))
        fixture = scratch / 'fixture_install.py'
        fixture.write_text(
            'import json,sys\nfrom pathlib import Path\n'
            'from tuf.ngclient.fetcher import FetcherInterface\nfrom tuf.api.exceptions import DownloadHTTPError\n'
            'from services.shared.release_packages import ReleaseClient\nfrom scripts.release.install_new import install\n'
            'from services.shared.component_setup import add_components\n'
            + inspect.getsource(FixtureFetcher) + '\n'
            'v=json.loads(Path(sys.argv[1]).read_text()); p=Path(v["bootstrap"])\n'
            'client=ReleaseClient(trusted_root=p/"trusted-root.json", cache=p/"cache", '
            'metadata_url="https://fixture.invalid/metadata/", targets_url="https://fixture.invalid/targets/", '
            'fetcher=FixtureFetcher(Path(v["repository"])))\n'
            '(add_components if v.get("add") else install)(client,v["names"],Path(v["root"]),Path(v["registry"]),'
            'settings=v["settings"])\n', encoding='utf-8')
        with T.step('install: TUF verification + safe extraction' + (' + Hub initdb ' if 'hub' in kinds else ' ') + '/'.join(sorted(kinds))):
            subprocess.run([str(installer / 'runtime/python/python.exe'), '-B', str(fixture), str(scratch / 'fixture.json')],
                           cwd=installer, env=env, stdout=log, stderr=log, check=True, timeout=300)
        record = Registry(registry).read()
        os.environ.clear()
        os.environ.update(env)
        report['checks'].append('real TUF verification + safe extraction' + (
            ' + fresh Hub initdb in Unicode C AppData' if 'hub' in kinds else ' in Unicode C AppData'))
        items = {c['kind']: c for c in record['components']}
        model = Path(record['locations']['models']) / 'fixture-existing.gguf'
        model.write_bytes(b'existing model sentinel; never opened by any engine')
        model_hash = sha256(model)
        with T.step('installed inventory == signed ZIP contents (baseline, hashed during install)'):
            # The installer's own sha256 inventory of every installed file must be exactly the signed ZIP's
            # members; the independent re-hash after start/stop/restart (below) is compared with the same
            # signed contents, so no separate pre-start hash pass of the installed tree is needed.
            original = {kind: signed[kind].result() for kind in items}
            for kind, item in items.items():
                inventory = json.loads((Path(item['state_path']) / 'installed-package.json').read_text(encoding='utf-8'))
                assert inventory['files'] == original[kind], 'Installed inventory differs from the signed ZIP: ' + kind
        for kind, item in items.items():
            report['artifacts'].append(json.loads((Path(item['code_path']) / 'orion-release.json').read_text()))
        hub = Path(items['hub']['code_path']) if 'hub' in items else None
        python = hub / 'runtime/python/python.exe' if hub else None
        router = Path(items['router']['code_path']) if 'router' in items else None
        router_python = router / 'runtime/python/python.exe' if router else None
        command_env = dict(env)

        def call(argv, cwd, timeout=180, extra_env=None):
            subprocess.run(list(map(str, argv)), cwd=cwd,
                           env={**command_env, **{k: str(v) for k, v in (extra_env or {}).items()}},
                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == 'nt' else 0)

        router_identity = {}
        # A fast run exercises Router standalone only when the Router itself changed.
        standalone_router = router is not None and (mode == 'full' or 'router' in (changed or kinds))
        if standalone_router:
            with T.step('standalone Router start/health/stop'):
                # Standalone Router has no Hub dependency and no Next/Node at runtime.
                processes.append(('router', [router_python, '-m', 'core.lifecycle_cli', 'stop'], router))
                call([router_python, '-m', 'core.lifecycle_cli', 'start'], router)
                call([router_python, '-m', 'core.lifecycle_cli', 'stop'], router)
                router_identity = {p: sha256(p) for p in (Path(items['router']['data_path']) / 'persistent').rglob('*') if p.is_file()}
            report['checks'].append('standalone packaged Router start/health/stop with SDK-free PATH')
        if later:
            with T.step('wait for the Client package (Flutter build)'):
                client_ready()
            available = artifact_set(artifacts)
            if set(available) != {'hub', 'router', 'client'} or any(available[k][0] != d for k, d in first.items()):
                raise RuntimeError('Candidate set changed while the acceptance ran')
            with T.step('add Client: TUF metadata v2 + verification + safe extraction (add path)'):
                archive = Path(artifacts) / json.loads(available['client'][2].read_text())['path']
                hashing = ThreadPoolExecutor(1)
                signed['client'] = hashing.submit(signed_contents, artifacts, available['client'][2], available['client'][0])
                hashing.shutdown(wait=False)
                # A newer signed version of the same test repository (same root and keys), listing all three.
                repository = publish(Path(artifacts), scratch / 'repository-2', bootstrap, signers, version=2)
                config.update(repository=str(repository), names=[archive.name], add=True)
                (scratch / 'fixture-add.json').write_text(json.dumps(config))
                subprocess.run([str(installer / 'runtime/python/python.exe'), '-B', str(fixture),
                                str(scratch / 'fixture-add.json')],
                               cwd=installer, env=env, stdout=log, stderr=log, check=True, timeout=300)
                items = {c['kind']: c for c in Registry(registry).read()['components']}
                original['client'] = signed['client'].result()
                inventory = json.loads((Path(items['client']['state_path']) / 'installed-package.json').read_text(encoding='utf-8'))
                assert inventory['files'] == original['client'], 'Installed inventory differs from the signed ZIP: client'
            report['artifacts'].append(json.loads((Path(items['client']['code_path']) / 'orion-release.json').read_text()))
            report['checks'].append('Client added to the Hub+Router installation through the add path '
                                    '(real TUF verification of metadata v2 + safe extraction)')
        if hub is not None:
            # Existing launcher starts both components using their independent runtimes.
            processes.append(('hub', [python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub))
            entry = [Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe',
                     '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                     registry.parent / 'bootstrap/Orion.ps1', '-Background', '-NonInteractive']
            with T.step('registered launcher start + Flutter health'):
                call(entry, registry.parent)
                if 'client' in items:
                    url = 'http://127.0.0.1:' + str(settings['hub']['ORION_FLUTTER_PORT'])
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            with opener.open(url + '/__orion_flutter_health__', timeout=2) as response:
                                assert response.read() == b'orion-flutter-web'
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.2)
            report['checks'].append('registered Hub/Router/Flutter package start and real Flutter health'
                                    if len(items) == 3 else 'registered ' + '/'.join(sorted(items)) + ' package start and health')
            if router is not None:
                with T.step('Router provenance + TLS identity bootstrap'):
                    call([python, '-c',
                        "import asyncio; from orion.kernel.router_bootstrap import bootstrap_local_router; "
                        "r=asyncio.run(bootstrap_local_router()); assert r['status'] in {'trusted','owner-configured'},r"], hub)
                report['checks'].append('registered packaged Router public provenance and live TLS identity bootstrap')
            # Write an independent DB row and an encrypted per-account override in data;
            # next process proves persistence without any external provider/model call.
            probe = (
                "from services.shared.native_services import hub_database; from services.shared.environment import get_env; "
                "from orion.kernel.local_security import tls_identity,security_dir; "
                "db=hub_database('.',get_env); assert db.ready(); db.sql('CREATE TABLE package_probe(value text)'); "
                "db.sql(\"INSERT INTO package_probe VALUES ('Türkçe 中文')\"); "
                "i=tls_identity(); print('identity',i['id']); "
                "from orion.kernel.router_access import save_manual; "
                "from orion.kernel.router_accounts import _material; "
                "_material(); save_manual('00000000-0000-4000-8000-000000000001', "
                "'00000000-0000-4000-8000-000000000002',key='sk-orion-stage05-fixture',enabled=True)"
            )
            with T.step('write DB row + encrypted override, stop, restart, verify persistence'):
                call([python, '-c', probe], hub, extra_env=settings['hub'])
                data_before = {p: sha256(p) for p in (Path(items['hub']['data_path']) / 'files/security').glob('*')
                               if p.is_file() and (p.name in {'hub-key.pem', 'hub-cert.pem', 'hub-id', 'router-access-seed'}
                                                   or p.name.startswith('router-access-'))}
                call([python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub)
                call(entry, registry.parent)
                call([python, '-c', "from services.shared.native_services import hub_database; from services.shared.environment import get_env; db=hub_database('.',get_env); assert db.ready(); assert db.sql('SELECT value FROM package_probe') == 'Türkçe 中文'; from orion.kernel.router_access import manual_state; assert manual_state('00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002')['key'] == 'sk-orion-stage05-fixture'"], hub, extra_env=settings['hub'])
                assert all(sha256(p) == digest for p, digest in data_before.items())
            with T.step('final stop'):
                call([python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub)
        assert all(sha256(p) == digest for p, digest in router_identity.items())
        assert sha256(model) == model_hash
        if hub is not None:
            report['checks'].append('package stop/restart preserves real PostgreSQL row, TLS/UUID and data files')
        with T.step('fingerprint installed package files again (immutability)'):
            for kind, item in items.items():
                assert fingerprint(Path(item['code_path'])) == original[kind], 'Immutable package was modified: ' + kind
        report['checks'].append('every installed file hash equals the signed ZIP after runtime start/stop/restart')
        assert Registry(registry).read()['preferences']['automatic_updates'] is False
        assert Registry(registry).read()['preferences']['start_with_windows'] is False
        if after_acceptance is not None:
            after_acceptance(scratch, signers, bootstrap, installer, env)
        if mode == 'full':
            # The exact set this pass proves; publish_release and the build cache bind to these hashes.
            report['packages'] = {kind: item[0] for kind, item in artifact_set(artifacts).items()}
            report['success'] = True
        else:
            # A fast pass must never satisfy the publish gate (`success` stays False).
            report['fast_success'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        cleanup = []
        for kind, argv, cwd in reversed(processes):
            # A Stage 06 callback can have selected a different immutable Hub.
            # Cleanup must use that registered runtime, never the old code path.
            if kind == 'hub' and after_acceptance is not None:
                current = Registry(registry).read()
                active = next(c for c in current['components'] if c['kind'] == 'hub')
                cwd = Path(active['code_path'])
                argv = [cwd / 'runtime/python/python.exe', '-m', 'services.shared.launcher',
                        '--registry', registry, 'stop']
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env=command_env,
                stdout=log, stderr=log, timeout=180)
            cleanup.append({'component': kind, 'stop_exit': result.returncode})
        report['cleanup'] = cleanup
        log.close()
        report['timings'] = T.rows
        (scratch / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True), flush=True)
    if mode == 'full' and report['success']:
        record_full_acceptance(cache or DEFAULT_CACHE, artifacts, scratch, tooling)
    return scratch


class AlreadyAccepted(Exception):
    def __init__(self, record):
        super().__init__('this exact package set already passed a full acceptance')
        self.record = record


def skipped(record):
    print(f"FULL acceptance SKIPPED: this exact package set already passed a full run with the same acceptance "
          f"tooling ({record['finished']}).\nResult: {record['result']}  (use without --reuse-accepted to run again)")


def wait_for(marker, timeout=1800):
    """A callable that blocks until `marker` reads 'ready'; anything else (a failed build) aborts the run."""
    def ready():
        deadline = time.monotonic() + timeout
        while not marker.is_file():
            if time.monotonic() >= deadline:
                raise RuntimeError('The Client package was not ready in time')
            time.sleep(.25)
        state = marker.read_text(encoding='utf-8').strip()
        if state != 'ready':
            raise RuntimeError('The Client package build did not finish: ' + state)
    return ready


def record_full_acceptance(cache, artifacts, scratch, tooling):
    packages = {kind: item[0] for kind, item in artifact_set(artifacts).items()}
    build_cache.Cache(cache).write_record('acceptance.json', {
        'mode': 'full', 'packages': packages, 'tooling': tooling,
        'result': str(Path(scratch) / 'result.json'), 'finished': datetime.now(timezone.utc).isoformat()})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--scratch-base', type=Path, required=True)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--mode', choices=['full', 'fast'], default='full')
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE, help='build cache (records the last full acceptance)')
    parser.add_argument('--reuse-accepted', action='store_true',
                        help='skip a full run when this exact package set already passed with unchanged acceptance tooling')
    parser.add_argument('--keep', type=int, default=DEFAULT_KEEP, help='sandboxes to keep when pruning')
    parser.add_argument('--no-prune', action='store_true', help='do not prune old sandboxes after starting')
    parser.add_argument('--client-wait', type=Path, metavar='MARKER',
                        help='full mode: start with the Hub and Router candidates while the Client is still being '
                             'built; add the Client once MARKER says "ready" (build_release.py writes it)')
    parser.add_argument('--prune', action='store_true', help='only prune old sandboxes, then exit')
    parser.add_argument('--dry-run', action='store_true', help='with --prune: list what would be deleted')
    parser.add_argument('--all-prefixes', action='store_true',
                        help='with --prune: apply the keep rule to every sandbox family (product-*, stage04-*, ...)')
    args = parser.parse_args()
    if args.prune:
        sandbox_prune.main(args.scratch_base, keep=args.keep, cache=args.cache, dry_run=args.dry_run,
                           all_prefixes=args.all_prefixes)
        return
    if args.artifacts is None or args.inputs is None:
        parser.error('--artifacts and --inputs are required unless --prune')
    artifacts, inputs = args.artifacts.resolve(), args.inputs.resolve()
    overlapped = args.client_wait is not None and args.mode == 'full'
    # Overlapped: the directory may still hold an earlier round's Client, so whether this exact set already
    # passed can only be decided once the new Client package is there (see `client_ready` below).
    decision, detail = ('full', None) if overlapped else plan(artifacts, args.mode, args.cache,
                                                             reuse_accepted=args.reuse_accepted)
    if decision == 'skip':
        skipped(detail)
        return
    if args.mode == 'fast' and decision == 'full':
        print('FAST requested but there is no earlier passing full acceptance to compare with: running FULL.')
    if decision == 'fast':
        if not detail:
            print('FAST pre-check: every package is identical to the last passing full run; nothing to exercise.')
            return
        print('FAST pre-check, components changed since the last full run:', ', '.join(sorted(detail)),
              '\nThis is NOT the publish gate: run again without --mode fast for the full acceptance.')
        mode, kinds = 'fast', fast_scope(detail)
    else:
        mode, kinds = 'full', None
    timings = Timings('acceptance ' + mode)
    client_ready = None
    if overlapped:
        wait = wait_for(args.client_wait)

        def client_ready():
            wait()
            decision, record = plan(artifacts, 'full', args.cache, reuse_accepted=args.reuse_accepted)
            if decision == 'skip':
                raise AlreadyAccepted(record)
    try:
        scratch = run(artifacts, args.scratch_base, inputs, mode=mode, kinds=kinds, changed=detail or None, cache=args.cache,
                      keep=args.keep, prune=not args.no_prune, timings=timings, client_ready=client_ready)
    except AlreadyAccepted as already:
        skipped(already.record)  # the Flutter build produced the already accepted Client again
        return
    finally:
        timings.summary()
    print(('FULL acceptance PASSED (publishable): ' if mode == 'full' else 'FAST pre-check PASSED (not publishable): ')
          + str(scratch / 'result.json'))


if __name__ == '__main__':
    main()
