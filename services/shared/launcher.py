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


def wait_for_update(registry, *, timeout=1800, interval=2, sleep=None, clock=time.monotonic, apply_owner=False):
    """A second start while an update is being applied waits for it instead of failing.

    Covers both the helper's switch (journal phases) and the whole "update now" run of the watcher
    (stop -> switch -> restart): nothing may start services in between, or the apply fails because
    the install is busy. `apply_owner` is the watcher's own restart, which must not wait for itself.

    Returns True when it had to wait. After `timeout` (or a repair-required journal, which is not
    progress) the normal blocked-start error is raised by the caller's own checks."""
    from .update_gate import progress
    from . import update_now
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    waited = False
    while (progress(registry.path) is not None
           or (not apply_owner and update_now.in_progress(registry))) and clock() < deadline:
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


ELEVATED = ('ORION-ELEVATED: Orion baska bir yetki duzeyinde (yonetici olarak) calisiyor. '
            'Tepsiden Cik ile kapatin, sonra Orion\'u normal (yonetici olmadan) acin.')
ELEVATED_SELF = ('ORION-ELEVATED-SELF: Orion yonetici olarak baslatilamaz. '
                 'Orion\'u normal (yonetici olmayan) bir pencereden acin.')


def is_elevated():
    """This process runs with an elevated (administrator) token. Orion never runs that way: services started
    elevated cannot be managed, stopped or updated by the normal shortcut afterwards (and vice versa)."""
    if os.name != 'nt' or os.environ.get('ORION_ALLOW_ELEVATED') == '1':
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


DEELEVATE_STAMP = 'bootstrap/deelevate.stamp'
DEELEVATE_WINDOW = 60  # a second elevated start within this time means de-elevation does not work here


def relaunch_deelevated(registry, *, startup=False, background=False, popen=None, clock=time.time):
    """Start Orion again with the normal (limited) token, then the caller exits.

    Plain launch: `explorer.exe Orion.exe` (Explorer is the user's de-elevated shell; no prompt, no extra rights).
    Startup/background flags cannot travel through Explorer, so those use `runas /trustlevel:0x20000` (a
    limited-token process, also without a prompt). False when that is impossible or was just tried (UAC off,
    so everything is elevated): the caller then explains instead of looping."""
    import subprocess
    folder = registry.path.parent / 'bootstrap'
    stamp = registry.path.parent / DEELEVATE_STAMP
    try:
        if clock() - float(stamp.read_text(encoding='ascii').strip()) < DEELEVATE_WINDOW:
            return False
    except (OSError, ValueError):
        pass
    exe, entry = folder / 'Orion.exe', folder / 'Orion.ps1'
    if not startup and not background and exe.is_file():
        command = ['explorer.exe', str(exe)]
    elif entry.is_file():
        flags = (' -Startup' if startup else '') + (' -Background' if background else '')
        command = ['runas.exe', '/trustlevel:0x20000',
                   f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{entry}"{flags}']
    else:
        return False
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(clock()), encoding='ascii')
        (popen or subprocess.Popen)(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        return False
    return True


def explain(error):
    """Message for the entry script. Access-denied lifecycle errors mean another integrity level owns the
    services: say that in a form the script turns into a clear Turkish dialog (not the Repair dialog)."""
    text = str(error)
    if 'ORION-ELEVATED' in text:
        return text
    return ELEVATED if 'access denied' in text.lower() else text


STOP_RETRIES = 2          # a briefly busy lifecycle lock is retried; a real shutdown timeout is not
STOP_RETRY_DELAY = 3
WATCHER_EXIT = 'updates/watcher.exit'


def _transient(error):
    text = str(error).lower()
    return 'busy' in text or 'mutex' in text


def _stop_component(function, argument, *, sleep=time.sleep):
    """One component's stop. Another process may hold its lock for a moment (start, status scan): retry that."""
    for attempt in range(STOP_RETRIES + 1):
        try:
            return function(argument)
        except Exception as error:
            if attempt == STOP_RETRIES or not _transient(error):
                raise
            sleep(STOP_RETRY_DELAY)


def running_services(components):
    """Kinds that still have a running supervisor/child after a stop (verified from their own state)."""
    from .process_lifecycle import Runtime, child_tree_alive, LifecycleError
    left = []
    for item in components:
        modes = ['hub'] if item['kind'] == 'hub' else ['router-prod', 'router-dev']
        for mode in modes:
            try:
                state = Runtime(item['code_path'], mode).read()
                if state and (state['status'] != 'stopped'
                              or any(child_tree_alive(c) for c in state['children'].values())):
                    left.append(item['kind'])
                    break
            except LifecycleError as error:
                if 'access denied' in str(error).lower():
                    left.append(item['kind'])  # Owned by another integrity level: certainly not stopped by us.
                    break
            except (OSError, KeyError):
                continue
    return left


def request_watcher_exit(registry):
    """"Çık" also ends the retained watcher (it would otherwise keep polling, possibly mid-download)."""
    marker = registry.path.parent / WATCHER_EXIT
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b'exit')


