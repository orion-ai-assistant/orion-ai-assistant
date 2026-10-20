"""Keep `%LOCALAPPDATA%\\Orion\\lifecycle-tests` from filling the disk.

Every acceptance run creates a ~1.3 GB sandbox (`stage05-<random>`: three unpacked packages,
a PostgreSQL cluster, logs). The rule, applied per sandbox family:

  keep  - the newest N (default 3) by modification time,
          the sandbox of the last passing FULL acceptance (that is the evidence a pending
          publish refers to: publish_release.py --accepted-result <its result.json>),
          every sandbox holding a file named PINNED,
          anything younger than one hour (it may still be running),
  delete everything else.

Manual cleanup:  accept_packages.py --prune --scratch-base <dir> [--keep N] [--dry-run] [--all-prefixes]
Only directories directly under the scratch base are considered; nothing else is touched.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

import build_cache

FAMILY = re.compile(r'^(?P<family>.+)-[a-z0-9_]{8}$')  # tempfile.mkdtemp(prefix='<family>-')
DEFAULT_FAMILY = 'stage05'
MIN_AGE_SECONDS = 3600
# NEW_PROCESS_GROUP | NO_WINDOW: a hidden console of its own, never the caller's. Not DETACHED_PROCESS: the
# venv's python.exe is a launcher that starts the real interpreter as a child, and a console program started
# from a process without any console gets a new, visible window - the cmd window that flashed after every
# acceptance run, for as long as the deletion took.
BACKGROUND = 0x00000200 | 0x08000000


def long_path(path):
    path = os.path.abspath(path)
    return '\\\\?\\' + path if os.name == 'nt' and not path.startswith('\\\\?\\') else path


def list_sandboxes(base, *, all_prefixes=False):
    """[(path, family, mtime)] of the sandbox directories directly under `base`."""
    found = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return found
    for entry in entries:
        match = FAMILY.match(entry.name)
        if not entry.is_dir(follow_symlinks=False) or not match:
            continue
        if not all_prefixes and match['family'] != DEFAULT_FAMILY:
            continue
        found.append((Path(entry.path), match['family'], entry.stat(follow_symlinks=False).st_mtime))
    return found


def protected_by_record(base, cache):
    """Sandbox of the last passing full acceptance, if it lives under `base`."""
    record = build_cache.Cache(cache).read_record('acceptance.json') if cache else None
    if not record or not record.get('result'):
        return set()
    folder = Path(record['result']).parent
    try:
        return {folder.resolve()} if folder.parent.resolve() == Path(base).resolve() else set()
    except OSError:
        return set()


def select(sandboxes, *, keep, protected=(), now=None, min_age=MIN_AGE_SECONDS):
    """(kept, doomed) lists of paths. Pure: no filesystem writes."""
    now = time.time() if now is None else now
    protected = {Path(p).resolve() for p in protected}
    kept, doomed = [], []
    families = {}
    for path, family, mtime in sandboxes:
        families.setdefault(family, []).append((mtime, path))
    for family, items in families.items():
        items.sort(key=lambda item: item[0], reverse=True)
        for position, (mtime, path) in enumerate(items):
            pinned = (path / 'PINNED').exists()
            if position < keep or pinned or path.resolve() in protected or now - mtime < min_age:
                kept.append(path)
            else:
                doomed.append(path)
    return kept, doomed


def delete_tree(path, *, workers=8):
    """Remove a directory tree; returns (bytes freed, errors). Read-only files are handled."""
    root = long_path(path)
    files, directories, freed, errors = [], [], 0, []
    stack = [root]
    while stack:
        directory = stack.pop()
        directories.append(directory)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        files.append(entry.path)
        except OSError as error:
            errors.append(f'{directory}: {error}')

    def remove(file):
        try:
            try:
                size = os.lstat(file).st_size
                os.unlink(file)
            except PermissionError:
                os.chmod(file, stat.S_IWRITE)
                size = os.lstat(file).st_size
                os.unlink(file)
            return size
        except OSError as error:
            errors.append(f'{file}: {error}')
            return 0
    with ThreadPoolExecutor(workers) as pool:
        freed = sum(pool.map(remove, files))
    for directory in reversed(directories):
        try:
            os.rmdir(directory)
        except OSError as error:
            errors.append(f'{directory}: {error}')
    return freed, errors


def prune(base, *, keep=3, protect=(), cache=None, dry_run=False, all_prefixes=False, now=None, min_age=MIN_AGE_SECONDS,
          say=print):
    protected = set(protect) | protected_by_record(base, cache)
    kept, doomed = select(list_sandboxes(base, all_prefixes=all_prefixes), keep=keep, protected=protected,
                          now=now, min_age=min_age)
    say(f'Sandbox prune in {base}: keeping {len(kept)}, {"would delete" if dry_run else "deleting"} {len(doomed)}')
    total = 0
    for path in sorted(doomed):
        if dry_run:
            say('  would delete', path.name)
            continue
        freed, errors = delete_tree(path)
        total += freed
        say(f'  deleted {path.name}: {freed / 1e9:.2f} GB' + (f' ({len(errors)} errors, e.g. {errors[0]})' if errors else ''))
    if not dry_run:
        say(f'Freed {total / 1e9:.2f} GB')
    return kept, doomed, total


def prune_in_background(base, *, keep, protect, cache):
    """Start the prune as a detached process so an acceptance run never waits for deletion."""
    argv = [sys.executable, str(Path(__file__).with_name('accept_packages.py')), '--prune',
            '--scratch-base', str(base), '--keep', str(keep), '--cache', str(cache)]
    # `protect` is passed implicitly: the running sandbox is brand new, hence inside `keep`.
    try:
        log = open(Path(base) / 'prune.log', 'ab')
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=BACKGROUND if os.name == 'nt' else 0)
    except OSError:
        pass  # pruning is housekeeping; never fail an acceptance run over it


def main(base, *, keep, cache, dry_run, all_prefixes):
    prune(Path(base), keep=keep, cache=cache, dry_run=dry_run, all_prefixes=all_prefixes)
