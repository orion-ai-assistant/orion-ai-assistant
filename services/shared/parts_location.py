"""Where the parts' models are, and moving them (Ayarlar > Bileşenler: "Modeller: <yol> · X GB · Değiştir").

The place is `local_parts.location()`: an installed Orion's models location (install.json), a source tree's own.
Moving it is a job in a process of its own, like a download (`python -m services.shared.parts_location move
<path>`): the running parts stop, content_migration copies every file, checks the copy and the source by their
SHA-256 and switches the place in the registry, then the parts start again from the new place. Nothing may
start, download or delete meanwhile (`local_parts.moving`). Once the copy is checked and the place switched, the
old folder goes (only a file that cannot be deleted keeps it, and the page then offers to delete it); a move that
fails or is cut short never touches it: the old place stays in use, and only what the move copied goes.

    <parts home>/jobs/move.json   phase (queued, stopping, scan, copy, verify, done, error), bytes done of total
                                  in this phase, speed, source, destination; an old folder a move could not
                                  delete (`old`) until the user deletes or keeps it

Like the rest of the page's status these are facts only: the page words the progress and works out whether the
models fit where the user points.
"""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from . import local_parts as P
from .content_migration import (MARKER, MigrationError, begin_content_migration, registry_revision, reporting,
                                operation_lock)
from .installation_paths import PathValidationError, canonical_path, paths_overlap, resolve_location, \
    validate_destination
from .installation_registry import new_registry, normalized_path

SUBFOLDER = 'Orion Models'    # Where the models go in a folder the user picked that already holds other files.
WORDS = (('fixed local disks', 'Yalnızca bu bilgisayarın kendi diskleri seçilebilir (ağ ya da çıkarılabilir disk '
                               'olmaz).'),
         ('NTFS or ReFS', 'Bu disk NTFS değil; NTFS bir disk seç.'),
         ('Network and device paths', 'Ağ klasörleri seçilemez.'),
         ('same or nested', 'Yeni yer şimdiki yerin içinde olamaz, onu da içeremez.'),
         ('another registered data location', 'Bu klasör Orion\'un başka bir klasörüyle çakışıyor.'),
         ('Target is occupied', 'Bu klasör dolu; boş bir klasör seç.'),
         ('not writable', 'Bu klasöre yazılamıyor.'),
         ('Symlink and junction', 'Bağlantı (junction) klasörler seçilemez.'),
         ('absolute path', 'Tam bir yol yaz (ör. D:\\Orion Models).'),
         ('Unsupported Windows path segment', 'Bu adda klasör olamaz.'),
         ('Insufficient target disk space', 'Hedefte yer yetmiyor.'),
         ('ordinary, unlinked files only', 'Modellerin klasöründe bağlantılı (kısayol) dosya var; taşınamadı.'),
         ('changed during', 'Taşınırken modellerin klasörü değişti; yeniden dene.'),
         ('hash/inventory mismatch', 'Kopya ile asıl dosyalar aynı çıkmadı; eski yer kullanılmaya devam ediyor.'),
         ('inaccessible or missing', 'Bu disk ya da klasör bulunamadı.'),
         ('Target disk is missing', 'Bu disk bulunamadı.'),
         ('not a directory', 'Bu yol bir dosyaya çıkıyor; bir klasör seç.'),
         ('Errno 28', 'Taşınamadı: hedefte yer kalmadı.'))


def _say(error, fallback='Taşınamadı'):
    text = str(error)
    for needle, words in WORDS:
        if needle in text:
            return words
    if isinstance(error, OSError):
        return f'{fallback}: {error.strerror or error}.'
    return f'{fallback} ({text}).'


def _job_path():
    return P.home() / 'jobs' / 'move.json'


def _update(**values):
    path = _job_path()
    P._write_json(path, {**P._read_json(path, {}), **values})


def _size(folder):
    total = 0
    for current, _, names in os.walk(folder):
        for name in names:
            try:
                total += os.stat(os.path.join(current, name)).st_size
            except OSError:
                pass
    return total


def job():
    """The running or last move (None: never). One cut short (Orion closed) says so."""
    value = P._read_json(_job_path(), None)
    if value and value.get('phase') not in P.TERMINAL and not P.moving():
        value = {**value, 'phase': 'interrupted',
                 'error': 'Taşıma yarıda kaldı (Orion kapandı ya da yeniden başladı). Yeniden dene.'}
    return value


