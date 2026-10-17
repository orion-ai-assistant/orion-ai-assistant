"""Local, per-Orion-user browser profiles. Never export cookies to the API."""

import hashlib
import os
import shutil
from contextlib import contextmanager
from pathlib import Path


class ProfileBusy(Exception):
    pass


def profile_root():
    # Stable across API/worker working directories; data/ is gitignored.
    from orion.kernel.data_paths import hub_data
    return hub_data() / 'google-profiles'


def profile_dir(user_id):
    return profile_root() / hashlib.sha256(user_id.encode('utf-8')).hexdigest()


@contextmanager
def profile_lock(user_id):
    root = profile_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / (profile_dir(user_id).name + '.lock')
    with lock.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProfileBusy from exc
        try:
            yield profile_dir(user_id)
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def profile_status(user_id):
    saved = (profile_dir(user_id) / '.orion-enabled').exists()
    try:
        with profile_lock(user_id):
            busy = False
    except ProfileBusy:
        busy = True
    # A saved profile is not proof of Google authentication.
    return {'saved': saved, 'busy': busy}


def delete_profile(user_id):
    with profile_lock(user_id) as directory:
        # The exact hashed child is checked before recursive deletion.
        if directory.resolve().parent != profile_root().resolve() or directory.is_symlink():
            raise ValueError('Invalid profile directory')
        if directory.exists():
            shutil.rmtree(directory)

