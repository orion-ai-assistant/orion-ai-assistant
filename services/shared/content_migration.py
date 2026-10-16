"""Conservative Models/Downloads copy-and-switch with a persistent recovery journal.

Source files are never deleted. Runtime, source code, Router, database, identity and
browser-profile migration are deliberately unsupported. Callers must hold consumers
and download writers quiescent for the entire operation (including recovery).
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from uuid import UUID, uuid4

KINDS = {'models', 'downloads'}
PHASES = {'planned', 'copying', 'verified', 'switching', 'completed', 'repair-required'}
MARKER = '.orion-content-migration.json'


class MigrationError(RuntimeError):
    """Preserve both trees and the journal; explicit repair may be required."""


def registry_revision(record):
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def operation_lock(registry):
    """Shared with installation changes and future update/move orchestration."""
    from services.shared.installation_registry import operation_lock as lock
    return lock(registry.path)


def _idle(gate):
    if not callable(gate) or gate() is not True:
        raise MigrationError('Content ownership is active or unknown; quiescence is required')


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MigrationError('Journal cannot be a symlink')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.migration-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _boundary(path, journal, phase, fault):
    journal['phase'] = phase
    _write(path, journal)
    if fault is not None:
        fault(phase, deepcopy(journal))


def _canonical(path, *, must_exist=False):
    from services.shared.installation_paths import canonical_path
    return canonical_path(str(path), must_exist=must_exist)


def _relative(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or path.is_absolute()
            or '..' in path.parts or '.' in path.parts or '\\' in name or ':' in name
            or str(path) != name):
        raise MigrationError('Unsafe inventory path')
    return Path(*path.parts)


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root, *, target=False, operation_id=None):
    _canonical(root, must_exist=True)
    files, directories = {}, []
    def inaccessible(error):
        raise MigrationError('Content directory is inaccessible') from error
    for current, subdirectories, names in os.walk(root, followlinks=False, onerror=inaccessible):
        parent = Path(current)
        for name in subdirectories + names:
            path = parent / name
            _canonical(path, must_exist=True)  # Reject every junction/reparse point.
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                directories.append(relative)
                continue
            if target and relative == MARKER:
                continue
            if relative == MARKER or name.startswith('.orion-partial-'):
                raise MigrationError('Reserved migration path in content tree')
            metadata = path.stat()
            if not path.is_file() or metadata.st_nlink != 1:
                raise MigrationError('Content must contain ordinary, unlinked files only')
            before = (metadata.st_size, metadata.st_mtime_ns)
            digest = _hash(path)
            after = path.stat()
            if before != (after.st_size, after.st_mtime_ns):
                raise MigrationError('Source changed during inventory')
            files[relative] = {'size': metadata.st_size, 'sha256': digest}
    return {'files': dict(sorted(files.items())), 'directories': sorted(directories)}


def _matches(root, inventory, *, target=False, operation_id=None):
    if target:
        marker = _canonical(root / MARKER, must_exist=True)
        value = json.loads(marker.read_text(encoding='utf-8'))
        if value.get('operation_id') != operation_id:
            raise MigrationError('Destination ownership marker changed')
    if _inventory(root, target=target, operation_id=operation_id) != inventory:
        raise MigrationError('Content hash/inventory mismatch; preserve both trees for repair')


def _occupied(record, kind):
    from services.shared.installation_paths import resolve_location
    paths = [resolve_location(record, name) for name in ('models', 'downloads', 'runtime', 'backups') if name != kind]
    for component in record['components']:
        if component['distribution'] != 'remote':
            paths.extend(Path(component[field]) for field in ('code_path', 'data_path', 'state_path'))
    return paths


def _claim_target(destination, journal):
    marker = destination / MARKER
    identity = {'operation_id': journal['operation_id'], 'installation_id': journal['installation_id']}
    if marker.exists():
        _canonical(marker, must_exist=True)
        if json.loads(marker.read_text(encoding='utf-8')) != identity:
            raise MigrationError('Destination belongs to another migration')
        return
    if destination.exists() and any(destination.iterdir()):
        raise MigrationError('Destination contains user files; collision')
    destination.mkdir(parents=True, exist_ok=True)
    # Exclusive creation; never overwrite a marker from another operation.
    with marker.open('x', encoding='utf-8') as stream:
        json.dump(identity, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(destination)


def _copy_files(source, destination, journal, path, gate, fault):
    inventory = journal['inventory']
    for name in inventory['directories']:
        relative = _relative(name)
        target = _canonical(destination / relative)
        target.mkdir(exist_ok=True, parents=True)
    for name, expected in inventory['files'].items():
        _idle(gate)
        relative = _relative(name)
        original = _canonical(source / relative, must_exist=True)
        target = _canonical(destination / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != expected['size'] or _hash(target) != expected['sha256']:
                raise MigrationError('Destination file collision or hash mismatch')
        else:
            partial = target.with_name('.orion-partial-' + journal['operation_id'] + '-' + target.name)
            _canonical(partial)
            if not partial.exists():
                with original.open('rb') as reader, partial.open('xb') as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    writer.flush()
                    os.fsync(writer.fileno())
            if partial.stat().st_size != expected['size'] or _hash(partial) != expected['sha256']:
                raise MigrationError('Interrupted copy or changing source; partial retained for repair')
            # The destination was claimed empty; no unowned file may be replaced.
            if target.exists():
                raise MigrationError('Destination changed during copy')
            if os.name == 'nt':
                os.rename(partial, target)  # Windows refuses an existing target.
            else:
                os.link(partial, target)  # Exclusive publication; never overwrite.
                partial.unlink()  # Only this operation's fully verified temp file.
            _sync_directory(target.parent)
        if original.stat().st_size != expected['size'] or _hash(original) != expected['sha256']:
            raise MigrationError('Source changed during copy')
        journal['copied_files'] = sorted(set(journal['copied_files']) | {name})
        _boundary(path, journal, 'copying', fault)


def _journal_path(registry, operation_id):
    return registry.path.parent / 'migrations' / (operation_id + '.json')


def _ensure_no_pending(registry):
    directory = _canonical(registry.path.parent / 'migrations')
    if not directory.exists():
        return
    for path in directory.glob('*.json'):
        _canonical(path, must_exist=True)
        try:
            journal = json.loads(path.read_text(encoding='utf-8'))
            _validate_journal(registry, path.resolve(), journal)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise MigrationError('Existing migration journal needs repair') from exc
        if journal['phase'] != 'completed':
            raise MigrationError('An unfinished migration must be recovered before another starts')


def _previous_marker(registry, source, record, kind):
    marker = source / MARKER
    if not marker.exists():
        return None
    _canonical(marker, must_exist=True)
    try:
        identity = json.loads(marker.read_text(encoding='utf-8'))
        operation_id = str(UUID(identity['operation_id']))
        path = _canonical(_journal_path(registry, operation_id), must_exist=True)
        previous = json.loads(path.read_text(encoding='utf-8'))
        _validate_journal(registry, path, previous)
        if (identity['installation_id'] != record['installation_id'] or previous['phase'] != 'completed'
                or previous['kind'] != kind or _canonical(previous['destination']) != source):
            raise MigrationError('Source marker is not from this completed migration history')
        return operation_id
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MigrationError('Source marker history cannot be verified') from exc


def _matches_source(source, journal):
    previous = journal.get('source_marker')
    _matches(source, journal['inventory'], target=previous is not None, operation_id=previous)


def begin_content_migration(registry, name, destination, *, expected_revision,
                            quiescence_gate, fault=None):
    """Plan/copy/verify/switch an explicitly registered Models or Downloads tree.

    Gate must be a trusted host callback confirming all consumers and writers are
    idle, with ownership held by the caller until this function returns. Unknown
    ownership is rejected. No source or previous target cleanup is performed.
    """
    from services.shared.installation_paths import resolve_location, validate_destination
    if name not in KINDS:
        raise MigrationError('Only Models and Downloads migration is supported')
    with operation_lock(registry):
        return _begin_content_migration_locked(registry, name, destination, expected_revision=expected_revision,
                                               quiescence_gate=quiescence_gate, fault=fault)


def _begin_content_migration_locked(registry, name, destination, *, expected_revision, quiescence_gate, fault=None):
    from services.shared.installation_paths import resolve_location, validate_destination
    if name not in KINDS:
        raise MigrationError('Only Models and Downloads migration is supported')
    _idle(quiescence_gate)
    _ensure_no_pending(registry)
    record = registry.read()
    if record is None or registry_revision(record) != expected_revision:
        raise MigrationError('Installation registry changed; select the source again')
    source = _canonical(resolve_location(record, name), must_exist=True)
    if not source.is_dir():
        raise MigrationError('Registered content source is missing')
    for occupied in _occupied(record, name):
        occupied = _canonical(occupied)
        if source == occupied or source.is_relative_to(occupied) or occupied.is_relative_to(source):
            raise MigrationError('Content source overlaps another component/location; source/runtime/DB moves are unsupported')
    previous_marker = _previous_marker(registry, source, record, name)
    inventory = _inventory(source, target=previous_marker is not None, operation_id=previous_marker)
    size = sum(item['size'] for item in inventory['files'].values())
    check = validate_destination(str(destination), source=source,
                                 occupied_paths=_occupied(record, name), required_bytes=size)
    target = _canonical(check.path)
    operation_id = str(uuid4())
    journal = dict(schema_version=1, operation_id=operation_id, installation_id=record['installation_id'],
                   kind=name, source=str(source), destination=str(target), registry_path=str(registry.path.resolve()),
                   expected_revision=expected_revision, phase='planned', inventory=inventory, copied_files=[])
    journal['source_marker'] = previous_marker
    path = _journal_path(registry, operation_id)
    _boundary(path, journal, 'planned', fault)
    return _resume_locked(registry, path, journal, quiescence_gate, fault)


def _validate_journal(registry, path, journal):
    if (not isinstance(journal, dict) or journal.get('schema_version') != 1
            or journal.get('kind') not in KINDS or journal.get('phase') not in PHASES):
        raise MigrationError('Unsupported migration journal; repair required')
    operation_id = str(UUID(journal['operation_id']))
    if operation_id != journal['operation_id'] or path != _journal_path(registry, operation_id).resolve():
        raise MigrationError('Journal path/operation mismatch')
    if journal['registry_path'] != str(registry.path.resolve()):
        raise MigrationError('Journal belongs to another registry')
    for name, entry in journal['inventory']['files'].items():
        _relative(name)
        if (type(entry['size']) is not int or entry['size'] < 0 or not isinstance(entry['sha256'], str)
                or len(entry['sha256']) != 64 or any(char not in '0123456789abcdef' for char in entry['sha256'])):
            raise MigrationError('Invalid hash inventory')
    for name in journal['inventory']['directories']:
        _relative(name)


def _resume_locked(registry, path, journal, gate, fault):
    from services.shared.installation_paths import resolve_location
    _validate_journal(registry, path.resolve(), journal)
    if journal['phase'] == 'repair-required':
        raise MigrationError('Migration needs explicit repair; source and destination retained')
    try:
        _idle(gate)
        source, destination = _canonical(journal['source'], must_exist=True), _canonical(journal['destination'])
        if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
            raise MigrationError('Overlapping source/destination')
        record = registry.read()
        if record is None or record['installation_id'] != journal['installation_id']:
            raise MigrationError('Installation ownership changed')
        if journal['phase'] == 'completed':
            if resolve_location(record, journal['kind']) != destination:
                raise MigrationError('Completed migration location was subsequently changed')
            return deepcopy(journal)
        revision = registry_revision(record)
        already_switched = revision == journal.get('switched_revision')
        expected_source = destination if already_switched else source
        if resolve_location(record, journal['kind']) != expected_source:
            raise MigrationError('Registered content source changed')
        if not already_switched and revision != journal['expected_revision']:
            raise MigrationError('Installation registry changed during migration')
        for occupied in _occupied(record, journal['kind']):
            occupied = _canonical(occupied)
            for location in (source, destination):
                if location == occupied or location.is_relative_to(occupied) or occupied.is_relative_to(location):
                    raise MigrationError('Migration overlaps another registered component/location')
        _matches_source(source, journal)
        if already_switched:
            # Never revert to the old source after the new location was enabled.
            _matches(destination, journal['inventory'], target=True, operation_id=journal['operation_id'])
            _boundary(path, journal, 'completed', fault)
            return deepcopy(journal)
        _claim_target(destination, journal)
        _boundary(path, journal, 'copying', fault)
        _copy_files(source, destination, journal, path, gate, fault)
        _matches_source(source, journal)
        _matches(destination, journal['inventory'], target=True, operation_id=journal['operation_id'])
        _boundary(path, journal, 'verified', fault)
        _idle(gate)
        updated = deepcopy(record)
        updated.setdefault('locations', {})[journal['kind']] = str(destination)
        journal['switched_revision'] = registry_revision(updated)
        _boundary(path, journal, 'switching', fault)
        # Recheck both trees after interruption hooks and before authoritative CAS.
        _idle(gate)
        _matches_source(source, journal)
        _matches(destination, journal['inventory'], target=True, operation_id=journal['operation_id'])
        def switch(current):
            if current is None or registry_revision(current) != journal['expected_revision']:
                raise MigrationError('Installation registry changed before switch')
            return updated
        registry.update(switch)
        if fault is not None:
            fault('registry-switched', deepcopy(journal))
        _boundary(path, journal, 'completed', fault)
        return deepcopy(journal)
    except Exception as exc:
        journal['error'] = type(exc).__name__ + ': ' + str(exc)
        _boundary(path, journal, 'repair-required', None)
        raise MigrationError(journal['error']) from exc


def resume_content_migration(registry, journal_path, *, quiescence_gate, fault=None):
    """Recover an owned journal; never silently roll back a switched location."""
    with operation_lock(registry):
        return _resume_content_migration_locked(registry, journal_path, quiescence_gate=quiescence_gate, fault=fault)


def _resume_content_migration_locked(registry, journal_path, *, quiescence_gate, fault=None):
    path = _canonical(journal_path, must_exist=True)
    expected_directory = _canonical(registry.path.parent / 'migrations', must_exist=True)
    if path.parent != expected_directory:
        raise MigrationError('Migration journal must belong to this registry')
    try:
        journal = json.loads(path.read_text(encoding='utf-8'))
        return _resume_locked(registry, path, journal, quiescence_gate, fault)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MigrationError('Invalid migration journal; preserve it for repair') from exc
