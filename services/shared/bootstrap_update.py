"""Signed self-update of the retained bootstrap.

The retained bootstrap (own Python + copies of services/shared and scripts/release)
runs update checks, the watcher and the apply/rollback helper OUTSIDE the Hub. It
used to be frozen at install time, so new update machinery never reached existing
installs. The bundle is now a signed TUF target (component `bootstrap`); the
ordinary ReleaseClient (signature, hash, rollback protection, safe extraction) is
the only trust path.

Adoption never touches the executing bootstrap:
  prepare in a fresh sibling folder -> validate -> repoint
  `bootstrap/automatic-runtime.json` atomically under the operation lock.
The previous bootstrap stays until the new one has completed one successful check;
a new bootstrap that cannot start is abandoned automatically. The trusted root is
never changed here: a bundle whose root differs from the installed one is refused
(root rotation stays an explicit, separate step).
"""
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .installation_paths import PathValidationError, canonical_path, validate_private_directory
from .installation_registry import _atomic_write, operation_lock
from .process_lifecycle import LifecycleError
from .release_packages import PackageError, sha256
from .update_gate import TERMINAL, blocked
from .update_helper import UpdateError

# The unsigned installer download (get-orion.ps1) and the signed TUF target share one stable name.
BOOTSTRAP_TARGET = 'orion-bootstrap-windows-x64.zip'
MARKER = '.orion-self-updated'
STARTUP_WAIT = 60
REQUIRED_FILES = (
    'bootstrap.json', 'orion-release.json', 'trusted-root.json', 'runtime/python/python.exe',
    'scripts/release/update_automatic.py', 'scripts/release/update_local.py',
    'scripts/release/install_new.py', 'scripts/release/manage_installation.py',
    'services/shared/automatic_updates.py', 'services/shared/bootstrap_update.py',
    'services/shared/update_helper.py', 'services/shared/release_packages.py',
    'services/shared/launcher_entry.ps1',
)


class BootstrapUpdateError(UpdateError):
    """`code` is the only text persisted in automatic.json (never paths, URLs or output)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def _write(path, value):
    canonical_path(path)
    _atomic_write(path, (json.dumps(value, ensure_ascii=True) + '\n').encode())


def _folder(registry):
    return registry.path.parent / 'bootstrap'


def pointer_root(registry):
    value = _read(_folder(registry) / 'automatic-runtime.json')
    if not isinstance(value, dict) or set(value) != {'schema', 'root'} or value['schema'] != 1:
        return None
    return Path(value['root'])


def bundle_sequence(root):
    """Sequence recorded in a bootstrap; bundles built before self-update have none (0)."""
    value = _read(Path(root) / 'bootstrap.json', {})
    sequence = value.get('sequence', 0) if isinstance(value, dict) else 0
    return sequence if type(sequence) is int and sequence >= 0 else 0


def state_path(registry):
    return _folder(registry) / 'self-update.json'


def trial(registry):
    """The unconfirmed adoption that is still the active pointer, else None."""
    value = _read(state_path(registry))
    if (isinstance(value, dict) and value.get('state') == 'trial'
            and pointer_root(registry) == Path(value['new_root'])):
        return value
    return None


def default_probe(root):
    """The new bootstrap's own interpreter must start and its scripts must import."""
    python = str(Path(root) / 'runtime/python/python.exe')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    imports = subprocess.run([python, '-B', '-c', 'import tuf.ngclient, services.shared.bootstrap_update'],
                             cwd=root, capture_output=True, timeout=120, creationflags=flags)
    if imports.returncode:
        return False
    cli = subprocess.run([python, '-B', str(Path(root) / 'scripts/release/update_automatic.py'), '--help'],
                         cwd=root, capture_output=True, timeout=120, creationflags=flags)
    return cli.returncode == 0 and b'--registry' in cli.stdout


def validate_bundle(tree, release, *, installed_root, current_root, probe=default_probe):
    """Everything a prepared tree must satisfy before it may become the active bootstrap."""
    tree = Path(tree)
    for name in REQUIRED_FILES:
        if not (tree / name).is_file():
            raise BootstrapUpdateError('BundleIncomplete')
    info = _read(tree / 'bootstrap.json')
    if (not isinstance(info, dict) or info.get('schema') != 1 or type(info.get('test_only')) is not bool
            or info.get('sequence') != release['sequence'] or info.get('package_id') != release['package_id']):
        raise BootstrapUpdateError('BundleMetadataMismatch')
    # A root change is an explicit, separate rotation, never a side effect of a bootstrap update.
    if ((tree / 'trusted-root.json').read_bytes() != Path(installed_root).read_bytes()
            or info.get('root_sha256') != sha256(tree / 'trusted-root.json')):
        raise BootstrapUpdateError('RootMismatch')
    current = _read(Path(current_root) / 'bootstrap.json', {})
    if info['test_only'] and not (isinstance(current, dict) and current.get('test_only')):
        raise BootstrapUpdateError('TestBundleOnProductionInstall')
    if not probe(tree):
        raise BootstrapUpdateError('BundleDoesNotStart')
    return info


def _active(automatic):
    try:
        tx = automatic.helper.status()
    except UpdateError:
        return True  # An unreadable journal means recovery is needed: touch nothing.
    return bool(tx and tx['phase'] not in TERMINAL) or blocked(automatic.registry.path)


