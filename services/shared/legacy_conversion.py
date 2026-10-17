"""Explicit, stopped-only legacy conversion. Original trees/data are retained.

No automatic legacy conversion, SQL major upgrade, data deletion or source reset.
The full private legacy tree is copied as data, including unrecognized user files.
This intentionally needs room for both a copy and a recovery backup.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import uuid

from .installation_registry import operation_lock
from .installation_paths import canonical_path, validate_private_directory
from .release_packages import compatible, record_prepared_package
from .update_helper import UpdateHelper, UpdateError, inventory
from .update_gate import TERMINAL


def revision(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def runtime_python(item):
    root = Path(item['code_path'])
    if item['distribution'] == 'packaged':
        return root / 'runtime/python/python.exe'
    return root / ('services/hub/.venv/Scripts/python.exe' if item['kind'] == 'hub' else '.venv/Scripts/python.exe')


def preflight(registry, destination):
    record = registry.read()
    if not record or not record['components']:
        raise UpdateError('Register the selected legacy installations first')
    dest = canonical_path(destination)
    validate_private_directory(dest)
    if dest.exists():
        raise UpdateError('Conversion destination must be a new private directory')
    files, total = {}, 0
    for item in record['components']:
        if (item['distribution'] != 'legacy-source' or item['ownership'] != 'owned'
                or item['management'] != 'local' or item['kind'] not in {'hub', 'router'}):
            raise UpdateError('Conversion requires owned legacy Hub/Router only; development/external/container paths are preserved')
        root = canonical_path(item['code_path'], must_exist=True)
        if Path(item['data_path']) != root or Path(item['state_path']) != root / '.orion-lifecycle':
            raise UpdateError('Unsupported legacy data/state layout')
        if dest == root or dest.is_relative_to(root) or root.is_relative_to(dest):
            raise UpdateError('Conversion destination overlaps the original installation')
        metadata = root / ('services/hub/pyproject.toml' if item['kind'] == 'hub' else 'pyproject.toml')
        if tomllib.loads(metadata.read_text(encoding='utf-8'))['project']['name'] != 'orion-' + item['kind']:
            raise UpdateError('Legacy component provenance mismatch')
        pgdata = root / ('.local_db/postgres/data' if item['kind'] == 'hub' else '.pgdata-prod')
        if (pgdata / 'PG_VERSION').read_text().strip() != '16':
            raise UpdateError('Only PostgreSQL 16 clusters are supported; major conversion requires a separate SQL adapter')
        for cluster in (pgdata, root / '.pgdata-dev'):
            if (cluster / 'pg_tblspc').exists() and any((cluster / 'pg_tblspc').iterdir()):
                raise UpdateError('External tablespaces require explicit migration')
        # Redirected mutable resources must be reconciled explicitly rather than
        # pointing a new package silently at data belonging to another runtime.
        from dotenv import dotenv_values
        envfile = root / ('services/.env.global.local' if item['kind'] == 'hub' else '.env')
        values = dotenv_values(envfile) if envfile.exists() else {}
        forbidden = {'ORION_SECURITY_DIR', 'ORION_ROUTER_TLS_DIRECTORY', 'ORION_ROUTER_MDNS_ID_FILE',
                     'PGDATA', 'POSTGRES_DATA_DIR', 'DATABASE_URL'}
        if any(values.get(key) for key in forbidden):
            raise UpdateError('Custom security/database paths require an explicit migration adapter')
        files[item['kind']] = inventory(root)
        if '.orion-converted.json' in files[item['kind']]:
            raise UpdateError('Source already converted; use the registered launcher')
        total += sum((root / name).stat().st_size for name in files[item['kind']])
    return {'schema': 1, 'installation_id': record['installation_id'], 'revision': revision(record),
            'destination': str(dest), 'copy_bytes': total, 'required_bytes': total * 2 + 128 * 1024**2,
            'originals_retained': True, 'components': [c['kind'] for c in record['components']]}, files


def protect_sources(helper, tx):
    for item in tx['previous']['components']:
        files = inventory(item['code_path'])
        files.pop('.orion-converted.json', None)
        if files != tx['legacy_inventories'][item['kind']]:
            raise UpdateError('Legacy data changed since the stopped copy; explicit repair required')
        path = canonical_path(Path(item['code_path']) / '.orion-converted.json')
        value = {'schema': 1, 'operation_id': tx['operation_id'], 'installation_id': tx['installation_id'],
                 'registry': str(helper.registry.path)}
        if path.exists() and json.loads(path.read_text()) != value:
            raise UpdateError('Another conversion owns this source')
        helper._json(path, value, 'legacy-guard:' + item['kind'])


def unprotect_sources(helper, tx):
    for item in tx['previous']['components']:
        path = canonical_path(Path(item['code_path']) / '.orion-converted.json')
        if path.exists():
            value = json.loads(path.read_text())
            if value.get('operation_id') != tx['operation_id'] or value.get('installation_id') != tx['installation_id']:
                raise UpdateError('Legacy guard ownership mismatch')
            path.unlink()


def convert(registry, client, names, destination, *, expected_revision, helper=None):
    helper = helper or UpdateHelper(registry)
    with operation_lock(registry.path, timeout=0):
        old = registry.read()
        if revision(old) != expected_revision:
            raise UpdateError('Installation changed; review a fresh conversion plan')
        pending = helper.status()
        if pending and pending['phase'] not in TERMINAL:
            raise UpdateError('Resolve the pending update before conversion')
        plan, files = preflight(registry, destination)
        with helper.host.stopped(old) as stopped:
            if not stopped:
                raise UpdateError('Stop legacy services at a convenient time; no service was interrupted')
            if {c['kind']: inventory(c['code_path']) for c in old['components']} != files:
                raise UpdateError('Legacy tree changed during preflight')
            checked, targets = client.check(names)
            if targets is None:
                raise UpdateError('Signed conversion repository unavailable')
            releases = [t.custom for t in targets.values()]
            if {r['component'] for r in releases} != set(plan['components']) or len(releases) != len(old['components']):
                raise UpdateError('One signed package is required for every legacy component')
            compatible(releases, mobile_api=1)
            if any(r['data_schema'] != 1 or 'lifecycle.update-quarantine.v1' not in r['capabilities'] for r in releases):
                raise UpdateError('Conversion requires schema 1 and quarantine-capable packages')
            dest = Path(plan['destination'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(dest.parent).free < plan['required_bytes']:
                raise UpdateError('Insufficient space for legacy copy and recovery backup')
            dest.mkdir()
            identity = str(uuid.uuid4())
            (helper.base / identity).mkdir()
            tx = dict(schema=1, operation_id=identity, installation_id=old['installation_id'], previous=old,
                      proposed=None, changes=[], attempts=0, verification={}, backups={}, health=None, reason=None,
                      conversion=True, conversion_data_ready=False, legacy_inventories=files)
            helper._phase(tx, 'checking')
            helper._json(helper.base / 'active.json', {'schema': 1, 'operation_id': identity}, 'active')
            # This independently retained guard closes ordinary launch during copy/recovery.
            entry = registry.path.parent / 'bootstrap/Orion.ps1'
            entry.parent.mkdir(exist_ok=True)
            helper._write(entry, Path(__file__).with_name('launcher_entry.ps1').read_bytes(), 'launcher-guard')
            proposed = deepcopy(old)
            tx['proposed'] = proposed
            try:
                for target in targets.values():
                    kind = target.custom['component']
                    item = next(c for c in proposed['components'] if c['kind'] == kind)
                    directory = dest / kind.capitalize()
                    tree = client.prepare(target, directory / 'releases' / target.custom['package_id'])
                    state, data = directory / 'state', directory / 'data'
                    state.mkdir()
                    receipt = record_prepared_package(tree, state, target)
                    helper._write(helper._directory(tx) / (kind + '-candidate.json'),
                                  (state / 'installed-package.json').read_bytes(), 'candidate:' + kind)
                    source = item['code_path']
                    item.update(distribution='packaged', code_path=str(tree), data_path=str(data), state_path=str(state), package=receipt)
                    item.pop('lifecycle_ids', None)
                    for field in ('identity_path', 'certificate_path'):
                        if field in item:
                            relative = Path(item[field]).relative_to(source)
                            item[field] = str(data / relative)
                    tx['changes'].append(kind)
                    tx['verification'][kind] = {'verified': True, 'sha256': receipt['sha256'], 'length': receipt['length']}
                helper._phase(tx, 'backing_up')
                for item in old['components']:
                    data = Path(next(c for c in proposed['components'] if c['kind'] == item['kind'])['data_path'])
                    shutil.copytree(item['data_path'], data)
                    if inventory(data) != files[item['kind']] or inventory(item['data_path']) != files[item['kind']]:
                        raise UpdateError('Legacy data copy verification failed')
                tx['conversion_data_ready'] = True
                helper._backup(tx)
                helper._phase(tx, 'switching')
                helper._verify_backups(tx)
                helper._publish(tx, proposed, True)
                helper._phase(tx, 'health_check', attempts=1)
                health = helper.host.health(proposed)
                required = {'hub_api', 'worker', 'database', 'client_api', 'router_identity'} if any(
                    c['kind'] == 'hub' for c in proposed['components']) else {'database', 'router_identity'}
                if set(health) != required or not all(v is True for v in health.values()):
                    raise UpdateError('Converted package health failed')
                helper.host.stop(proposed)
                with helper.host.stopped(proposed) as stopped:
                    if not stopped:
                        raise UpdateError('Converted package shutdown unverified')
                    helper._verify(tx, proposed, True)
                    return helper._phase(tx, 'completed', health=health)
            except Exception as error:
                helper._phase(tx, 'rollback_pending', reason=type(error).__name__)
                return helper._rollback(tx)