def facts():
    """Where the models are and what they take, the move (running or last), and old folders a move could not
    delete."""
    registry, place, _ = P.location()
    value = job()
    return {'path': str(place), 'bytes': _size(place), 'movable': registry is not None,
            'move': {key: item for key, item in value.items() if key != 'old'} if value else None,
            'old': [{'path': old, 'bytes': _size(old)} for old in (value or {}).get('old') or [] if Path(old).is_dir()]}


def _record(registry, place):
    """The registry's record; a source tree's own made up (not written) until its first move."""
    record = registry.read()
    if record is None:
        record = new_registry(normalized_path(str(P.home().resolve())))
        record['locations'] = {'models': str(canonical_path(place))}
    return record


def check(path):
    """Where the models would go if the user picks `path` (into a folder that already holds files: its
    `Orion Models`), that disk's free space, and why it cannot be (in Turkish; None: it can)."""
    registry, place, _ = P.location()
    text = str(path or '').strip().strip('"')
    target, free = text, None
    try:
        if registry is None:
            raise P.PartError('Bu kurulumda modellerin yeri değiştirilemiyor.')
        picked = canonical_path(text)
        if picked == canonical_path(place):
            raise P.PartError('Modeller zaten burada.')
        target = picked / SUBFOLDER if picked.is_dir() and any(picked.iterdir()) else picked
        nearest = next((item for item in (target, *target.parents) if item.is_dir()), None)
        free = shutil.disk_usage(nearest).free if nearest else None
        if paths_overlap(target, place):
            raise P.PartError('Yeni yer şimdiki yerin içinde olamaz, onu da içeremez.')
        from .content_migration import _occupied
        validate_destination(target, occupied_paths=_occupied(_record(registry, place), 'models'))
        problem = None
    except P.PartError as error:
        problem = str(error)
    except (PathValidationError, OSError) as error:
        problem = _say(error, 'Bu klasör kullanılamıyor')
    return {'path': str(target), 'free_bytes': free, 'problem': problem}


# --- Choosing a folder on the Hub PC ------------------------------------------------------------------------

_picks = {}
_picking = threading.Lock()


def pick():
    """Open a folder dialog on the Hub PC (in front of the Orion window) and answer at once with its id; the
    choice shows in `picked(id)` when the user closes it. One dialog at a time: asking again answers the same."""
    with _picking:
        for key, value in _picks.items():
            if not value['done']:
                return key
        key = uuid.uuid4().hex
        _picks[key] = {'done': False, 'path': None}
    _, place, _ = P.location()
    start = next((str(item) for item in (place, *place.parents) if item.is_dir()), '')

    def wait():
        try:
            result = subprocess.run([sys.executable, '-B', '-m', 'services.shared.parts_location', 'pick', start],
                                    cwd=str(P.ROOT), env=dict(os.environ, PYTHONPATH=str(P.ROOT), PYTHONIOENCODING='utf-8'),
                                    capture_output=True, text=True, encoding='utf-8', timeout=3600,
                                    creationflags=P.NO_WINDOW)
            chosen = result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            chosen = None
        _picks[key] = {'done': True, 'path': chosen, 'check': check(chosen) if chosen else None}

    threading.Thread(target=wait, daemon=True).start()
    return key


def picked(key):
    value = _picks.get(key)
    if value is None:
        raise P.PartError('Bu klasör seçimi bitti.')
    return value


TITLE = 'Modellerin yeni yeri'


def choose_folder(start=''):
    """Windows' own folder dialog (the one Explorer has); the chosen folder or None."""
    import ctypes
    from ctypes import wintypes
    ole = ctypes.OleDLL('ole32')
    ole.CoInitializeEx(None, 2)  # Apartment threaded, as a dialog needs.

    class GUID(ctypes.Structure):
        _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16), ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]

    def guid(text):
        value = GUID()
        ole.CLSIDFromString(text, ctypes.byref(value))
        return value

    def call(item, index, *args, types=()):
        table = ctypes.cast(item, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *types)(table[index])(item, *args)

    dialog = ctypes.c_void_p()
    ole.CoCreateInstance(ctypes.byref(guid('{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}')), None, 1,
                         ctypes.byref(guid('{d57c7288-d4ad-4768-be02-9d969532d960}')), ctypes.byref(dialog))
    options = wintypes.DWORD()
    call(dialog, 10, ctypes.byref(options), types=(ctypes.POINTER(wintypes.DWORD),))  # GetOptions
    # Folders only, on the file system, that exist.
    call(dialog, 9, wintypes.DWORD(options.value | 0x20 | 0x40 | 0x800), types=(wintypes.DWORD,))
    call(dialog, 17, TITLE, types=(wintypes.LPCWSTR,))  # SetTitle
    call(dialog, 18, 'Seç', types=(wintypes.LPCWSTR,))  # SetOkButtonLabel
    if start:
        folder = ctypes.c_void_p()
        try:
            ctypes.OleDLL('shell32').SHCreateItemFromParsingName(
                start, None, ctypes.byref(guid('{43826d1e-e718-42ee-bc55-a1e261c37bfe}')), ctypes.byref(folder))
            call(dialog, 12, folder, types=(ctypes.c_void_p,))  # SetFolder
        except OSError:
            pass
    threading.Thread(target=P.to_front, args=(lambda name, title: name == '#32770' and title == TITLE, 10),
                     daemon=True).start()
    try:
        call(dialog, 3, None, types=(wintypes.HWND,))  # Show; cancelling raises
    except OSError:
        return None
    item, text = ctypes.c_void_p(), wintypes.LPWSTR()
    call(dialog, 20, ctypes.byref(item), types=(ctypes.POINTER(ctypes.c_void_p),))  # GetResult
    call(item, 5, wintypes.DWORD(0x80058000), ctypes.byref(text),  # GetDisplayName: its file system path
         types=(wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR)))
    try:
        return text.value
    finally:
        ole.CoTaskMemFree(text)


