"""Shared installation location policy; validation never creates a new install.

Descriptions from install.json do not grant ownership. Callers must separately
authorize managed actions and hold the installation operation lock during moves.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .installation_registry import validate_record


class PathValidationError(ValueError):
    pass


LOCATION_DEFAULTS = {'models': 'Models', 'downloads': 'Downloads', 'runtime': 'Runtime', 'backups': 'backups'}


def _lexical_path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise PathValidationError('An absolute path is required')
    value = os.fspath(value)
    if not isinstance(value, str) or not value or '\x00' in value:
        raise PathValidationError('Invalid path')
    path = Path(value)
    if not path.is_absolute():
        raise PathValidationError('An absolute path is required')
    if '..' in path.parts:
        raise PathValidationError('Parent traversal is not permitted')
    if os.name == 'nt':
        if value.startswith(('\\\\', '//')):
            raise PathValidationError('Network and device paths are unsupported')
        for part in path.parts[1:]:
            if (part.endswith((' ', '.')) or any(c in part for c in '<>:"|?*')
                    or any(ord(c) < 32 for c in part)
                    or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}):
                raise PathValidationError('Unsupported Windows path segment')
    return path


def _no_links(path):
    for part in reversed([path, *path.parents]):
        try:
            details = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PathValidationError('Path is inaccessible') from exc
        if stat.S_ISLNK(details.st_mode) or getattr(details, 'st_file_attributes', 0) & 0x400:
            raise PathValidationError('Symlink and junction paths are unsupported')


def canonical_path(value, *, must_exist=False):
    path = _lexical_path(value)
    _no_links(path)
    try:
        canonical = path.resolve(strict=must_exist)
        canonical.anchor and Path(canonical.anchor).stat()
    except OSError as exc:
        raise PathValidationError('Path or disk is inaccessible or missing') from exc
    return canonical


def _fixed_local_disk(path):
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(path.anchor) != 3:
        raise PathValidationError('Only available fixed local disks are supported')
    filesystem = ctypes.create_unicode_buffer(261)
    if not kernel.GetVolumeInformationW(path.anchor, None, 0, None, None, None, filesystem, len(filesystem)):
        raise PathValidationError('Cannot verify target filesystem')
    if filesystem.value.upper() not in {'NTFS', 'REFS'}:
        raise PathValidationError('Target requires NTFS or ReFS permissions')


def _nearest_directory(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PathValidationError('Target is inaccessible') from exc
        if not stat.S_ISDIR(info.st_mode):
            raise PathValidationError('Target or its ancestor is not a directory')
        return candidate
    raise PathValidationError('Target disk is missing')


def _probe_writable(directory):
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix='.orion-write-check-', delete=False) as stream:
            name = Path(stream.name)
            stream.write(b'Orion location write check\n')
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise PathValidationError('Target is not writable') from exc
    finally:
        if name is not None:
            try:
                name.unlink()
            except OSError as exc:
                raise PathValidationError('Target write probe cleanup failed') from exc


def _windows_acl(directory):
    """Read owner/DACL/current token SID with trusted Windows APIs only."""
    import ctypes
    from ctypes import wintypes as W
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ptr = ctypes.c_void_p
    api.GetNamedSecurityInfoW.argtypes = [W.LPWSTR, W.DWORD, W.DWORD, ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr)]
    api.GetNamedSecurityInfoW.restype = W.DWORD
    api.GetAce.argtypes = [ptr, W.DWORD, ctypes.POINTER(ptr)]
    api.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(W.LPWSTR)]
    api.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
    api.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ptr, W.DWORD, ctypes.POINTER(W.DWORD)]
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.LocalFree.argtypes = [ptr]
    descriptor, dacl, owner = ptr(), ptr(), ptr()
    token = W.HANDLE()

    def sid_string(pointer):
        text = W.LPWSTR()
        if not api.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
            raise PathValidationError('Cannot inspect private directory SID')
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ptr))

    try:
        if api.GetNamedSecurityInfoW(str(directory), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor)):
            raise PathValidationError('Cannot verify private directory permissions')
        if not owner.value or not dacl.value:
            raise PathValidationError('Private directory has unrestricted permissions')
        if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise PathValidationError('Cannot inspect current user identity')
        needed = W.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not api.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(needed)):
            raise PathValidationError('Cannot inspect current user identity')
        current = sid_string(ptr.from_buffer(buffer))
        rules = []
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        for index in range(count):
            ace = ptr()
            if not api.GetAce(dacl, index, ctypes.byref(ace)):
                raise PathValidationError('Cannot inspect private directory access rule')
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind in {5, 9, 11}:  # object/callback allow: fail closed, no heuristic parsing
                raise PathValidationError('Unsupported private directory access rule')
            if kind not in {0, 1}:
                continue
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            rules.append({'sid': sid_string(ptr(ace.value + 8)), 'rights': mask, 'allow': kind == 0})
        return {'current_sid': current, 'owner_sid': sid_string(owner), 'rules': rules}
    finally:
        if token.value:
            kernel.CloseHandle(token)
        if descriptor.value:
            kernel.LocalFree(descriptor)


def _validate_windows_acl(record):
    try:
        own_sid = record['current_sid']
        if not isinstance(own_sid, str) or not own_sid.startswith('S-1-'):
            raise ValueError()
        allowed = {own_sid, 'S-1-5-18', 'S-1-5-32-544'}  # current user, SYSTEM, Administrators
        if record['owner_sid'] not in allowed:
            raise PathValidationError('Private directory is owned by another user')
        write_mask = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
        if not isinstance(record['rules'], list) or not record['rules']:
            raise ValueError()
        for rule in record['rules']:
            if (not isinstance(rule['sid'], str) or type(rule['allow']) is not bool
                    or type(rule['rights']) is not int):
                raise ValueError()
            # Conservatively reject broad Allow even if another Deny exists;
            # we never attempt to rewrite or simplify the user's ACL.
            if rule['allow'] and rule['rights'] & write_mask and rule['sid'] not in allowed:
                raise PathValidationError('Private directory is writable by another user or group')
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PathValidationError):
            raise
        raise PathValidationError('Cannot verify private directory permissions') from exc


def validate_private_directory(path):
    """Reject other-user writable executable/key locations without changing ACLs.

    For a missing destination inspect the nearest existing parent's inherited
    policy. Installers must recheck the actual directory after creating it.
    Shared model/download pools deliberately use ordinary destination validation.
    """
    path = canonical_path(path)
    parent = _nearest_directory(path)
    if os.name == 'nt':
        _validate_windows_acl(_windows_acl(parent))
    else:
        details = parent.stat()
        if details.st_uid not in {0, os.getuid()} or details.st_mode & 0o022:
            raise PathValidationError('Private directory permits writes by another user or group')
    return path


def paths_overlap(first, second):
    first, second = canonical_path(first), canonical_path(second)
    return first == second or first in second.parents or second in first.parents


@dataclass(frozen=True)
class DestinationCheck:
    path: Path
    free_bytes: int
    existing: bool


def validate_destination(destination, *, source=None, occupied_paths=(), required_bytes=0, check_writable=True, private=False):
    """Validate an empty destination, disk capacity and source/path isolation.

    No directory is created. A short uniquely named probe is removed from the
    nearest existing parent. Copy code must recheck links and reserve exclusively
    while holding its operation lock because paths can change after validation.
    """
    if type(required_bytes) is not int or required_bytes < 0:
        raise PathValidationError('Required disk space must be a nonnegative integer')
    destination = canonical_path(destination)
    _fixed_local_disk(destination)
    if source is not None:
        source = canonical_path(source, must_exist=True)
        if paths_overlap(source, destination):
            raise PathValidationError('Source and target cannot be the same or nested')
    for occupied in occupied_paths:
        if paths_overlap(destination, occupied):
            raise PathValidationError('Target overlaps another registered data location')
    parent = _nearest_directory(destination)
    existing = parent == destination
    if existing:
        try:
            if next(destination.iterdir(), None) is not None:
                raise PathValidationError('Target is occupied; existing files are never overwritten')
        except OSError as exc:
            raise PathValidationError('Target is inaccessible') from exc
    try:
        available = shutil.disk_usage(parent).free
    except OSError as exc:
        raise PathValidationError('Cannot inspect available disk space') from exc
    if available < required_bytes:
        raise PathValidationError('Insufficient target disk space')
    if private:
        validate_private_directory(destination)
    if check_writable:
        _probe_writable(parent)
    return DestinationCheck(destination, available, existing)


def resolve_location(record, name):
    if name not in LOCATION_DEFAULTS:
        raise PathValidationError('Unsupported shared installation location')
    record = validate_record(record)
    return canonical_path(record.get('locations', {}).get(name, str(Path(record['root']) / LOCATION_DEFAULTS[name])))


def registered_component_paths(record, kind, *, distribution='installed'):
    """Resolve a unique recorded component; never infer it from a shared root."""
    record = validate_record(record)
    if distribution not in {'installed', 'development'}:
        raise PathValidationError('Component selection mode must be explicit')
    choices = [item for item in record['components'] if item['kind'] == kind
               and (item['distribution'] == 'development') == (distribution == 'development')]
    if len(choices) != 1:
        raise PathValidationError('A unique registered component is required')
    component = choices[0]
    if component['distribution'] in {'remote', 'docker'}:
        raise PathValidationError('Component does not support native local paths')
    return {field: canonical_path(component[field]) for field in ('code_path', 'data_path', 'state_path')}
