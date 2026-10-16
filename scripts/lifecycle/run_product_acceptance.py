"""Real native applications in disposable source/data roots; no live settings.

Requires the already installed Windows runtimes. Never installs dependencies or
copies .env, persistent data, browser profiles, or credentials from a checkout.
All artifacts (including failed runs) are retained for inspection.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.native_postgres import Postgres
from services.shared.process_lifecycle import Runtime, living, child_tree_alive, stop_component


GUARD = '''import ipaddress, socket, os
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_bind = socket.socket.bind
def allowed(address):
    if not isinstance(address, tuple): return
    host = address[0]
    if host == 'localhost': return
    try: safe = ipaddress.ip_address(host).is_loopback
    except ValueError: safe = False
    if not safe:
        with open(os.environ['ORION_TEST_EGRESS_LOG'], 'a') as log:
            log.write(repr(address) + '\\n')
        raise OSError('Product acceptance permits loopback sockets only')
def connect(self, address):
    allowed(address)
    return _connect(self, address)
def connect_ex(self, address):
    allowed(address)
    return _connect_ex(self, address)
def bind(self, address):
    allowed(address)
    return _bind(self, address)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.bind = bind
'''


def port():
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        return channel.getsockname()[1]


def copy_source(source, destination):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        '__pycache__', '.venv', '*.pyc', '.env', '*.log', '*.pid',
        'node_modules', '.next', 'out', '.git'))


def runtime(source, destination):
    destination.mkdir(parents=True)
    shutil.copy2(source / 'pyvenv.cfg', destination / 'pyvenv.cfg')
    # Do not enable system-site .pth files, which may point to live source trees.
    cfg = (destination / 'pyvenv.cfg').read_text()
    cfg = cfg.replace('include-system-site-packages = true', 'include-system-site-packages = false')
    (destination / 'pyvenv.cfg').write_text(cfg)
    scripts = destination / 'Scripts'
    scripts.mkdir()
    shutil.copy2(source / 'Scripts/python.exe', scripts / 'python.exe')
    paths = json.loads(subprocess.check_output([str(source / 'Scripts/python.exe'), '-c',
        'import sys,json; print(json.dumps([p for p in sys.path if p.endswith("site-packages")]))'], text=True))
    packages = destination / 'Lib/site-packages'
    packages.mkdir(parents=True)
    (packages / 'dependencies.pth').write_text('\n'.join(paths) + '\n')
    (packages / 'sitecustomize.py').write_text(GUARD)
    return scripts / 'python.exe'


def binaries(source, target):
    # Independent directory entries, shared immutable binary contents. No data
    # directories are linked, and this runner never removes its scratch roots.
    if (source / 'bin/pg_ctl.exe').is_file():
        for directory in ('bin', 'lib', 'share'):
            shutil.copytree(source / directory, target / directory, copy_function=os.link)
    elif (source / 'bin/ffmpeg.exe').is_file():
        shutil.copytree(source / 'bin', target / 'bin', copy_function=os.link)
    else:
        target.mkdir(parents=True)
        for original in source.iterdir():
            if original.suffix.lower() in {'.exe', '.dll'}:
                os.link(original, target / original.name)


def environment(scratch):
    keep = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'SYSTEMDRIVE',
            'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    for key in ('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        directory = scratch / key
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    env.update(PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               ORION_TEST_EGRESS_LOG=str(scratch / 'blocked-egress.log'),
               ORION_MDNS='0', ORION_ROUTER_MDNS='0', ORION_FLUTTER_AUTOSTART='0',
               ORION_LOCAL_SHORTCUTS='0', ORION_PUBLIC_BIND_HOST='127.0.0.1',
               ORION_MOBILE_BIND_HOST='127.0.0.1', ORION_ROUTER_TLS_HOST='127.0.0.1',
               LOCAL_IP='127.0.0.1', HOST_IP='127.0.0.1')
    return env


def setup(kind, scratch):
    root = scratch / kind
    root.mkdir()
    env = environment(scratch)
    dbport = port()
    if kind == 'hub':
        hub = root / 'services/hub'
        copy_source(ROOT / 'services/hub/src', hub / 'src')
        copy_source(ROOT / 'services/shared', root / 'services/shared')
        shutil.copy2(ROOT / 'services/hub/run_local.py', hub / 'run_local.py')
        py = runtime(ROOT / 'services/hub/.venv', hub / '.venv')
        binaries(ROOT / '.local_db/postgres', root / '.local_db/postgres')
        binaries(ROOT / '.local_db/redis', root / '.local_db/redis')
        data = root / '.local_db/postgres/data'
        ctl = root / '.local_db/postgres/bin/pg_ctl.exe'
        env.update(HUB_PORT=str(port()), ORION_PUBLIC_PORT=str(port()),
                   ORION_LOCAL_PORT=str(port()), ORION_MOBILE_PORT=str(port()),
                   POSTGRES_HOST='127.0.0.1', POSTGRES_PORT=str(dbport),
                   POSTGRES_USER='orion', POSTGRES_PASSWORD='fixture-password', POSTGRES_DB='orion',
                   REDIS_HOST='127.0.0.1', REDIS_PORT=str(port()),
                   JWT_SECRET_KEY='isolated-product-test-' + os.urandom(32).hex(),
                   ADMIN_API_KEY='isolated-admin-' + os.urandom(16).hex(),
                   ORION_SECURITY_DIR=str(root / 'files/security'),
                   ORION_MDNS_ID_FILE=str(hub / 'data/mdns-id'),
                   ORION_ROUTER_CONNECTION_FILE=str(root / 'selected-router.json'),
                   PYTHONPATH=os.pathsep.join([str(root), str(hub / 'src')]))
        # Empty dotenv files prevent any source-checkout settings from loading.
        (root / 'services/.env.global').touch()
        (root / 'services/.env.global.local').touch()
        subprocess.run([str(ctl.with_name('initdb.exe')), '-D', str(data), '-U', 'orion',
                        '--auth=trust', '--locale=C', '-E', 'UTF8'], env=env,
                       stdout=subprocess.DEVNULL, check=True, timeout=90)
        db = Postgres(ctl, data, dbport, 'orion', 'postgres')
        db.ensure()
        try:
            db.sql('CREATE DATABASE orion OWNER orion')
        finally:
            db.stop()
        db.database = 'orion'
        argv = [str(py), '-m', 'services.shared.native_services']
        start = argv + ['start-hub', str(root), '']
        stop = argv + ['stop-hub', str(root), '']
        component = 'hub'
    else:
        source = ROOT.parent / 'orion-router'
        # Only tracked public source and the new lifecycle files; never user data.
        names = subprocess.check_output(['git', '-C', str(source), 'ls-files'], text=True).splitlines()
        names += [str(p.relative_to(source)) for p in (source / 'core').glob('*lifecycle*.py')]
        names += ['core/native_postgres.py', 'core/process_child.py', 'core/process_child.cjs',
                  'core/windows_job.py', 'core/process_host.py']
        for name in set(names):
            relative = Path(name)
            if relative.parts[0] in {'persistent', 'tools', '.pgdata-prod', '.pgdata-dev', '.git'}:
                continue
            if relative.name == '.env' or relative.name.endswith(('.log', '.pyc', '.pid')):
                continue
            original = source / relative
            if not original.is_file():
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        shutil.copytree(source / 'dashboard/out', root / 'dashboard/out')
        binaries(source / 'tools/pgsql', root / 'tools/pgsql')
        binaries(source / 'tools/ffmpeg', root / 'tools/ffmpeg')
        (root / 'tools/pgsql.ready').touch()
        py = runtime(source / '.venv', root / '.venv')
        env.update(POSTGRES_PORT=str(dbport), POSTGRES_DEV_PORT=str(port()),
                   ORION_ROUTER_TLS_PORT=str(port()), ORION_ROUTER_LOCAL_HTTP_PORT=str(port()),
                   PYTHONPATH=str(root))
        if kind == 'router-dev':
            env.update(ORION_ROUTER_TLS_DEV_PORT=str(port()), UI_PORT=str(port()),
                       NEXT_TELEMETRY_DISABLED='1')
            modules = root / 'dashboard/node_modules'
            # Turbopack rejects junctions outside its project filesystem root.
            # Separate entries keep all compiler writes within the fixture.
            shutil.copytree(source / 'dashboard/node_modules', modules, copy_function=os.link,
                            ignore=shutil.ignore_patterns('.cache'))
            shutil.copy2(source / 'dashboard/.npm_lockfile_hash', root / 'dashboard/.npm_lockfile_hash')
            lock = root / 'dashboard/package-lock.json'
            stored = (root / 'dashboard/.npm_lockfile_hash').read_text().strip()
            assert stored == f'{lock.stat().st_size}:{hashlib.sha256(lock.read_bytes()).hexdigest()}', 'Existing npm dependencies not current; no install allowed'
        (root / '.env').write_text('\n'.join(f'{k}={v}' for k, v in env.items() if k.startswith(('POSTGRES_', 'ORION_ROUTER_')) or k == 'UI_PORT') + '\n')
        db = Postgres(root / 'tools/pgsql/bin/pg_ctl.exe', root / '.pgdata-prod',
                      dbport, 'router_user', 'postgres', 'router_pass')
        start = [str(py), '-m', 'core.lifecycle_cli', 'start']
        stop = [str(py), '-m', 'core.lifecycle_cli', 'stop']
        component = 'router-prod'
        if kind == 'router-dev':
            component = 'router-dev'
            db = Postgres(root / 'tools/pgsql/bin/pg_ctl.exe', root / '.pgdata-dev',
                          int(env['POSTGRES_DEV_PORT']), 'router_user_dev', 'postgres', 'router_pass_dev')
            start = [str(py), '-c', "import sys; from pathlib import Path; from core.process_lifecycle import ensure_started; ensure_started(Path.cwd(), 'router-dev', [sys.executable, str(Path.cwd() / 'cli.py'), 'dev'], timeout=180)"]
    (scratch / 'ports.json').write_text(json.dumps({k: v for k, v in env.items() if 'PORT' in k}, indent=2))
    return root, env, db, start, stop, component


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['hub', 'router', 'router-dev'])
    parser.add_argument('--reuse', type=Path, help='Reuse a stopped Router test scratch only')
    args = parser.parse_args()
    base = ROOT / '.tmp/lifecycle'
    base.mkdir(parents=True, exist_ok=True)
    scratch = args.reuse.resolve() if args.reuse else Path(tempfile.mkdtemp(prefix='product-', dir=base))
    if args.reuse:
        assert scratch.parent == base.resolve() and scratch.name.startswith('product-')
        assert args.kind in {'router', 'router-dev'} and json.loads((scratch / 'result.json').read_text())['kind'] == args.kind
    print('Product scratch:', scratch, flush=True)
    report = {'kind': args.kind, 'checks': [], 'success': False}
    db = None
    root = None
    try:
        if args.reuse:
            root = scratch / args.kind
            mode = 'dev' if args.kind == 'router-dev' else 'prod'
            component = 'router-' + mode
            previous = Runtime(root, component).read()
            assert previous and previous['status'] == 'stopped' and not living(previous['supervisor'])
            assert not any(child_tree_alive(c) for c in previous['children'].values())
            # Preserve every prior result/log before rerunning this synthetic root.
            archive = scratch / ('previous-' + str(time.time_ns()))
            archive.mkdir()
            for file in scratch.glob('*.log'):
                shutil.copy2(file, archive / file.name)
            shutil.copy2(scratch / 'result.json', archive / 'result.json')
            source = ROOT.parent / 'orion-router'
            for name in ('core/process_lifecycle.py', 'core/process_child.cjs', 'core/native_postgres.py',
                         'core/process_child.py', 'core/windows_job.py', 'core/process_host.py',
                         'bin/common.py', 'bin/dev.py', 'bin/prod.py'):
                shutil.copy2(source / name, root / name)
            env = environment(scratch)
            env.update(json.loads((scratch / 'ports.json').read_text()),
                       NEXT_TELEMETRY_DISABLED='1', PYTHONPATH=str(root))
            db = Postgres(root / 'tools/pgsql/bin/pg_ctl.exe', root / ('.pgdata-' + mode),
                          int(env['POSTGRES_DEV_PORT' if mode == 'dev' else 'POSTGRES_PORT']),
                          'router_user_dev' if mode == 'dev' else 'router_user', 'postgres',
                          'router_pass_dev' if mode == 'dev' else 'router_pass')
            assert db.owner() is None
            py = root / '.venv/Scripts/python.exe'
            start = [str(py), '-c', "import sys; from pathlib import Path; from core.process_lifecycle import ensure_started; ensure_started(Path.cwd(), 'router-dev', [sys.executable, str(Path.cwd() / 'cli.py'), 'dev'], timeout=180)"]
            if mode == 'prod':
                start = [str(py), '-m', 'core.lifecycle_cli', 'start']
            stop = [str(py), '-m', 'core.lifecycle_cli', 'stop']
        else:
            root, env, db, start, stop, component = setup(args.kind, scratch)
        def call(argv, label):
            with (scratch / (label + '.log')).open('wb') as log:
                result = subprocess.run(argv, cwd=root, env=env, stdout=log, stderr=log, timeout=220)
            if result.returncode:
                raise AssertionError(f'{label} exited {result.returncode}; inspect retained logs')
        with ThreadPoolExecutor(max_workers=3) as workers:
            list(workers.map(lambda i: call(start, f'concurrent-start-{i}'), range(3)))
        first = Runtime(root, component).read()
        assert first['status'] == 'ready'
        assert db.ready()
        report['checks'].append('3 concurrent real starts, one ready supervisor, authenticated durable database')
        call(start, 'repeat-start')
        assert Runtime(root, component).read()['run_id'] == first['run_id']
        report['checks'].append('repeat start retains supervisor identity')
        call(stop, 'stop')
        record = Runtime(root, component).read()
        assert record['status'] == 'stopped'
        assert not any(child_tree_alive(c) for c in first['children'].values())
        assert db.owner() is None
        report['checks'].append('controlled full application shutdown before PostgreSQL, no living recorded child')
        identity = (root / '.orion-lifecycle' / component / 'identity.json').read_bytes()
        call(start, 'restart')
        assert identity == (root / '.orion-lifecycle' / component / 'identity.json').read_bytes()
        assert db.ready()
        call(stop, 'final-stop')
        report['checks'].append('real restart and stop, persistent component identity unchanged')
        report['success'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if root is not None:
            try:
                record = Runtime(root, component).read()
                if record and record['status'] != 'stopped':
                    stop_component(root, component, timeout=120)
            except Exception as exc:
                report['cleanup_error'] = repr(exc)
        if db is not None:
            try:
                db.stop()
            except Exception as exc:
                report['database_cleanup_error'] = repr(exc)
        (scratch / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
