"""User-facing uninstall: "Uygulamalar ve özellikler" entry and the safe removal flow behind it.

Options: remove Hub only (with its web client), Router only, or everything; keep (default) or delete the user
data of what is removed; keep (default) or delete models/downloads when everything goes. Built on
`package_maintenance` (plan, expected-revision uninstall, bounded code cleanup): its guarantees are not
weakened. Nothing outside the registered, owned, packaged components and the fixed installer-owned folders
of the installation root is ever deleted; the developer registrations (hub-installation.json,
router-installation.json) and `lifecycle-tests` are never read or touched. Data kept by an uninstall carries
a marker so a later install adopts it (same Hub secrets, identity, databases) instead of refusing.

Firewall rules need administrator rights: Windows asks once (UAC); a declined or failed request is reported
and never fails the uninstall.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .component_setup import KIND_DIRS, KEPT_MARKER, remove_tree
from .installation_registry import Registry, RegistryError
from .installation_paths import canonical_path

ARP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\OrionAIAssistant'
SCRIPT = 'Orion-Uninstall.ps1'
# Installer-owned children of the installation root that go away with the last component.
ROOT_OWNED = ('bootstrap', 'release-cache', 'updates', 'maintenance', 'flutter-ui-profile', 'install.json',
              'install.json.bak')
NEVER_TOUCH = ('hub-installation.json', 'router-installation.json', 'lifecycle-tests')
SCOPES = ('hub', 'router', 'all')


class UninstallError(ValueError):
    """Message is Turkish and safe to show; nothing beyond what it says was changed."""


# --- Apps & features entry ---------------------------------------------------------------------------

def _powershell():
    return str(Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


def entry_values(registry, record):
    """DisplayName/Icon/UninstallString of the entry; the Router-only install names itself "Orion Router"."""
    from .launcher import identity
    ident = identity(record)
    folder = registry.path.parent / 'bootstrap'
    version = ''
    for kind in ('hub', 'router'):
        item = next((c for c in record['components'] if c['kind'] == kind), None)
        if item:
            try:
                version = json.loads((Path(item['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))['version']
            except (OSError, ValueError, KeyError):
                pass
            break
    return {'DisplayName': ident['product'], 'DisplayVersion': version, 'Publisher': 'Orion',
            'InstallLocation': record['root'], 'DisplayIcon': str(folder / 'Orion.exe') + ',0',
            'UninstallString': f'"{_powershell()}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
                               f'-File "{folder / SCRIPT}" -Registry "{registry.path}"',
            'NoModify': 1, 'NoRepair': 1}


def register_entry(registry, *, key=ARP_KEY, hive=None):
    """Create/refresh the HKCU Uninstall entry and the script it runs. Cosmetic: never raises."""
    if os.name != 'nt':
        return False
    try:
        import winreg
        record = registry.read()
        if record is None or not record['components']:
            return False
        folder = registry.path.parent / 'bootstrap'
        source = Path(__file__).with_name('uninstall.ps1').read_bytes()
        script = folder / SCRIPT
        folder.mkdir(parents=True, exist_ok=True)
        if not script.exists() or script.read_bytes() != source:
            from .installation_registry import _atomic_write
            _atomic_write(script, source)
        values = entry_values(registry, record)
        with winreg.CreateKeyEx(hive if hive is not None else winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            for name, value in values.items():
                if isinstance(value, int):
                    winreg.SetValueEx(handle, name, 0, winreg.REG_DWORD, value)
                else:
                    winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)
        return True
    except (OSError, ImportError, ValueError, KeyError, RegistryError):
        return False


def unregister_entry(*, key=ARP_KEY, hive=None):
    if os.name != 'nt':
        return False
    try:
        import winreg
        winreg.DeleteKey(hive if hive is not None else winreg.HKEY_CURRENT_USER, key)
        return True
    except OSError:
        return False


# --- the removal flow --------------------------------------------------------------------------------

def components_for(record, scope):
    if scope not in SCOPES:
        raise UninstallError('Bilinmeyen kaldırma seçeneği.')
    kinds = {c['kind'] for c in record['components']}
    wanted = kinds if scope == 'all' else ({'hub', 'client'} if scope == 'hub' else {'router'})
    wanted &= kinds
    if not wanted:
        raise UninstallError('Seçilen bileşen bu kurulumda yok.')
    return wanted


def _inside(path, root):
    try:
        return canonical_path(path).is_relative_to(canonical_path(root))
    except ValueError:
        return False


def _remove_tree(path, root, leftovers):
    path = Path(path)
    if not path.exists():
        return
    if not _inside(path, root) or path == Path(root) or path.name in NEVER_TOUCH:
        leftovers.append(path.name)
        return
    if not remove_tree(path):
        leftovers.append(str(path))


def describe(error):
    """Class, message and where it was raised, for the uninstall log (never just the class)."""
    import traceback
    frames = traceback.extract_tb(error.__traceback__)
    where = f' ({Path(frames[-1].filename).name}:{frames[-1].lineno})' if frames else ''
    return f'{type(error).__name__}: {error}{where}'


def keep_data_marker(directory, kind):
    marker = Path(directory) / KEPT_MARKER
    marker.write_text(json.dumps({'schema': 1, 'kind': kind, 'kept_at': time.time()}), encoding='utf-8')


def retained_runtime_processes(roots, *, exclude=()):
    """Processes running from the retained bootstrap runtime (watcher, update helper). Never ours."""
    try:
        import psutil
    except ImportError:
        return []
    mine = {os.getpid(), *exclude}
    try:
        mine.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    found = []
    wanted = [str(Path(r)).lower() + os.sep for r in roots]
    for process in psutil.process_iter(['pid', 'exe']):
        try:
            exe = (process.info['exe'] or '').lower()
            if process.info['pid'] not in mine and any(exe.startswith(w) for w in wanted):
                found.append(process)
        except (psutil.Error, OSError):
            continue
    return found


def stop_background(registry, roots):
    """The retained watcher/helper must not outlive the installation (and must release the folders)."""
    from . import update_now
    deadline = time.monotonic() + 15
    while update_now.watcher_alive(registry) and time.monotonic() < deadline:
        time.sleep(1)
    victims = retained_runtime_processes(roots)
    for process in victims:
        try:
            process.terminate()
        except Exception:
            pass
    try:
        import psutil
        psutil.wait_procs(victims, timeout=5)
    except Exception:
        pass
    return len(victims)


CLEANUP_VALUE = 'OrionUninstallCleanup'
RUN_ONCE = r'Software\Microsoft\Windows\CurrentVersion\RunOnce'
# Runs hidden after the uninstaller (this Python and the dialog that started it) exited, and once more at the next
# sign-in when something was still held. It stops as soon as a new installation appears (install.json, or a
# Bootstrap-Installer folder it was not asked to remove), so it never deletes a reinstall in progress.
CLEANUP_SCRIPT = r"""param([string]$Pids = '', [string]$Later = '')
$request = Get-Content -LiteralPath ([IO.Path]::ChangeExtension($PSCommandPath, '.json')) -Raw | ConvertFrom-Json
function Wait-Exit([string]$ids) {
  $deadline = (Get-Date).AddMinutes(30)
  foreach ($id in @($ids -split ',' | Where-Object { $_ })) { while ((Get-Process -Id $id -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 } }
}
$names = @($request.paths | ForEach-Object { Split-Path -Leaf $_ })
function Superseded {
  if (Test-Path -LiteralPath (Join-Path $request.root 'install.json')) { return $true }
  return [bool](Get-ChildItem -LiteralPath $request.root -Filter 'Bootstrap-Installer*' -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $names -notcontains $_.Name })
}
function Sweep([int]$tries) {
  $left = 1
  for ($i = 0; $i -lt $tries -and $left -gt 0; $i++) {
    if (Superseded) { return 0 }
    $left = 0
    foreach ($p in $request.paths) {
      if (Test-Path -LiteralPath $p) {
        Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Attributes = 'Normal' } catch {} }
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $p) { $left++ }
      }
    }
    if ($left -gt 0) { Start-Sleep -Seconds 2 }
  }
  return $left
}
# The uninstaller (whose runtime lives in what is removed) first; the result window only if something stays held.
Wait-Exit $Pids
$left = Sweep 15
if ($left -gt 0 -and $Later) { Wait-Exit $Later; $left = Sweep 60 }
if ($left -eq 0) {
  try { if (-not (Get-ChildItem -LiteralPath $request.root -Force -ErrorAction Stop)) { Remove-Item -LiteralPath $request.root -Force } } catch {}
  Remove-ItemProperty -Path ('HKCU:\' + $request.run_once) -Name $request.value -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath ([IO.Path]::ChangeExtension($PSCommandPath, '.json')), $PSCommandPath -Force -ErrorAction SilentlyContinue
}
"""


def _run_once(command, *, remove=False):
    if os.name != 'nt':
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_ONCE, 0, winreg.KEY_SET_VALUE) as handle:
            if remove:
                winreg.DeleteValue(handle, CLEANUP_VALUE)
            else:
                winreg.SetValueEx(handle, CLEANUP_VALUE, 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def _start_outside_job(arguments):
    """Start the cleanup through WMI (Win32_Process.Create): its parent is the WMI service, so it is in no job of
    ours. Settings > Apps runs the uninstall dialog in a job that kills every child when the dialog closes and does
    not allow breakaway; a cleanup started with Popen then died with the window and left the bootstrap runtime."""
    if os.name != 'nt':
        return False
    line = subprocess.list2cmdline(arguments).replace("'", "''")
    # ShowWindow=0 (SW_HIDE): without it Windows 11's default terminal (Windows Terminal) opened an empty window
    # next to the uninstall dialog, which -WindowStyle Hidden cannot hide. (No console at all, DETACHED_PROCESS,
    # makes PowerShell exit without running the script.)
    probe = ("$s = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
             "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
             "@{CommandLine='" + line + "'; ProcessStartupInformation=$s}; exit [int]$r.ReturnValue")
    try:
        return subprocess.run([_powershell(), '-NoProfile', '-NonInteractive', '-Command', probe],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def schedule_removal(paths, *, root, wait=None, later=None, folder=None, spawn=True, run_once=_run_once):
    """Delete what is still held (the running bootstrap runtime, files a closing process keeps open) once this
    uninstaller exited: a hidden detached PowerShell waits for that PID (`wait`), retries, waits for the result
    dialog (`later`) only if something is still held, removes the then-empty root and, if something is still
    locked, a HKCU RunOnce entry repeats it at the next sign-in. Returns the cleanup script path, or None when there was nothing (safe) to schedule."""
    safe = [str(p) for p in paths if _inside(p, root) and Path(p) != Path(root) and Path(p).name not in NEVER_TOUCH]
    if not safe:
        return None
    wait = [os.getpid()] if wait is None else list(wait)
    later = [os.getppid()] if later is None else list(later)
    folder = Path(folder or os.environ.get('TEMP') or Path.home())
    script = folder / ('orion-cleanup-' + uuid.uuid4().hex[:12] + '.ps1')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        script.with_suffix('.json').write_text(json.dumps({'paths': safe, 'root': str(root), 'run_once': RUN_ONCE,
                                                           'value': CLEANUP_VALUE}), encoding='utf-8')
        script.write_text(CLEANUP_SCRIPT, encoding='utf-8')
    except OSError:
        return None
    command = [_powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
               '-File', str(script)]
    run_once(subprocess.list2cmdline(command))  # Last resort, also when this process is killed before the spawn.
    if spawn:
        flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        arguments = command + (['-Pids', ','.join(map(str, wait))] if wait else [])             + (['-Later', ','.join(map(str, later))] if later else [])
        if _start_outside_job(arguments):
            return script
        for extra in (getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0), 0):  # Outlive a job that kills on close.
            try:
                subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags | extra, close_fds=True)
                break
            except OSError:
                continue
    return script


def remove_links(registry, installation_id):
    """Both product identities' desktop/Startup links (if ours) and the stale Task Manager Startup state."""
    from .launcher import ALL_DESKTOP_LINKS, ALL_STARTUP_LINKS
    from .windows_autostart import known_folder, shortcut, clean_startup_approved
    entry = registry.path.parent / 'bootstrap/Orion.ps1'
    errors = []
    for kind, names in (('desktop', ALL_DESKTOP_LINKS), ('startup', ALL_STARTUP_LINKS)):
        directory = known_folder(kind)
        for name in names:
            path = directory / name
            if path.exists():
                try:
                    shortcut(path, entry, installation_id, startup=kind == 'startup', remove=True)
                except RuntimeError:
                    errors.append(name)  # Someone else's link of that name: preserved.
    startup = known_folder('startup')
    clean_startup_approved({n for n in ALL_STARTUP_LINKS if (startup / n).exists()},
                           [n for n in ALL_STARTUP_LINKS if not (startup / n).exists()])
    return errors


def uninstall_orion(registry, scope, *, keep_data=True, keep_models=True, firewall=True, stop=None,
                    links=remove_links, helper=None, firewall_api=None, background=True, entry_key=ARP_KEY):
    """Run the whole flow. Returns a path-free result dict; raises UninstallError before changing anything
    when the request cannot be honoured (no installation, source/development components, running services
    that cannot be stopped)."""
    from . import package_maintenance as maintenance
    from .legacy_conversion import revision
    from .launcher import launch, identity, install_entry
    registry = registry if isinstance(registry, Registry) else Registry(registry)
    record = registry.read()
    if record is None:
        raise UninstallError('Kayıtlı bir Orion kurulumu bulunamadı.')
    if registry.path.name != 'install.json':
        raise UninstallError('Yalnızca Orion\'un kendi kurulum kaydı kaldırılabilir.')
    kinds = components_for(record, scope)
    try:
        plan = maintenance.removal_plan(registry, kinds)
    except ValueError as error:
        raise UninstallError('Bu kurulum Orion paket kurucusuyla yapılmamış (kaynak/geliştirme kaydı); '
                             'ona dokunulmaz. Ayrıntı: ' + str(error)) from error
    removed = sorted({c['kind'] for c in plan['components']})
    root = Path(record['root'])
    everything = {c['kind'] for c in record['components']} <= set(removed)
    stop = stop or (lambda reg: launch(reg, stop=True))
    result = {'schema': 1, 'status': 'uninstalled', 'removed': removed, 'data': 'kept' if keep_data else 'deleted',
              'models': 'kept' if keep_models or not everything else 'deleted', 'firewall': 'skipped',
              'leftovers': [], 'notes': []}
    try:
        stop(registry)
    except Exception as error:
        raise UninstallError('Orion kapatılamadı, hiçbir şey kaldırılmadı: ' + str(error)) from error
    try:
        outcome = maintenance.uninstall(registry, removed, expected_revision=revision(registry.read()),
                                        helper=helper, links=links)
    except ValueError as error:
        raise UninstallError('Kaldırma yapılamadı (' + type(error).__name__ + '): ' + str(error)) from error
    result['operation_id'] = outcome['operation_id']
    # Old releases of what was removed (bounded: only these components' immutable releases). A release inside a
    # removed component's own releases folder goes with that folder below; purging it first only fails on it.
    going = [root / KIND_DIRS[kind] / 'releases' for kind in removed]
    for item in maintenance.cleanup_plan(registry)['components']:
        if item['kind'] not in removed or any(_inside(item['code_path'], folder) for folder in going):
            continue
        try:
            maintenance.purge_old_code(registry, item['package']['id'],
                                       expected_revision=revision(registry.read()))
        except Exception as error:
            result['leftovers'].append(item['package']['id'])
            result['notes'].append('Eski sürüm kodu silinemedi: ' + describe(error))
            print('purge ' + item['package']['id'] + ': ' + describe(error), file=sys.stderr, flush=True)
    # State (process records, package inventories) always goes with the code; user data only on request.
    for item in plan['components']:
        directory = root / KIND_DIRS[item['kind']]
        _remove_tree(Path(item['state_path']), root, result['leftovers'])
        data = Path(item['data_path'])
        if item['kind'] == 'client' or not keep_data:
            _remove_tree(data, root, result['leftovers'])
        elif data.is_dir() and _inside(data, root):
            keep_data_marker(data, item['kind'])  # A later install adopts it (secrets, identity, databases).
        if directory.is_dir():
            _remove_tree(directory / 'releases', root, result['leftovers'])
            try:
                directory.rmdir()  # Only when empty: kept data stays.
            except OSError:
                pass
    locations = (record.get('locations') or {})
    if everything and not keep_models:
        for name in ('models', 'downloads'):
            target = locations.get(name)
            if target and _inside(target, root):
                _remove_tree(target, root, result['leftovers'])
            elif target:
                result['notes'].append('Kurulum klasörü dışındaki ' + name + ' klasörü silinmedi.')
    after = registry.read()
    # Firewall: one UAC prompt at most; declined/failed never fails the uninstall.
    if firewall:
        try:
            from . import windows_firewall as default
            api = firewall_api or default
            ports = api.tcp_ports(after) if after and after['components'] else []
            result['firewall'] = api.ensure_rules(after) if ports else api.remove_rules()
        except Exception as error:
            result['firewall'] = 'failed'
            result['notes'].append('Güvenlik duvarı kuralı değiştirilemedi: ' + type(error).__name__)
    if not everything:
        # What remains keeps working under its own identity (Hub removed -> "Orion Router" links, exe, entry).
        try:
            install_entry(registry, desktop=True)
            register_entry(registry, key=entry_key)
        except Exception as error:
            result['notes'].append('Kısayollar yenilenemedi: ' + type(error).__name__)
        return result
    # Everything is gone: the entry, Startup/desktop state, background helpers and the installer-owned files.
    unregister_entry(key=entry_key)
    roots = [registry.path.parent / 'bootstrap']
    try:
        from .bootstrap_update import pointer_root
        pointer = pointer_root(registry)
        if pointer:
            roots.append(pointer)
    except Exception:
        pass
    roots += list(registry.path.parent.glob('Bootstrap-Installer*'))
    if background:
        stop_background(registry, roots)
    held = []
    for path in leftovers(root, locations, keep_data=keep_data, keep_models=keep_models):
        if not remove_tree(path):
            held.append(path)  # The running installer lives there, or a closing process still holds it.
    # What is still there after the sweep (earlier attempts may have failed only because a process was closing).
    result['leftovers'] = [str(p.relative_to(root)) for p in held]
    if held:
        result['scheduled'] = schedule_removal(held, root=root) is not None
    try:
        root.rmdir() if not any(root.iterdir()) else None
    except OSError:
        pass
    return result


def leftovers(root, locations, *, keep_data, keep_models):
    """Everything Orion owns in the root once every component is gone, minus what the user keeps: Hub/Router data
    (keep_data), backups (keep_data), Models and Downloads (keep_models). The developer registrations,
    `lifecycle-tests` and anything Orion does not recognise are never listed."""
    root = Path(root)
    kept = set()
    if keep_models:
        kept |= {locations.get('models'), locations.get('downloads')}
    if keep_data:
        kept.add(locations.get('backups'))
    kept = {canonical_path(p) for p in kept if p and _inside(p, root)}
    owned = {*ROOT_OWNED, *KIND_DIRS.values(), *(Path(p).name for p in locations.values() if p and _inside(p, root))}
    found = []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        name = path.name
        if name in NEVER_TOUCH or canonical_path(path) in kept:
            continue
        if not (name in owned or name.startswith(('Bootstrap-Installer', 'release-cache', 'install.json'))):
            continue
        if keep_data and name in (KIND_DIRS['hub'], KIND_DIRS['router']) and path.is_dir():
            found += [child for child in path.iterdir() if child.name != 'data']
            if not (path / 'data').exists():
                found.append(path)
            continue
        found.append(path)
    return found


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Orion uninstall (safe flow; see services/shared/uninstall.py)')
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--scope', choices=SCOPES, required=True)
    parser.add_argument('--delete-data', action='store_true', help='Also delete the user data of what is removed (default: keep)')
    parser.add_argument('--delete-models', action='store_true', help='Everything: also delete Models/Downloads (default: keep)')
    parser.add_argument('--no-firewall', action='store_true')
    args = parser.parse_args(argv)
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(args.registry)  # Packaged runtime lookups need it
    try:
        result = uninstall_orion(Registry(args.registry), args.scope, keep_data=not args.delete_data,
                                 keep_models=not args.delete_models, firewall=not args.no_firewall)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except UninstallError as error:
        print(json.dumps({'schema': 1, 'status': 'refused', 'message': str(error)}, ensure_ascii=True))
        return 3


if __name__ == '__main__':
    sys.exit(main())
