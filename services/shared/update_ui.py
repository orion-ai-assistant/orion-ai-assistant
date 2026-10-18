"""What the user sees while an update is applied.

While services are stopped and versions are switched the Orion app window cannot work (its Hub is
gone), so it is closed, one small standalone window says that Orion is updating (and what it is doing
right now), and when everything is finished the app window opens again together with the outcome.
The same three pieces serve "Şimdi güncelle" (retained watcher) and the automatic apply at launch.

* `updating_window` – context manager: close the app window, show the standalone window, hide it on exit;
* `write_result` / `read_result` / `mark_seen` – `updates/last-result.json`, shown once by the app;
* the window itself is `updating.ps1` (WinForms, no dependencies), started outside any Hub-owned Job.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time

from .automatic_updates import read_json, write_json
from .installation_paths import canonical_path
from .installation_registry import _atomic_write
from . import update_log

SCRIPT = 'Orion-Updating.ps1'
EXIT_MARKER = 'updating-window.exit'
RESULT = 'updates/last-result.json'
_PACKAGE = re.compile(r'^orion-(?P<kind>[a-z]+)-(?P<version>[0-9][^-]*)-')


def _disabled():
    # Sandboxes and tests never open real windows or touch real browser windows.
    return os.environ.get('ORION_UPDATE_UI') == 'off'


def _bootstrap(registry):
    return registry.path.parent / 'bootstrap'


# ---- standalone window ------------------------------------------------------------------------

def window_processes(registry):
    import psutil
    script = str(canonical_path(_bootstrap(registry) / SCRIPT)).lower()
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


def spawn_window(registry, *, popen=None, mode='updating'):
    """Show the "Orion is updating" window (`mode='quitting'`: "Orion kapanıyor"). False when it is not
    applicable or already open."""
    if os.name != 'nt' or _disabled() or window_processes(registry):
        return False
    import subprocess
    destination = canonical_path(_bootstrap(registry) / SCRIPT)
    source = Path(__file__).with_name('updating.ps1').read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != source:
        _atomic_write(destination, source)
    (_bootstrap(registry) / EXIT_MARKER).unlink(missing_ok=True)
    (popen or subprocess.Popen)(
        ['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
         '-File', str(destination), '-Registry', str(registry.path), *(['-Mode', mode] if mode != 'updating' else [])],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    return True


def stop_window(registry, *, timeout=6):
    if os.name != 'nt':
        return 0
    import psutil
    victims = window_processes(registry)
    if not victims:
        return 0
    marker = _bootstrap(registry) / EXIT_MARKER
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


def close_app_window(registry):
    """Close Orion's own app window (never the user's normal browser windows)."""
    if _disabled():
        return 0
    from .launcher import close_window
    try:
        return close_window(registry)
    except Exception as error:  # A window that stays open is cosmetic; the update goes on.
        update_log.log_exception(registry.path.parent / 'updates', 'close_app_window', error)
        return 0


@contextmanager
def updating_window(registry, *, close_app=True, spawn=spawn_window, stop=stop_window, close=close_app_window):
    """Show the updating window for the duration. Never raises because of the window itself."""
    base = registry.path.parent / 'updates'
    try:
        spawn(registry)
        if close_app:
            close(registry)
    except Exception as error:
        update_log.log_exception(base, 'updating_window.open', error)
    try:
        yield
    finally:
        try:
            stop(registry)
        except Exception as error:
            update_log.log_exception(base, 'updating_window.close', error)


# ---- result message ---------------------------------------------------------------------------

def _components(record):
    result = {}
    for item in (record or {}).get('components', []):
        package = (item.get('package') or {}).get('id', '')
        match = _PACKAGE.match(package)
        result[item['kind']] = match.group('version') if match else package
    return result


def summarize(tx, outcome, error):
    """(status, Turkish message) for the user after an apply attempt."""
    if outcome == 'completed' and not error:
        proposed = _components((tx or {}).get('proposed'))
        changed = (tx or {}).get('changes') or list(proposed)
        text = ', '.join(f'{kind} {proposed[kind]}' for kind in sorted(changed) if kind in proposed)
        return 'completed', 'Güncellendi: ' + text + '.' if text else 'Güncellendi.'
    reason = update_log.describe(error or outcome)
    if outcome == 'repair_required':
        return 'repair_required', 'Güncelleme uygulanamadı; yerel onarma gerekli. ' + (reason or '')
    status = 'rolled_back' if outcome == 'rolled_back' else 'failed'
    return status, 'Güncelleme uygulanamadı, önceki sürümle devam ediliyor.' + (' ' + reason if reason else '')


def write_result(registry, tx, outcome, error, *, clock=time.time):
    status, message = summarize(tx, outcome, error)
    try:
        write_json(registry.path.parent / RESULT, {
            'schema': 1, 'status': status, 'message': message, 'at': clock(), 'seen': False,
            'operation_id': (tx or {}).get('operation_id')})
    except OSError as failure:
        update_log.log_exception(registry.path.parent / 'updates', 'write_result', failure)
    return status, message


def read_result(registry):
    try:
        value = read_json(registry.path.parent / RESULT, None)
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get('schema') != 1
            or value.get('status') not in {'completed', 'rolled_back', 'failed', 'repair_required'}):
        return None
    return {key: value.get(key) for key in ('status', 'message', 'at', 'seen')}


def mark_seen(registry):
    path = registry.path.parent / RESULT
    value = read_json(path, None)
    if isinstance(value, dict) and value.get('schema') == 1 and not value.get('seen'):
        write_json(path, {**value, 'seen': True})
    return read_result(registry)


def phase_text(phase):
    """Turkish description of the step in progress; the window shows the same strings."""
    return {
        'stopping': 'Servisler kapatılıyor…',
        'draining': 'Geçiş hazırlanıyor…',
        'backing_up': 'Veriler yedekleniyor…',
        'applying': 'Yeni sürüm yerleştiriliyor…',
        'switching': 'Yeni sürüm yerleştiriliyor…',
        'health_check': 'Yeni sürüm denetleniyor…',
        'rollback_pending': 'Önceki sürüme dönülüyor…',
        'restarting': 'Orion yeniden başlatılıyor…',
    }.get(phase, 'Güncelleme sürüyor…')


@contextmanager
def quitting_window(registry, *, spawn=spawn_window, stop=stop_window):
    """A launch that waits for a running quit: same window, "Orion kapanıyor, birazdan açılacak"."""
    base = registry.path.parent / 'updates'
    try:
        spawn(registry, mode='quitting')
    except Exception as error:
        update_log.log_exception(base, 'quitting_window.open', error)
    try:
        yield
    finally:
        try:
            stop(registry)
        except Exception as error:
            update_log.log_exception(base, 'quitting_window.close', error)
