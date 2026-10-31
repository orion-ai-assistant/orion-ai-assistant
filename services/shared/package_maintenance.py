"""Reviewed package removal and bounded old-code cleanup; data is retained."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import uuid

from .automatic_updates import read_json, write_json
from .installation_registry import operation_lock
from .installation_paths import canonical_path, validate_private_directory
from .legacy_conversion import revision
from .process_lifecycle import LifecycleError
from .release_packages import verify_package_record
from .update_helper import UpdateHelper, UpdateError, inventory, StoppedPackages
from .update_gate import TERMINAL

TRASH = '.trash-'  # A folder being deleted: renamed first, so a half-deleted release never looks like a release.


# An update in these phases has not switched anything yet (the active version is unchanged), exactly what an
# explicit rollback may cancel. Under the operation lock no prepare/apply is running for it.
NOT_SWITCHED = {'checking', 'downloading', 'verifying', 'staged', 'waiting_for_idle', 'draining', 'backing_up'}


def _idle_update(helper, *, cancel_unapplied=False):
    tx = helper.status()
    if tx and tx['phase'] not in TERMINAL:
        if cancel_unapplied and not tx.get('conversion') and (tx['proposed'] is None or tx['phase'] in NOT_SWITCHED):
            # A downloaded-but-not-applied update must not block removing the product it would update.
            helper._phase(tx, 'failed', reason='Cancelled by uninstall; active version unchanged')
            return
        raise UpdateError('Resolve the pending update/conversion before maintenance')


def removal_plan(registry, kinds):
    record = registry.read()
    if not record:
        raise UpdateError('No registered installation')
    kinds = set(kinds)
    if not kinds or kinds - {'hub', 'client', 'router'}:
        raise UpdateError('Select registered component kinds')
    if 'hub' in kinds:
        kinds.add('client')
    selected = [c for c in record['components'] if c['kind'] in kinds]
    if not selected or any(c['distribution'] != 'packaged' or c['ownership'] != 'owned'
                           or c['management'] != 'local' for c in selected):
        raise UpdateError('Only owned local packages can be removed; external/source installs are preserved')
    for item in selected:
        verify_package_record(item)
    return {'schema': 1, 'installation_id': record['installation_id'], 'revision': revision(record),
            'components': selected, 'preserve_data': True, 'preserve_models': True,
            'preserve_bootstrap_and_backups': True}


def remove_links(registry, installation_id):
    """Desktop/Startup links of every product identity (if ours) and stale Startup-apps state."""
    from .uninstall import remove_links as remove_all
    return remove_all(registry, installation_id)


def uninstall(registry, kinds, *, expected_revision, helper=None, links=remove_links):
    helper = helper or UpdateHelper(registry)
    with operation_lock(registry.path, timeout=0):
        _idle_update(helper, cancel_unapplied=True)
        plan = removal_plan(registry, kinds)
        if plan['revision'] != expected_revision:
            raise UpdateError('Installation changed; review a fresh uninstall plan')
        previous = registry.read()
        with helper.host.stopped(previous) as idle:
            if not idle:
                raise UpdateError('Services are running; uninstall never interrupts user work')
            proposed = deepcopy(previous)
            ids = {c['component_id'] for c in plan['components']}
            proposed['components'] = [c for c in proposed['components'] if c['component_id'] not in ids]
            if 'hub' in {c['kind'] for c in plan['components']} or not proposed['components']:
                proposed['preferences']['start_with_windows'] = False
                proposed['preferences']['automatic_updates'] = False
            directory = registry.path.parent / 'maintenance' / str(uuid.uuid4())
            directory.mkdir(parents=True)
            journal = {'schema': 1, 'installation_id': previous['installation_id'], 'phase': 'prepared',
                       'previous': previous, 'proposed': proposed, 'removed': plan['components']}
            write_json(directory / 'journal.json', journal)
            registry.write(proposed)
            journal['phase'] = 'unregistered'
            write_json(directory / 'journal.json', journal)
            # Disabled Startup is inert even if shortcut deletion fails. The journal
            # and original package/data remain available for explicit retry/repair.
            if not proposed['components'] or any(c['kind'] == 'hub' for c in plan['components']):
                links(registry, previous['installation_id'])
            journal['phase'] = 'completed'
            write_json(directory / 'journal.json', journal)
            return {'status': 'uninstalled', 'operation_id': directory.name,
                    'data_preserved': True, 'models_preserved': True, 'code_retained': True}


def restore_registration(registry, operation, *, expected_revision):
    identity = str(uuid.UUID(operation))
    if identity != operation:
        raise UpdateError('Invalid maintenance identity')
    with operation_lock(registry.path, timeout=0):
        helper = UpdateHelper(registry)
        _idle_update(helper)
        path = registry.path.parent / 'maintenance' / identity / 'journal.json'
        tx = read_json(path, None)
        current = registry.read()
        if (not tx or tx.get('schema') != 1 or tx['installation_id'] != current['installation_id']
                or current != tx['proposed'] or revision(current) != expected_revision):
            raise UpdateError('Uninstall journal/installation changed; explicit repair required')
        with helper.host.stopped(current) as idle:
            if not idle:
                raise UpdateError('Stop services before restoring registration')
            for item in tx['previous']['components']:
                verify_package_record(item)
            record = deepcopy(tx['previous'])
            # Registration restore is not consent to re-enable Startup/updates.
            record['preferences'] = current['preferences']
            registry.write(record)
            tx['phase'] = 'restored'
            write_json(path, tx)
            return {'status': 'registration-restored', 'preferences': record['preferences']}


def cleanup_plan(registry):
    helper = UpdateHelper(registry)
    _idle_update(helper)
    record = registry.read()
    protected, candidates = set(), {}
    for item in record['components']:
        protected.add(item['code_path'])
    journals = sorted(helper.base.glob('*/journal.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    last_healthy = next((read_json(p, {}) for p in journals if read_json(p, {}).get('phase') == 'completed'), None)
    for path in journals:
        tx = read_json(path, {})
        if tx.get('installation_id') != record['installation_id']:
            continue
        keep = tx.get('phase') not in TERMINAL or tx == last_healthy
        for key in ('previous', 'proposed'):
            for item in (tx.get(key) or {}).get('components', []):
                if item['distribution'] != 'packaged':
                    continue
                if keep:
                    protected.add(item['code_path'])
                else:
                    candidates[item['code_path']] = item
    for path in (registry.path.parent / 'maintenance').glob('*/journal.json'):
        tx = read_json(path, {})
        if tx.get('installation_id') == record['installation_id'] and tx.get('phase') == 'completed':
            for item in tx.get('removed', []):
                candidates[item['code_path']] = item
    result = []
    for code, item in candidates.items():
        if code not in protected and Path(code).exists():
            result.append(item)
    return {'schema': 1, 'revision': revision(record), 'components': result,
            'protected_releases': sorted(protected), 'data_preserved': True}


def purge_old_code(registry, package_id, *, expected_revision):
    """Explicit code-only deletion, never wildcard cleanup or user-data deletion."""
    with operation_lock(registry.path, timeout=0):
        plan = cleanup_plan(registry)
        if plan['revision'] != expected_revision:
            raise UpdateError('Installation changed; review cleanup again')
        matches = [c for c in plan['components'] if c['package']['id'] == package_id]
        if len(matches) != 1:
            raise UpdateError('Release is active, needed for recovery, unknown or ambiguous')
        item = matches[0]
        tree = canonical_path(item['code_path'], must_exist=True)
        validate_private_directory(tree)
        if tree.name != package_id or tree.parent.name != 'releases':
            raise UpdateError('Immutable release layout required')
        for path in [item['data_path'], item['state_path'], str(registry.path.parent / 'bootstrap'),
                     *registry.read().get('locations', {}).values()]:
            protected = canonical_path(path)
            if tree == protected or tree.is_relative_to(protected) or protected.is_relative_to(tree):
                raise UpdateError('Release overlaps data/models/state/bootstrap')
        with StoppedPackages({**registry.read(), 'components': [*registry.read()['components'], item]}) as idle:
            if not idle:
                raise UpdateError('A consumer still uses this release; no process was stopped')
            verify_package_record(item)
            # inventory rejects every link/junction in the exact validated tree.
            inventory(tree)
            audit = registry.path.parent / 'maintenance' / ('purge-' + str(uuid.uuid4()) + '.json')
            audit.parent.mkdir(exist_ok=True)
            write_json(audit, {'schema': 1, 'phase': 'deleting-code', 'package_id': package_id, 'code_path': str(tree)})
            shutil.rmtree(tree)
            write_json(audit, {'schema': 1, 'phase': 'completed', 'package_id': package_id})
            return {'status': 'code-removed', 'package_id': package_id, 'data_preserved': True}


def prune_old_releases(registry, helper=None):
    """Automatic cleanup after updates: only the active version of each component stays on disk.

    Deleted: release trees (`<Component>/releases/orion-<kind>-*`) and their lifecycle state folders
    (`<Component>/state/releases/...`) that no component uses, and the folders of finished update operations
    except the latest one. Kept: everything the registry or a pending update names, data, models, the bootstrap
    and anything Orion did not name this way. A busy installation (another operation holds the lock), an
    unreadable journal or an update in an undecided phase deletes nothing. Returns the removed folder names."""
    try:
        # Under the lock only the decision and the renames (milliseconds); the slow delete runs after it, so a
        # check or "update now" is never refused as busy while a gigabyte is being deleted.
        with operation_lock(registry.path, timeout=0):
            moved = _retire(registry, helper or UpdateHelper(registry))
    except LifecycleError:
        return []
    removed = []
    for name, trash in moved:
        shutil.rmtree(trash, ignore_errors=True)
        if not trash.exists():
            removed.append(name)
    return removed


def _retire(registry, helper):
    """(name, trash path) of every unused folder, already renamed out of the way."""
    record = registry.read()
    try:
        tx = helper.status()
    except UpdateError:
        return []  # An explicit repair needs everything it may refer to.
    records = [record]
    if tx and tx['phase'] not in TERMINAL:
        if tx['phase'] == 'repair_required' or tx['proposed'] is None:
            return []  # Undecided, or a release tree is still being downloaded.
        records += [tx['previous'], tx['proposed']]
    keep, folders = set(), {}
    for item in (c for r in records for c in r['components'] if c['distribution'] == 'packaged'):
        for field in ('code_path', 'state_path'):
            path = canonical_path(item[field])
            keep.add(path)
            if path.parent.name == 'releases' and (field == 'code_path' or path.parent.parent.name == 'state'):
                folders[path.parent] = item['kind']
    removed = []
    for folder, kind in folders.items():
        name = re.compile(r'orion-' + kind + r'-[0-9A-Za-z._-]+')
        for path in _children(folder):
            if path not in keep and (name.fullmatch(path.name) or path.name.startswith(TRASH)):
                removed += _delete(path)
    active = (tx or {}).get('operation_id')
    for path in _children(helper.base):
        if path.name.startswith(TRASH):
            removed += _delete(path)
            continue
        try:
            finished = (str(uuid.UUID(path.name)) == path.name and path.name != active
                        and read_json(path / 'journal.json', {}).get('phase') in TERMINAL)
        except (ValueError, OSError, AttributeError):
            finished = False  # Not an operation folder, or a journal that cannot tell: kept.
        if finished:
            removed += _delete(path)
    return removed


def _children(folder):
    if not folder.is_dir():
        return []
    result = []
    for path in folder.iterdir():
        try:
            result.append(canonical_path(path, must_exist=True))  # Links and junctions are refused, never followed.
        except ValueError:
            continue
    return [path for path in result if path.is_dir()]


def _delete(path):
    """Rename out of the way; the caller deletes. A folder in use (a running executable, an open file) cannot be
    renamed on Windows, so it is left alone; a delete cut short leaves a `.trash-` folder the next round finishes."""
    if path.name.startswith(TRASH):
        return [(path.name, path)]
    trash = path.with_name(TRASH + uuid.uuid4().hex)
    try:
        path.rename(trash)
    except OSError:
        return []
    return [(path.name, trash)]