def launch(registry, *, startup=False, background=False, stop=False, keep_window=False, apply_owner=False):
    from .native_services import start_hub, start_router, stop_hub, stop_router
    if not stop and is_elevated():
        # Never start services elevated: hand over to a normal-rights Orion and leave.
        if relaunch_deelevated(registry, startup=startup, background=background):
            return {'status': 'relaunched-deelevated'}
        raise RegistryError(ELEVATED_SELF)
    if not stop:
        wait_for_update(registry, apply_owner=apply_owner)
        record = registry.read()
        # The watcher's own restart never re-applies: a failed/rolled-back attempt is not retried behind the
        # user's back (the next explicit launch or "Şimdi güncelle" decides).
        if record and not apply_owner and (not startup or record['preferences']['start_with_windows']):
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
        if not stop and os.name == 'nt':
            try:
                refresh_links(registry)
            except (OSError, RuntimeError, ValueError, KeyError):
                pass  # Name and icon are cosmetic; the next start retries.
        packages = [json.loads((Path(c['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))
                    for c in record['components'] if c['distribution'] == 'packaged']
        if packages and not stop:
            from .release_packages import compatible
            compatible(packages)
        with launch_environment(registry):
            failed = {}
            for item in (reversed(components) if stop else components):
                root = item['code_path']
                if not stop:
                    (start_hub if item['kind'] == 'hub' else start_router)(
                        root if item['kind'] == 'hub' else str(Path(root) / 'cli.py'))
                    continue
                # Stopping must never give up halfway: a Hub that cannot stop in time used to leave the
                # Router (and tray, window, watcher) untouched. Every component is attempted; failures are
                # collected and reported at the end.
                try:
                    if item['kind'] == 'hub':
                        _stop_component(stop_hub, root)
                    else:
                        _stop_component(stop_router, str(Path(root) / 'cli.py'))
                except Exception as error:
                    failed[item['kind']] = type(error).__name__ + ': ' + str(error)
            if stop:
                from . import update_log
                updates = registry.path.parent / 'updates'
                if not apply_owner:
                    # An explicit stop voids any "update now" request that was waiting for this Hub, and ends
                    # the updating window; the update flow itself stops services with apply_owner=True.
                    try:
                        from . import update_now, update_ui
                        update_now.abandon(registry)
                        update_ui.stop_window(registry)
                    except Exception as error:
                        update_log.log_exception(updates, 'stop.cleanup', error)
                stop_tray(registry)
                if not keep_window:
                    close_window(registry)
                left = sorted(set(running_services(components)) | set(failed))
                if any('access denied' in text.lower() for text in failed.values()):
                    raise RegistryError(ELEVATED)
                if left:
                    update_log.log(updates, 'stop.incomplete', services=','.join(left),
                                   errors=' | '.join(f'{k}={v}' for k, v in sorted(failed.items())))
                    raise RegistryError(
                        'Orion tamamen kapatilamadi; calismaya devam eden: ' + ', '.join(left)
                        + '. Bir sure sonra Cik/-Stop komutunu yeniden deneyin; ayrinti updates/watcher.log.')
                if not apply_owner:
                    request_watcher_exit(registry)
            if not stop:
                (registry.path.parent / WATCHER_EXIT).unlink(missing_ok=True)
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


DESKTOP_LINK = 'Orion.lnk'
STARTUP_LINK = 'Orion AI Assistant.lnk'
LEGACY_STARTUP_LINK = 'Orion.lnk'  # Installs before Orion.exe named their Startup entry like the desktop one.
ROUTER_LINK = 'Orion Router.lnk'   # Router-only installations: same file name on the desktop and in Startup.
ALL_DESKTOP_LINKS = (DESKTOP_LINK, ROUTER_LINK)
ALL_STARTUP_LINKS = (STARTUP_LINK, ROUTER_LINK, LEGACY_STARTUP_LINK)


def identity(record):
    """Product identity of this installation: a Hub installation is "Orion AI Assistant"; a Router-only one
    is "Orion Router" (own links, own Orion.exe version resource, so Task Manager shows the right name)."""
    from .windows_autostart import PRODUCT_NAME, ROUTER_PRODUCT_NAME
    hub = record is None or any(c['kind'] == 'hub' for c in record.get('components', []))
    return {'product': PRODUCT_NAME if hub else ROUTER_PRODUCT_NAME, 'router_only': not hub,
            'desktop': DESKTOP_LINK if hub else ROUTER_LINK, 'startup': STARTUP_LINK if hub else ROUTER_LINK}


def retained_launcher(registry, product=None):
    """Orion.exe of the trusted retained bootstrap (the signed bundle that is running, or the active one).

    Nothing is downloaded or compiled on the user's machine; a bundle without an exe, or one whose
    bytes differ from the hash recorded in its own bootstrap.json, yields None (PowerShell fallback).
    A Router-only installation uses the bundle's `OrionRouter.exe` (file description "Orion Router").
    """
    from .release_packages import sha256
    if product is None:
        try:
            product = identity(registry.read())['product']
        except (OSError, ValueError, RegistryError):
            product = None
    router = product is not None and product != identity(None)['product']
    name, key = ('OrionRouter.exe', 'router_launcher_sha256') if router else ('Orion.exe', 'launcher_sha256')
    roots = [Path(__file__).resolve().parents[2]]
    try:
        from .bootstrap_update import pointer_root
        roots.append(pointer_root(registry))
    except Exception:
        pass
    for root in roots:
        try:
            exe = Path(root) / name
            info = json.loads((Path(root) / 'bootstrap.json').read_text(encoding='utf-8-sig'))
            if exe.is_file() and not exe.is_symlink() and sha256(exe) == info.get(key):
                return exe
        except (OSError, TypeError, ValueError):
            continue
    return None


def _remove_own_link(directory, name, entry, installation_id, *, startup):
    from .windows_autostart import shortcut
    if (directory / name).exists():
        try:
            shortcut(directory / name, entry, installation_id, startup=startup, remove=True)
        except RuntimeError:
            pass  # Someone else's link of that name: preserved, never ours to touch.


def sync_desktop_link(record, entry, *, create):
    """The desktop link of the current identity (restyled/created) and no stale link of the other one."""
    from .windows_autostart import known_folder, shortcut
    ident, directory = identity(record), known_folder('desktop')
    current = directory / ident['desktop']
    if create or current.exists():
        shortcut(current, entry, record['installation_id'], product=ident['product'])
    for name in ALL_DESKTOP_LINKS:
        if name != ident['desktop']:
            _remove_own_link(directory, name, entry, record['installation_id'], startup=False)


def install_entry(registry, *, desktop=True):
    record = registry.read()
    if record is None:
        raise RegistryError('No installation registered')
    if registry.path.name != 'install.json':
        raise RegistryError('Stable Windows entry requires a registry named install.json')
    entry = registry.path.parent / 'bootstrap/Orion.ps1'
    canonical_path(entry)
    entry.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(entry, Path(__file__).with_name('launcher_entry.ps1').read_bytes())
    source = retained_launcher(registry)
    if source is not None:
        destination = entry.with_name('Orion.exe')
        data = source.read_bytes()
        if not destination.exists() or destination.read_bytes() != data:
            try:
                _atomic_write(destination, data)
            except OSError:
                if not destination.exists():
                    raise  # A running older copy is fine to keep; a missing one is not.
    if desktop:
        sync_desktop_link(record, entry, create=True)
    return entry


def _startup_links(directory, entry, installation_id, enabled, record=None):
    """Product-named Startup entry; the pre-Orion.exe `Orion.lnk` (and the other identity's link) is
    replaced if it is ours. Stale Task Manager "Startup apps" state of vanished links is dropped too."""
    from .windows_autostart import shortcut, clean_startup_approved
    ident = identity(record)
    current = ident['startup']
    if enabled:
        shortcut(directory / current, entry, installation_id, startup=True, product=ident['product'])
    elif (directory / current).exists():
        shortcut(directory / current, entry, installation_id, startup=True, remove=True)
    for name in ALL_STARTUP_LINKS:
        if name != current:
            _remove_own_link(directory, name, entry, installation_id, startup=True)
    clean_startup_approved({n for n in ALL_STARTUP_LINKS if (directory / n).exists()},
                           [n for n in ALL_STARTUP_LINKS if not (directory / n).exists()])


LINKS_REVISION = 2  # 2: identity per installation (Orion Router) + stale StartupApproved cleanup


def refresh_links(registry):
    """Give an existing install the product name/icon after a bootstrap update; once per Orion.exe.

    Never creates a Startup entry the user turned off (it only removes ours), and only restyles a
    desktop link that already exists.
    """
    from .release_packages import sha256
    from .windows_autostart import known_folder
    record = registry.read()
    exe = retained_launcher(registry)
    if exe is None or registry.path.name != 'install.json' or record is None:
        return False
    ident = identity(record)
    marker = registry.path.parent / 'bootstrap/links.json'
    digest = sha256(exe)
    wanted = {'schema': 1, 'launcher_sha256': digest, 'product': ident['product'], 'revision': LINKS_REVISION}
    try:
        if json.loads(marker.read_text(encoding='utf-8')) == wanted:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    entry = install_entry(registry, desktop=False)
    sync_desktop_link(record, entry, create=False)
    _startup_links(known_folder('startup'), entry, record['installation_id'],
                   record['preferences']['start_with_windows'], record)
    _atomic_write(marker, json.dumps(wanted).encode())
    return True


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
        def update(current):
            if current != record:
                raise RegistryError('Installation changed; retry preferences')
            current['preferences'].update(start_with_windows=start_with_windows, show_client=show_client)
            return current
        if start_with_windows:
            _startup_links(directory, entry, record['installation_id'], True, record)
            return registry.update(update)
        # A stale link after crash/removal failure remains inert: startup checks the registry.
        result = registry.update(update)
        _startup_links(directory, entry, record['installation_id'], False, record)
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
        print('Orion launcher: ' + explain(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