def adopt(automatic, source, *, probe=default_probe):
    """Adopt a newer signed bootstrap. Returns the new root, or None when there is nothing to do."""
    registry = automatic.registry
    current = pointer_root(registry)
    if current is None:
        return None  # Source installations never have a retained bootstrap to replace.
    previous = _read(state_path(registry), {})
    if previous.get('state') == 'trial' and trial(registry):
        return None  # Never stack a second adoption on an unconfirmed one.
    if _active(automatic):
        return None  # Not in the middle of a staged/running update or recovery.
    client = automatic.new_client(source)
    _, targets = client.check([BOOTSTRAP_TARGET])
    if targets is None:
        return None  # Feed without a bootstrap target, or unreachable: the component check reports that.
    target = targets[BOOTSTRAP_TARGET]
    release = target.custom
    if release['component'] != 'bootstrap':
        raise BootstrapUpdateError('ComponentMismatch')
    installed = bundle_sequence(current)
    if release['sequence'] <= installed or release['sequence'] <= previous.get('failed_sequence', 0):
        return None
    try:
        with operation_lock(registry.path, timeout=0):
            if _active(automatic):
                return None
            destination = current.parent / f"Bootstrap-Installer-s{release['sequence']}-{uuid.uuid4().hex[:8]}"
            try:
                tree = client.prepare(target, destination)
                validate_bundle(tree, release, installed_root=_folder(registry) / 'trusted-root.json',
                                current_root=current, probe=probe)
                validate_private_directory(tree)
                (tree / MARKER).write_text('', encoding='utf-8')
                _write(state_path(registry), {
                    'schema': 1, 'state': 'trial', 'sequence': release['sequence'], 'previous_sequence': installed,
                    'previous_root': str(current), 'new_root': str(tree), 'adopted_at': automatic.clock(),
                    'started_at': None, 'discard': previous.get('discard', []),
                    'failed_sequence': previous.get('failed_sequence', 0)})
                _write(_folder(registry) / 'automatic-runtime.json', {'schema': 1, 'root': str(tree)})
            except BaseException:
                if destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                raise
            return tree
    except LifecycleError:
        return None  # Another operation owns the lock; try again at the next check.


def handover_due(registry, own_root):
    pointed = pointer_root(registry)
    return own_root is not None and pointed is not None and pointed != Path(own_root)


def note_started(registry, own_root):
    """The new bootstrap's watcher proves it started; the old watcher is waiting for this."""
    record = trial(registry)
    if record and Path(record['new_root']) == Path(own_root) and not record.get('started_at'):
        _write(state_path(registry), {**record, 'started_at': time.time()})


def rollback(registry, code, *, clock=time.time):
    """Return to the previous bootstrap; the failed sequence is not retried automatically."""
    with operation_lock(registry.path, timeout=15):
        record = _read(state_path(registry), {})
        if record.get('state') != 'trial':
            return False
        previous = Path(record['previous_root'])
        try:
            validate_private_directory(canonical_path(previous, must_exist=True))
            for name in REQUIRED_FILES[2:5]:
                canonical_path(previous / name, must_exist=True)
        except (PathValidationError, OSError):
            return False  # Nothing valid to return to: keep the record for explicit repair.
        _write(_folder(registry) / 'automatic-runtime.json', {'schema': 1, 'root': str(previous)})
        _write(state_path(registry), {
            **record, 'state': 'rolled_back', 'error': code, 'rolled_back_at': clock(),
            'failed_sequence': max(record['sequence'], record.get('failed_sequence', 0)),
            'discard': [*record.get('discard', []), record['new_root']]})
        return True


def fallback(registry, failed_root):
    """runtime_command found the unconfirmed new bootstrap unusable: use the previous one."""
    record = trial(registry)
    return bool(record and Path(record['new_root']) == Path(failed_root)
                and rollback(registry, 'BundleUnavailable'))


def supervise_handover(registry, spawn, *, wait=STARTUP_WAIT, sleep=time.sleep, clock=time.monotonic):
    """The old watcher released its lock and starts the new one; it stays to see that start.

    Returns True when the new watcher started, False after rolling back to the old runtime.
    """
    try:
        spawn()
    except Exception:
        pass  # Treated like a new watcher that never started: the wait below rolls back.
    deadline = clock() + wait
    while clock() < deadline:
        record = _read(state_path(registry), {})
        if record.get('state') == 'confirmed' or (record.get('state') == 'trial' and record.get('started_at')):
            return True
        if record.get('state') != 'trial':
            break
        sleep(1)
    if trial(registry) is not None:
        rollback(registry, 'NewBootstrapDidNotStart')
    return False


def confirm(registry, own_root):
    """One successful check from the new bootstrap: retire the previous one."""
    record = trial(registry)
    if not record or Path(record['new_root']) != Path(own_root):
        return
    _write(state_path(registry), {**record, 'state': 'confirmed', 'confirmed_at': time.time(),
                                  'discard': [*record.get('discard', []), record['previous_root']]})
    prune(registry, own_root)


def prune(registry, own_root):
    """Delete only bootstraps this mechanism created; the original install folder is kept."""
    record = _read(state_path(registry), {})
    keep, active = [], pointer_root(registry)
    for name in record.get('discard', []):
        path = Path(name)
        if not path.is_dir() or path == active or path == Path(own_root):
            continue
        if (path / MARKER).is_file() and path.parent == Path(own_root).parent:
            shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                keep.append(name)
    if keep != record.get('discard', []):
        _write(state_path(registry), {**record, 'discard': keep})


def summary(registry):
    record = _read(state_path(registry), {})
    root = pointer_root(registry)
    return {'sequence': bundle_sequence(root) if root and root.is_dir() else None,
            'state': record.get('state'), 'error': record.get('error')}
