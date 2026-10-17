"""Reviewed package removal and bounded old-code cleanup; data is retained."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import uuid

from .automatic_updates import read_json, write_json
from .installation_registry import operation_lock
from .installation_paths import canonical_path, validate_private_directory
from .legacy_conversion import revision
from .release_packages import verify_package_record
from .update_helper import UpdateHelper, UpdateError, inventory, StoppedPackages
from .update_gate import TERMINAL


def _idle_update(helper):
    tx = helper.status()
    if tx and tx['phase'] not in TERMINAL:
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
    from .windows_autostart import known_folder, shortcut
    entry = registry.path.parent / 'bootstrap/Orion.ps1'
    for kind in ('startup', 'desktop'):
        path = known_folder(kind) / 'Orion.lnk'
        if path.exists():
            shortcut(path, entry, installation_id, startup=kind == 'startup', remove=True)


def uninstall(registry, kinds, *, expected_revision, helper=None, links=remove_links):
    helper = helper or UpdateHelper(registry)
    with operation_lock(registry.path, timeout=0):
        _idle_update(helper)
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
