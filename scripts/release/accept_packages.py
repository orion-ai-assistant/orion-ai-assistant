"""Actual candidate ZIP acceptance on a development Windows host, NOT a clean VM.

Ephemeral in-memory signing keys authorize only a local test repository. Every
product command uses bundled Python with an SDK-free PATH and isolated registry,
ports, AppData and data. Product/DB work is sequential. No GUI/logon is exercised.
"""
import argparse
from datetime import datetime, timedelta, timezone
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

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from securesystemslib.signer import CryptoSigner
from tuf.api.exceptions import DownloadHTTPError
from tuf.api.metadata import Metadata, Root
from tuf.ngclient.fetcher import FetcherInterface
from services.shared.installation_registry import Registry
from services.shared.release_packages import ReleaseClient, sha256
from publish_metadata import publish
from install_new import install
from build_bootstrap import build as build_bootstrap
from services.shared.release_packages import extract_zip


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


def port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


def fingerprint(root):
    return {p.relative_to(root).as_posix(): sha256(p) for p in root.rglob('*') if p.is_file()}


def run(artifacts, base, inputs):
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='stage05-', dir=base))
    print('Package acceptance:', scratch, flush=True)
    report = {'success': False, 'trust': 'ephemeral-test-only', 'clean_machine': False,
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
               PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
    log = (scratch / 'product.log').open('wb')
    command_env = env
    try:
        signers = {role: [CryptoSigner.generate_ed25519()] for role in ('root', 'targets', 'snapshot', 'timestamp')}
        root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=1)))
        for role, keys in signers.items():
            for signer in keys:
                root.signed.add_key(signer.public_key, role)
        root.sign(signers['root'][0])
        bootstrap = scratch / 'TEST-ONLY-root.json'
        bootstrap.write_bytes(root.to_bytes())
        repository = publish(artifacts, scratch / 'repository', bootstrap, signers, version=1)
        installer_archive = build_bootstrap(bootstrap, inputs, scratch / 'TEST-ONLY-bootstrap.zip', test_only=True)
        installer = extract_zip(installer_archive, scratch / 'bootstrap')
        names = [json.loads(p.read_text())['path'] for p in Path(artifacts).glob('*.target.json')]
        if len(names) != 3:
            raise RuntimeError('Acceptance needs Hub, Router and Flutter candidates')
        client = ReleaseClient(trusted_root=bootstrap, cache=scratch / 'cache',
            metadata_url='https://fixture.invalid/metadata/', targets_url='https://fixture.invalid/targets/',
            fetcher=FixtureFetcher(repository))
        settings = {'hub': {'HUB_PORT': port(), 'ORION_PUBLIC_PORT': port(), 'ORION_LOCAL_PORT': port(),
            'ORION_MOBILE_PORT': port(), 'ORION_FLUTTER_PORT': port(), 'ORION_BROWSER_BRIDGE_PORT': port(),
            'POSTGRES_PORT': port(), 'REDIS_PORT': port(), 'POSTGRES_HOST': '127.0.0.1',
            'REDIS_HOST': '127.0.0.1', 'POSTGRES_DB': 'orion', 'POSTGRES_USER': 'orion',
            'POSTGRES_PASSWORD': 'orion', 'ORION_LOCAL_SHORTCUTS': '0'},
            'router': {'POSTGRES_PORT': port(), 'POSTGRES_DEV_PORT': port(), 'ORION_ROUTER_TLS_PORT': port(),
                       'ORION_ROUTER_LOCAL_HTTP_PORT': port(), 'ORION_ROUTER_MDNS': '0'}}
        (scratch / 'ports.json').write_text(json.dumps(settings, indent=2))
        config = {'root': str(scratch / 'Türkçe İıŞşĞğ 中文 Orion'), 'registry': str(registry),
                  'repository': str(repository), 'settings': settings, 'names': names, 'bootstrap': str(installer)}
        (scratch / 'fixture.json').write_text(json.dumps(config))
        fixture = scratch / 'fixture_install.py'
        fixture.write_text(
            'import json,sys\nfrom pathlib import Path\n'
            'from tuf.ngclient.fetcher import FetcherInterface\nfrom tuf.api.exceptions import DownloadHTTPError\n'
            'from services.shared.release_packages import ReleaseClient\nfrom scripts.release.install_new import install\n'
            + inspect.getsource(FixtureFetcher) + '\n'
            'v=json.loads(Path(sys.argv[1]).read_text()); p=Path(v["bootstrap"])\n'
            'client=ReleaseClient(trusted_root=p/"trusted-root.json", cache=p/"cache", '
            'metadata_url="https://fixture.invalid/metadata/", targets_url="https://fixture.invalid/targets/", '
            'fetcher=FixtureFetcher(Path(v["repository"])))\n'
            'install(client,v["names"],Path(v["root"]),Path(v["registry"]),settings=v["settings"])\n', encoding='utf-8')
        subprocess.run([str(installer / 'runtime/python/python.exe'), '-B', str(fixture), str(scratch / 'fixture.json')],
                       cwd=installer, env=env, stdout=log, stderr=log, check=True, timeout=300)
        record = Registry(registry).read()
        os.environ.clear()
        os.environ.update(env)
        report['checks'].append('real TUF verification + safe extraction + fresh Hub initdb in Unicode C AppData')
        items = {c['kind']: c for c in record['components']}
        model = Path(record['locations']['models']) / 'fixture-existing.gguf'
        model.write_bytes(b'existing model sentinel; never opened by any engine')
        model_hash = sha256(model)
        original = {kind: fingerprint(Path(item['code_path'])) for kind, item in items.items()}
        for kind, item in items.items():
            report['artifacts'].append(json.loads((Path(item['code_path']) / 'orion-release.json').read_text()))
        hub = Path(items['hub']['code_path'])
        python = hub / 'runtime/python/python.exe'
        router = Path(items['router']['code_path'])
        router_python = router / 'runtime/python/python.exe'
        command_env = dict(env)

        def call(argv, cwd, timeout=180, extra_env=None):
            subprocess.run(list(map(str, argv)), cwd=cwd,
                           env={**command_env, **{k: str(v) for k, v in (extra_env or {}).items()}},
                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == 'nt' else 0)

        # Standalone Router has no Hub dependency and no Next/Node at runtime.
        processes.append(('router', [router_python, '-m', 'core.lifecycle_cli', 'stop'], router))
        call([router_python, '-m', 'core.lifecycle_cli', 'start'], router)
        call([router_python, '-m', 'core.lifecycle_cli', 'stop'], router)
        router_identity = {p: sha256(p) for p in (Path(items['router']['data_path']) / 'persistent').rglob('*') if p.is_file()}
        report['checks'].append('standalone packaged Router start/health/stop with SDK-free PATH')
        # Existing launcher starts both components using their independent runtimes.
        processes.append(('hub', [python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub))
        entry = [Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe',
                 '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 registry.parent / 'bootstrap/Orion.ps1', '-Background', '-NonInteractive']
        call(entry, registry.parent)
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
        report['checks'].append('registered Hub/Router/Flutter package start and real Flutter health')
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
        call([python, '-c', probe], hub, extra_env=settings['hub'])
        data_before = {p: sha256(p) for p in (Path(items['hub']['data_path']) / 'files/security').glob('*')
                       if p.is_file() and (p.name in {'hub-key.pem', 'hub-cert.pem', 'hub-id', 'router-access-seed'}
                                           or p.name.startswith('router-access-'))}
        call([python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub)
        call(entry, registry.parent)
        call([python, '-c', "from services.shared.native_services import hub_database; from services.shared.environment import get_env; db=hub_database('.',get_env); assert db.ready(); assert db.sql('SELECT value FROM package_probe') == 'Türkçe 中文'; from orion.kernel.router_access import manual_state; assert manual_state('00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002')['key'] == 'sk-orion-stage05-fixture'"], hub, extra_env=settings['hub'])
        assert all(sha256(p) == digest for p, digest in data_before.items())
        call([python, '-m', 'services.shared.launcher', '--registry', registry, 'stop'], hub)
        assert all(sha256(p) == digest for p, digest in router_identity.items())
        assert sha256(model) == model_hash
        report['checks'].append('package stop/restart preserves real PostgreSQL row, TLS/UUID and data files')
        for kind, item in items.items():
            assert fingerprint(Path(item['code_path'])) == original[kind], 'Immutable package was modified: ' + kind
        report['checks'].append('every package file hash unchanged after runtime start/stop/restart')
        assert Registry(registry).read()['preferences']['automatic_updates'] is False
        assert Registry(registry).read()['preferences']['start_with_windows'] is False
        report['success'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        cleanup = []
        for kind, argv, cwd in reversed(processes):
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env=command_env,
                stdout=log, stderr=log, timeout=180)
            cleanup.append({'component': kind, 'stop_exit': result.returncode})
        report['cleanup'] = cleanup
        log.close()
        (scratch / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True), flush=True)
    return scratch


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--scratch-base', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    args = parser.parse_args()
    run(args.artifacts.resolve(), args.scratch_base, args.inputs.resolve())