# --- Moving --------------------------------------------------------------------------------------------------

def _parts():
    return [part for part in P.catalog()['parts'] if part.get('engine')]


def start_move(path):
    """Begin moving the models to `path` (as `check` takes it): refused before anything stops when it cannot be,
    or when the models would not fit there."""
    registry, place, _ = P.location()
    if P.moving():
        raise P.PartError('Modeller zaten taşınıyor.')
    for part in _parts():
        if P.busy(part['id']):
            raise P.PartError(f'{part["title"]}: bir indirme sürüyor; bitince taşı.')
        change = P.transition(part['id'])
        if change and not change.get('error'):
            raise P.PartError(f'{part["title"]} başlıyor ya da duruyor; bitince taşı.')
    target = check(path)
    if target['problem']:
        raise P.PartError(target['problem'])
    size = _size(place)
    if target['free_bytes'] is not None and target['free_bytes'] < size:
        raise P.PartError(f'Hedefte yer yetmiyor: {size / 1073741824:.1f} GB gerekli, '
                          f'{target["free_bytes"] / 1073741824:.1f} GB boş.')
    previous = P._read_json(_job_path(), {}) or {}
    P._write_json(_job_path(), {'phase': 'queued', 'started': time.time(), 'pid': None, 'source': str(place),
                                'destination': target['path'], 'done': 0, 'total': size, 'speed': None,
                                'error': None, 'old': previous.get('old') or []})
    process = P._spawn(['move', target['path']], module='services.shared.parts_location')
    _update(pid=process.pid)
    return job()


class _Progress:
    """What content_migration reads and copies, as the phase the page shows: `scan` (every source file's
    SHA-256 first), `copy` (the bytes copied; each copied file is checked as it goes), `verify` (source and copy
    read once more before the switch and once more right at it: four times the size)."""
    def __init__(self, size):
        self.size, self.phase, self.done, self.at = size, 'scan', 0, 0.0
        self.recent = deque()
        self._write(force=True)

    def __call__(self, kind, size):
        if (kind == 'copy') == (self.phase == 'copy'):
            self.done += size
        self._write()  # Checking a copied file counts no bytes, but its time is in the speed.

    def boundary(self, phase, journal):
        if phase == 'copying':
            copied = len(journal['copied_files']) == len(journal['inventory']['files'])
            phase = 'verify' if copied else 'copy'
        elif phase in ('verified', 'switching'):
            phase = 'verify'
        else:
            return
        if phase != self.phase:
            self.phase, self.done = phase, 0
            self.recent.clear()
            self._write(force=True)

    def _write(self, force=False):
        now = time.monotonic()
        self.recent.append((now, self.done))
        while now - self.recent[0][0] > 5:
            self.recent.popleft()
        if not force and now - self.at < .5:
            return
        self.at = now
        total = self.size * (4 if self.phase == 'verify' else 1)
        first, span = self.recent[0], now - self.recent[0][0]
        _update(phase=self.phase, done=min(self.done, total), total=total,
                speed=round((self.done - first[1]) / span) if span >= 1 else None)


def _quiet():
    """No part runs, starts or downloads (content_migration asks before every file and at the switch)."""
    for part in _parts():
        port = int(os.getenv(part['port_key']) or part['port'])
        if P.busy(part['id']) or P._supervised(part['id']) or P.answers(port):
            return False
    return True


