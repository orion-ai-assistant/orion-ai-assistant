"""Registered source launcher. Packaged runtime trust is a stage 05 dependency.

Closing the browser window leaves services running. Only explicit stop shuts
down services; connect-only Routers are never started or stopped here.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import tomllib
import urllib.request

from .installation_registry import Registry, RegistryError, operation_lock, _atomic_write
from .installation_paths import canonical_path


def managed_components(record):
    result = []
    hubs = [c for c in record['components'] if c['kind'] == 'hub']
    routers = [c for c in record['components'] if c['kind'] == 'router']
    if len(hubs) > 1 or (not hubs and (len(routers) != 1 or any(c['kind'] == 'client' for c in record['components']))):
        raise RegistryError('Select one Hub or one standalone Router')
    if len([c for c in record['components'] if c['kind'] == 'client']) > 1:
        raise RegistryError('Select one registered client before launch')
    for item in record['components']:
        if item['kind'] == 'client' or item['management'] == 'connect-only':
            continue
        if item['ownership'] != 'owned' or item['distribution'] not in {'legacy-source', 'development', 'packaged'}:
            raise RegistryError('Local source ownership/runtime is unavailable; package repair is required')
        root = canonical_path(item['code_path'], must_exist=True)
        if item['distribution'] == 'packaged':
            from .release_packages import verify_registered_package
            release = verify_registered_package(root)
            if release['component'] != item['kind']:
                raise RegistryError('Package component mismatch')
            for field in ('data_path', 'state_path'):
                target = canonical_path(item[field], must_exist=True)
                if target == root or root in target.parents or target in root.parents:
                    raise RegistryError('Package data/state overlaps immutable code')
            result.append(item)
            continue
        if Path(item['state_path']) != root / '.orion-lifecycle' or Path(item['data_path']) != root:
            raise RegistryError('External data/state runtime is not supported by this source launcher')
        metadata = root / ('services/hub/pyproject.toml' if item['kind'] == 'hub' else 'pyproject.toml')
        if tomllib.loads(metadata.read_text(encoding='utf-8')).get('project', {}).get('name') != 'orion-' + item['kind']:
            raise RegistryError('Registered path is not the expected Orion product')
        for mode, expected in item.get('lifecycle_ids', {}).items():
            identity = json.loads((root / '.orion-lifecycle' / mode / 'identity.json').read_text(encoding='utf-8'))
            if (not isinstance(identity, dict) or identity.get('v') != 1 or identity.get('id') != expected or identity.get('component') != mode
                    or identity.get('root') != os.path.normcase(os.path.realpath(root))):
                raise RegistryError('Registered lifecycle identity mismatch; repair required')
        result.append(item)
    if hubs and hubs[0] not in result:
        raise RegistryError('Hub local management is not authorized')
    return sorted(result, key=lambda c: c['kind'] != 'router')


@contextmanager
def launch_environment(registry):
    # Loaded in this dedicated launcher process, never in a long-lived API host.
    from dotenv import dotenv_values
    values = {}
    hub = next((c for c in registry.read()['components'] if c['kind'] == 'hub'), None)
    if hub:
        for base, name in ((hub['code_path'], '.env.global'), (hub['data_path'], '.env.global.local')):
            values.update({k: v for k, v in dotenv_values(Path(base) / 'services' / name).items() if v is not None})
    else:
        router = next(c for c in registry.read()['components'] if c['kind'] == 'router')
        values.update({k: v for k, v in dotenv_values(Path(router['data_path']) / '.env').items() if v is not None})
    clients = [c for c in registry.read()['components'] if c['kind'] == 'client']
    if clients:
        client = clients[0]
        if client['distribution'] not in {'legacy-source', 'development', 'packaged'}:
            raise RegistryError('Client runtime unsupported')
        values['ORION_FLUTTER_APP_DIR'] = str(canonical_path(client['code_path'], must_exist=True))
        if client['distribution'] == 'packaged':
            from .release_packages import verify_registered_package
            metadata = verify_registered_package(client['code_path'])
            if metadata['component'] != 'client':
                raise RegistryError('Client package component mismatch')
    elif hub:
        values.setdefault('ORION_FLUTTER_APP_DIR', os.environ.get('ORION_FLUTTER_APP_DIR', str(Path(hub['code_path']).parent / 'Client')))
    values.update(ORION_INSTALLATION_REGISTRY=str(registry.path), ORION_FLUTTER_OPEN_WINDOW='0')
    # Popen inherits a snapshot; restore our process even on a failed start.
    previous = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def show_window(registry, hub, *, timeout=30):
    from .flutter_launcher import flutter_url, open_window
    from .process_lifecycle import Runtime, child_running, owns_listener
    url = flutter_url(lambda name, default=None, **kw: os.environ.get(name, default)).replace('localhost', '127.0.0.1')
    port = int(url.rsplit(':', 1)[1])
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        state = Runtime(hub['code_path'], 'hub').read()
        child = state and state['children'].get('flutter')
        if child and child_running(child) and owns_listener(child['process'], port):
            try:
                with opener.open(url + '/__orion_flutter_health__', timeout=1) as response:
                    if response.read(128) == b'orion-flutter-web':
                        profile = registry.path.parent / 'flutter-ui-profile'
                        if not open_window(url + '/', allow_tab_fallback=False, profile=profile, reuse_existing=True):
                            raise RuntimeError('Orion window could not open; install Edge/Chrome or use repair')
                        return
            except OSError:
                pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Flutter UI unavailable; services remain running. Check the registered client build and supervisor.log')
        time.sleep(.2)


def close_window(registry):
    """Close the Orion app window opened by show_window.

    Only browser processes started with Orion's own dedicated profile directory are
    touched, so the user's normal Chrome/Edge windows are never affected."""
    import psutil
    profile = str(canonical_path(registry.path.parent / 'flutter-ui-profile')).lower().rstrip(chr(92) + '/')
    victims = []
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if (process.info['name'] or '').lower() not in {'chrome.exe', 'msedge.exe', 'chrome', 'msedge'}:
                continue
            if any(profile in (arg or '').lower().replace('/', chr(92)) for arg in process.info['cmdline'] or []):
                victims.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in victims:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(victims, timeout=5)
    return len(victims)


