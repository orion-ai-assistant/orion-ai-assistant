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
import shutil
import subprocess
import sys
import time

from .component_setup import KIND_DIRS, KEPT_MARKER
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
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        leftovers.append(str(path))


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


def schedule_removal(paths, *, root):
    """Delete what this very process (running from the retained bootstrap) still holds, after it exited."""
    safe = [str(p) for p in paths if _inside(p, root) and Path(p) != Path(root) and Path(p).name not in NEVER_TOUCH]
    script = (
        '$r = $env:ORION_REMOVE_REQUEST | ConvertFrom-Json\n'
        'Start-Sleep -Seconds 3\n'
        'for ($i = 0; $i -lt 10; $i++) {\n'
        '  $left = 0\n'
        '  foreach ($p in $r.paths) { if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force '
        '-ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $p) { $left++ } } }\n'
        '  if ($left -eq 0) { break }\n'
        '  Start-Sleep -Seconds 2\n'
        '}\n'
        'try { if (-not (Get-ChildItem -LiteralPath $r.root -Force -ErrorAction Stop)) { Remove-Item -LiteralPath $r.root -Force } } catch {}\n')
    if not safe:
        return False
    try:
        subprocess.Popen([_powershell(), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script],
                         env=dict(os.environ, ORION_REMOVE_REQUEST=json.dumps({'paths': safe, 'root': str(root)})),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
        return True
    except OSError:
        return False


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
    # Code of what was removed (bounded: only these components' immutable releases).
    for item in maintenance.cleanup_plan(registry)['components']:
        if item['kind'] in removed:
            try:
                maintenance.purge_old_code(registry, item['package']['id'],
                                           expected_revision=revision(registry.read()))
            except Exception as error:
                result['leftovers'].append(item['package']['id'])
                result['notes'].append('Eski sürüm kodu silinemedi: ' + type(error).__name__)
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
    if everything and not keep_data:
        for name in ('runtime', 'backups'):
            if locations.get(name) and _inside(locations[name], root):
                _remove_tree(locations[name], root, result['leftovers'])
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
    owned = [registry.path.parent / name for name in ROOT_OWNED] + list(registry.path.parent.glob('release-cache*')) \
        + list(registry.path.parent.glob('Bootstrap-Installer*')) + [registry.path.parent / 'install.json.bak']
    held = []
    for path in dict.fromkeys(owned):
        if path.name in NEVER_TOUCH or not path.exists():
            continue
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                held.append(path)
            continue
        _remove_tree(path, root, [])
        if path.exists():
            held.append(path)  # The running installer lives there: removed right after it exits.
    if held:
        result['scheduled'] = schedule_removal(held, root=root)
    try:
        root.rmdir() if not any(root.iterdir()) else None
    except OSError:
        pass
    return result


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