def _settle(registry):
    """Moves cut short earlier (Orion closed meanwhile), so none blocks the next: one that already switched the
    place is finished (its copy was checked before the switch); one that did not is undone (only what it
    copied goes, never a folder it did not make)."""
    folder = registry.path.parent / 'migrations'
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
        try:
            journal = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if journal.get('phase') == 'completed':
            continue
        destination = Path(journal['destination'])
        record = registry.read()
        if record and resolve_location(record, 'models') == canonical_path(destination):
            P._write_json(path, {**journal, 'phase': 'completed'})
            continue
        marker = destination / MARKER
        try:
            ours = json.loads(marker.read_text(encoding='utf-8')).get('operation_id') == journal['operation_id']
        except (OSError, ValueError):
            ours = False
        if ours:
            shutil.rmtree(destination, ignore_errors=True)
        (folder / 'undone').mkdir(exist_ok=True)
        os.replace(path, folder / 'undone' / path.name)


def _switch(registry, place, destination):
    """No models yet: the place changes, nothing to copy."""
    with operation_lock(registry):
        record = _record(registry, place)
        record['locations'] = {**record.get('locations', {}), 'models': str(destination)}
        Path(destination).mkdir(parents=True, exist_ok=True)
        registry.write(record)


def _gone(folder):
    """Delete the old place after a successful move (nothing holds it: the parts are stopped); False when a
    file stays (the page then offers to delete it)."""
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f'old models folder {folder} stays: {type(error).__name__}: {error}', flush=True)
        return False
    return True


def run_move(destination):
    """The move's own process: stop the parts, move, start again the ones that ran."""
    _update(pid=os.getpid(), phase='stopping')
    config = P.read_config()
    restart = [part['id'] for part in _parts() if config.get(part['id'], {}).get('enabled')
               and not config[part['id']].get('paused')]
    for part in _parts():
        P.stop_part(part['id'])
    registry, place, _ = P.location()
    destination = str(canonical_path(destination))
    old, error = None, None
    try:
        until = time.monotonic() + 30
        while not _quiet():
            if time.monotonic() > until:
                raise P.PartError('Bir bileşen durmadı; taşınamadı.')
            time.sleep(.5)
        if registry.read() is None:
            registry.path.parent.mkdir(parents=True, exist_ok=True)
            registry.write(_record(registry, place))
        with operation_lock(registry):
            _settle(registry)
        if not place.is_dir():
            _switch(registry, place, destination)
        else:
            progress = _Progress(_size(place))
            with reporting(progress):
                begin_content_migration(registry, 'models', destination,
                                        expected_revision=registry_revision(registry.read()),
                                        quiescence_gate=_quiet, fault=progress.boundary)
            old = None if _gone(place) else str(place)
    except Exception as failure:
        error = failure.args[0] if isinstance(failure, P.PartError) else _say(failure)
        print(f'models move to {destination} failed: {type(failure).__name__}: {failure}', flush=True)
        try:
            with operation_lock(registry):
                _settle(registry)
        except Exception as cleanup:
            print(f'models move cleanup failed: {type(cleanup).__name__}: {cleanup}', flush=True)
    olds = P._read_json(_job_path(), {}).get('old') or []
    _update(phase='error' if error else 'done', error=error, speed=None, finished=time.time(),
            old=olds + [old] if old else olds)
    for part_id in restart:
        P._move(part_id, 'running')
    return 1 if error else 0


def _forget(path):
    _update(old=[old for old in P._read_json(_job_path(), {}).get('old') or [] if old != path])


def drop_old(path):
    """Delete an old copy a move left (the user said so). Only one a move left, never the place in use."""
    if path not in (P._read_json(_job_path(), {}) or {}).get('old', []):
        raise P.PartError('Böyle bir eski kopya yok.')
    if P.moving():
        raise P.PartError('Modeller taşınıyor; bitince sil.')
    _, place, _ = P.location()
    if paths_overlap(path, place):
        _forget(path)
        raise P.PartError('Bu klasör şimdi kullanılıyor; silinmez.')
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        raise P.PartError('Eski kopyanın bir dosyası açık; birazdan yeniden dene.') from None
    _forget(path)


def keep_old(path):
    """Keep an old copy where it is: the page stops asking (its files stay)."""
    _forget(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('move').add_argument('destination')
    commands.add_parser('pick').add_argument('start', nargs='?', default='')
    args = parser.parse_args(argv)
    if args.command == 'pick':
        chosen = choose_folder(args.start)
        if chosen:
            sys.stdout.write(chosen)
        return 0
    return run_move(args.destination)


if __name__ == '__main__':
    raise SystemExit(main())