def wait_for_update(registry, *, timeout=1800, interval=2, sleep=None, clock=time.monotonic):
    """A second start while an update is being applied waits for it instead of failing.

    Returns True when it had to wait. After `timeout` (or a repair-required journal, which is not
    progress) the normal blocked-start error is raised by the caller's own checks."""
    from .update_gate import progress
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    waited = False
    while progress(registry.path) is not None and clock() < deadline:
        waited = True
        sleep(interval)
    return waited


TRAY_SCRIPT = 'Orion-Tray.ps1'


def tray_processes(registry):
    """Tray icon processes of THIS installation (identified by script path and registry path)."""
    import psutil
    script = str(canonical_path(registry.path.parent / 'bootstrap' / TRAY_SCRIPT)).lower()
    found = []
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            command = [(arg or '').lower() for arg in process.info['cmdline'] or []]
            if ((process.info['name'] or '').lower() in {'powershell.exe', 'pwsh.exe'}
                    and any(script == arg.strip('"') for arg in command)):
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def spawn_tray(registry, *, popen=None):
    """Show the notification-area icon (full Hub installs only; a Router-only install has none)."""
    if os.name != 'nt' or tray_processes(registry):
        return False
    import subprocess
    destination = canonical_path(registry.path.parent / 'bootstrap' / TRAY_SCRIPT)
    source = Path(__file__).with_name('tray.ps1').read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != source:
        _atomic_write(destination, source)
    # Outside any Hub-owned Job: only the launcher process (or the retained watcher) starts it.
    (popen or subprocess.Popen)(
        ['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
         '-File', str(destination), '-Registry', str(registry.path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    return True


def stop_tray(registry, *, timeout=6):
    """Ask the icon to hide itself and exit; terminate it only if it does not (no orphans, no ghost icon)."""
    if os.name != 'nt':
        return 0
    import psutil
    victims = tray_processes(registry)
    if not victims:
        return 0
    marker = canonical_path(registry.path.parent / 'bootstrap/tray.exit')
    marker.write_bytes(b'exit')
    _, alive = psutil.wait_procs(victims, timeout=timeout)
    for process in alive:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(alive, timeout=5)
    marker.unlink(missing_ok=True)
    return len(victims)


def launch(registry, *, startup=False, background=False, stop=False, keep_window=False):
    from .native_services import start_hub, start_router, stop_hub, stop_router
    if not stop:
        wait_for_update(registry)
        record = registry.read()
        if record and (not startup or record['preferences']['start_with_windows']):
            from .automatic_updates import run_startup
            run_startup(registry)
    with operation_lock(registry.path, timeout=180):
        record = registry.read()
        if record is None:
            raise RegistryError('No Orion installation registered; open installation repair')
        if startup and not record['preferences']['start_with_windows']:
            return {'status': 'disabled'}
        from .update_gate import blocked
        if not stop and blocked(registry.path):
            raise RegistryError('Update recovery in progress; resume the local update helper')
        components = managed_components(record)  # Preflight all paths before any start.
        packages = [json.loads((Path(c['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))
                    for c in record['components'] if c['distribution'] == 'packaged']
        if packages and not stop:
            from .release_packages import compatible
            compatible(packages)
        with launch_environment(registry):
            for item in (reversed(components) if stop else components):
                root = item['code_path']
                if item['kind'] == 'hub':
                    (stop_hub if stop else start_hub)(root)
                else:
                    (stop_router if stop else start_router)(str(Path(root) / 'cli.py'))
            if stop:
                stop_tray(registry)
            if stop and not keep_window:
                close_window(registry)
            if not stop:
                from .automatic_updates import spawn_watcher
                spawn_watcher(registry)
                if any(c['kind'] == 'hub' for c in components):
                    try:
                        spawn_tray(registry)
                    except OSError:
                        pass  # The icon is a convenience; services and window do not depend on it.
            if not stop and not background and (not startup or record['preferences']['show_client']):
                hub = next((c for c in components if c['kind'] == 'hub'), None)
                if hub:
                    show_window(registry, hub)
                else:
                    from .flutter_launcher import open_window
                    open_window('http://127.0.0.1:' + os.environ.get('ORION_ROUTER_LOCAL_HTTP_PORT', '20128') + '/dashboard')
        return {'status': 'stopped' if stop else 'ready'}


def install_entry(registry, *, desktop=True):
    from .windows_autostart import known_folder, shortcut
    record = registry.read()
    if record is None:
        raise RegistryError('No installation registered')
    if registry.path.name != 'install.json':
        raise RegistryError('Stable Windows entry requires a registry named install.json')
    entry = registry.path.parent / 'bootstrap/Orion.ps1'
    canonical_path(entry)
    entry.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(entry, Path(__file__).with_name('launcher_entry.ps1').read_bytes())
    if desktop:
        shortcut(known_folder('desktop') / 'Orion.lnk', entry, record['installation_id'])
    return entry


def preferences(registry, *, start_with_windows, show_client, startup_directory=None):
    from .windows_autostart import known_folder, shortcut
    if type(start_with_windows) is not bool or type(show_client) is not bool:
        raise ValueError('Launcher preferences must be booleans')
    with operation_lock(registry.path):
        record = registry.read()
        if record is None:
            raise RegistryError('No installation registered')
        if start_with_windows:
            managed_components(record)
        entry = install_entry(registry, desktop=False)
        directory = canonical_path(startup_directory or known_folder('startup'), must_exist=True)
        path = directory / 'Orion.lnk'
        def update(current):
            if current != record:
                raise RegistryError('Installation changed; retry preferences')
            current['preferences'].update(start_with_windows=start_with_windows, show_client=show_client)
            return current
        if start_with_windows:
            shortcut(path, entry, record['installation_id'], startup=True)
            return registry.update(update)
        # A stale link after crash/removal failure remains inert: startup checks the registry.
        result = registry.update(update)
        shortcut(path, entry, record['installation_id'], startup=True, remove=True)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--startup', action='store_true')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('action', nargs='?', default='launch', choices=['launch', 'stop', 'install', 'preferences'])
    parser.add_argument('--start-with-windows', choices=['on', 'off'])
    parser.add_argument('--show-client', choices=['on', 'off'])
    args = parser.parse_args(argv)
    registry = Registry(args.registry)
    try:
        if args.action == 'install':
            with operation_lock(registry.path):
                result = {'entry': str(install_entry(registry))}
        elif args.action == 'preferences':
            if args.start_with_windows is None or args.show_client is None:
                parser.error('preferences requires --start-with-windows and --show-client')
            result = preferences(registry, start_with_windows=args.start_with_windows == 'on', show_client=args.show_client == 'on')
        else:
            result = launch(registry, startup=args.startup, background=args.background, stop=args.action == 'stop')
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        # Avoid logging credentials or complete subprocess environment/arguments.
        print('Orion launcher: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
