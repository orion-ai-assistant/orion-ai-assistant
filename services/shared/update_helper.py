"""Stage 06 local, restartable package transaction; no scheduler or remote API.

Activation is deliberately stopped-only for the Stage 05 baseline. A running
installation stays in waiting_for_idle; the helper never interrupts user work.
Only Hub/Client updates are enabled here. Independent Router rollout is Stage 08.
Schema changes and bootstrap replacement fail closed, never restore a stale DB.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .installation_registry import Registry, RegistryError, operation_lock, _atomic_write, validate_record
from .installation_paths import canonical_path, validate_private_directory
from .release_packages import (PackageError, compatible, record_prepared_package,
                               verify_package_record, sha256)
from .update_gate import TERMINAL

PHASES = ('checking', 'downloading', 'verifying', 'staged', 'waiting_for_idle',
          'draining', 'backing_up', 'switching', 'health_check', 'completed',
          'failed', 'rollback_pending', 'rolled_back', 'repair_required')


class UpdateError(ValueError):
    pass


def inventory(tree):
    result = {}
    tree = canonical_path(tree, must_exist=True)
    for path in tree.rglob('*'):
        canonical_path(path, must_exist=True)  # Reject links, junctions and missing disks.
        if path.is_file():
            result[path.relative_to(tree).as_posix()] = sha256(path)
    return result


class UpdateHelper:
    def __init__(self, registry, *, host=None, fault=None):
        self.registry = registry if isinstance(registry, Registry) else Registry(registry)
        canonical_path(self.registry.path, must_exist=True)
        validate_private_directory(self.registry.path.parent)
        self.base = canonical_path(self.registry.path.parent / 'updates')
        self.base.mkdir(exist_ok=True)
        self.host = host or NativeHost(self.registry)
        self.fault = fault or (lambda label: None)

    def _write(self, path, data, label):
        canonical_path(path)
        self.fault('before:' + label)
        _atomic_write(path, data)
        self.fault('after:' + label)

    def _json(self, path, value, label):
        self._write(path, (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8'), label)

    def _phase(self, tx, phase, **fields):
        if phase not in PHASES:
            raise UpdateError('Unknown transaction phase')
        tx.update(fields, phase=phase)
        self._json(self._directory(tx) / 'journal.json', tx, 'journal:' + phase)
        return tx

    def _directory(self, tx):
        identity = tx['operation_id']
        if str(uuid.UUID(identity)) != identity:
            raise UpdateError('Invalid operation identity')
        return canonical_path(self.base / identity, must_exist=True)

    def status(self):
        pointer = canonical_path(self.base / 'active.json')
        if not pointer.exists():
            return None
        try:
            active = json.loads(pointer.read_text(encoding='utf-8'))
            identity = active['operation_id']
            if active['schema'] != 1 or str(uuid.UUID(identity)) != identity:
                raise ValueError()
            path = canonical_path(self.base / identity / 'journal.json', must_exist=True)
            tx = json.loads(path.read_text(encoding='utf-8'))
            if (tx['schema'] != 1 or tx['operation_id'] != identity or tx['phase'] not in PHASES
                    or tx['installation_id'] != self.registry.read()['installation_id']):
                raise ValueError()
            validate_record(tx['previous'])
            if tx.get('proposed') is not None:
                validate_record(tx['proposed'])
            return tx
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise UpdateError('Update journal invalid; explicit repair required') from error

    def prepare(self, client, names):
        with operation_lock(self.registry.path, timeout=0):
            pending = self.status()
            if pending and pending['phase'] not in TERMINAL:
                raise UpdateError('An update is pending; resume it before starting another')
            old = self.registry.read()
            if not old or not names or len(names) != len(set(names)):
                raise UpdateError('Select distinct signed release targets for an installed product')
            manifests = {}
            for item in old['components']:
                if item['distribution'] != 'packaged' or item['management'] != 'local' or item['ownership'] != 'owned':
                    raise UpdateError('Update requires owned local packaged components; legacy conversion is Stage 08')
                root = canonical_path(item['code_path'], must_exist=True)
                if root.parent.name != 'releases' or root.name != item['package']['id']:
                    raise UpdateError('Immutable release layout required')
                for field in ('data_path', 'state_path'):
                    path = canonical_path(item[field], must_exist=True)
                    validate_private_directory(path)
                    if path == root or path.is_relative_to(root) or root.is_relative_to(path):
                        raise UpdateError('Data/state overlaps code')
                if item['kind'] in manifests:
                    raise UpdateError('Select one installation per component kind')
                manifests[item['kind']] = verify_package_record(item)
            identity = str(uuid.uuid4())
            directory = self.base / identity
            directory.mkdir()
            tx = dict(schema=1, operation_id=identity, installation_id=old['installation_id'],
                      previous=old, proposed=None, changes=[], attempts=0, verification={},
                      backups={}, health=None, reason=None)
            # Save previous inventories before any mutable publication.
            for item in old['components']:
                self._write(directory / (item['kind'] + '-previous.json'),
                            (Path(item['state_path']) / 'installed-package.json').read_bytes(), 'previous:' + item['kind'])
            self._phase(tx, 'checking')
            self._json(self.base / 'active.json', {'schema': 1, 'operation_id': identity}, 'active')
            # Guard also works when the currently installed Hub predates Stage 06.
            entry = self.registry.path.parent / 'bootstrap/Orion.ps1'
            entry.parent.mkdir(exist_ok=True)
            self._write(entry, Path(__file__).with_name('launcher_entry.ps1').read_bytes(), 'launcher-guard')
            try:
                status, targets = client.check(names)
                if targets is None:
                    raise UpdateError('Signed release check unavailable: ' + status['reason'])
                proposed = deepcopy(old)
                seen = set()
                for target in targets.values():
                    release = target.custom
                    kind = release['component']
                    if kind not in {'hub', 'client'} or kind in seen or kind not in manifests:
                        raise UpdateError('Only one registered Hub/Client target is accepted; Router rollout is Stage 08')
                    seen.add(kind)
                    prior = manifests[kind]
                    if release['sequence'] <= prior['sequence']:
                        raise UpdateError('Update sequence must advance')
                    if release['data_schema'] != prior['data_schema']:
                        raise UpdateError('Schema migration requires a separate verified migration adapter')
                    manifests[kind] = release
                compatible(list(manifests.values()), installed_sequences={c['kind']:
                    json.loads((Path(c['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))['sequence']
                    for c in old['components']}, mobile_api=1)
                # Health cannot start an old Hub without quarantine support, even
                # for a Client-only update. Fail before downloading/activation.
                if 'lifecycle.update-quarantine.v1' not in manifests.get('hub', {}).get('capabilities', []):
                    raise UpdateError('Proposed Hub needs lifecycle.update-quarantine.v1; update Hub with Client')
                for target in targets.values():
                    kind = target.custom['component']
                    item = next(c for c in proposed['components'] if c['kind'] == kind)
                    tree = canonical_path(Path(item['code_path']).parent / target.custom['package_id'])
                    self._phase(tx, 'downloading')
                    if tree.exists():
                        validate_private_directory(tree)
                        # A previous interrupted/rolled-back attempt may have
                        # left this immutable ID. Re-prove its bytes from a fresh
                        # TUF-verified extraction; never trust an orphan receipt
                        # or overwrite/delete an existing release in place.
                        with tempfile.TemporaryDirectory(prefix='.update-verify-', dir=tree.parent) as temporary:
                            temporary = canonical_path(temporary, must_exist=True)
                            if not temporary.is_relative_to(tree.parent):
                                raise UpdateError('Verification scratch escaped the release parent')
                            fresh = client.prepare(target, temporary / target.custom['package_id'])
                            if inventory(tree) != inventory(fresh):
                                raise UpdateError('Existing staged release differs; explicit repair required')
                    else:
                        client.prepare(target, tree)
                    self._phase(tx, 'verifying')
                    # The inventory is versioned with the transaction, not yet
                    # published as the active component's installed receipt.
                    receipt_dir = directory / kind
                    receipt_dir.mkdir()
                    receipt = record_prepared_package(tree, receipt_dir, target)
                    receipt_path = receipt_dir / 'installed-package.json'
                    self._write(directory / (kind + '-candidate.json'), receipt_path.read_bytes(), 'candidate:' + kind)
                    # Process ownership records embed the immutable code root.
                    # Give each release its own external lifecycle state; never
                    # rewrite/adopt the old release's supervisor/Job identity.
                    new_state = canonical_path(tree.parent.parent / 'state/releases' / target.custom['package_id'])
                    validate_private_directory(new_state)
                    new_state.mkdir(parents=True, exist_ok=True)
                    item.update(code_path=str(tree), state_path=str(new_state), package=receipt)
                    verify_package_record(item, inventory=directory / (kind + '-candidate.json'))
                    tx['changes'].append(kind)
                    tx['verification'][kind] = {'sha256': receipt['sha256'], 'length': receipt['length'], 'verified': True}
                tx['proposed'] = proposed
                return self._phase(tx, 'staged')
            except Exception as error:
                self._phase(tx, 'failed', reason=type(error).__name__)
                raise

    def _verify(self, tx, record, candidate):
        directory = self._directory(tx)
        for item in record['components']:
            suffix = 'candidate' if candidate and item['kind'] in tx['changes'] else 'previous'
            verify_package_record(item, inventory=directory / (item['kind'] + '-' + suffix + '.json'))

    def _publish(self, tx, record, candidate):
        self._verify(tx, record, candidate)
        directory = self._directory(tx)
        for item in record['components']:
            suffix = 'candidate' if candidate and item['kind'] in tx['changes'] else 'previous'
            self._write(Path(item['state_path']) / 'installed-package.json',
                        (directory / (item['kind'] + '-' + suffix + '.json')).read_bytes(), 'inventory:' + item['kind'])
        self.fault('before:registry')
        self.registry.write(record)
        self.fault('after:registry')

    def _backup(self, tx):
        # Offline physical backup only: host's gate has verified supervisor,
        # descendants and PostgreSQL ownership and holds their lifecycle locks.
        directory = self._directory(tx)
        snapshots = {}
        for item in tx['previous']['components']:
            source = Path(item['data_path'])
            files = inventory(source)
            size = sum((source / name).stat().st_size for name in files)
            if shutil.disk_usage(directory).free < size + 64 * 1024**2:
                raise UpdateError('Insufficient backup disk space')
            # An interrupted copy is retained as evidence; never reused as a
            # complete backup. No active data or previous release is deleted.
            dest = directory / ('backup-' + item['kind'] + '-' + str(uuid.uuid4()))
            shutil.copytree(source, dest)
            if inventory(dest) != files or inventory(source) != files:
                raise UpdateError('Offline backup changed during copy')
            for path in dest.rglob('*'):
                if path.is_file():
                    with path.open('r+b') as stream:
                        os.fsync(stream.fileno())
            snapshots[item['kind']] = {'path': str(dest), 'files': files}
            self.fault('after:backup:' + item['kind'])
        tx['backups'] = snapshots
        self._json(directory / 'backups.json', snapshots, 'backups')

    def _verify_backups(self, tx):
        if set(tx['backups']) != {c['kind'] for c in tx['previous']['components']}:
            raise UpdateError('Complete offline backup required')
        for snapshot in tx['backups'].values():
            path = canonical_path(snapshot['path'], must_exist=True)
            if not path.is_relative_to(self._directory(tx)) or inventory(path) != snapshot['files']:
                raise UpdateError('Backup inventory mismatch')

    def resume(self):
        with operation_lock(self.registry.path, timeout=0):
            tx = self.status()
            if tx is None or tx['phase'] in TERMINAL or tx['phase'] == 'repair_required':
                return tx
            if tx['phase'] in {'checking', 'downloading', 'verifying'}:
                return self._phase(tx, 'failed', reason='Preparation interrupted; active version unchanged; prepare again')
            current = self.registry.read()
            if current not in (tx['previous'], tx['proposed']):
                return self._phase(tx, 'repair_required', reason='Registry changed outside this transaction')
            if tx['phase'] == 'rollback_pending':
                return self._rollback(tx)
            try:
                self._verify(tx, tx['proposed'], True)
                if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                    self._verify(tx, tx['previous'], False)
                if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                    with self.host.stopped(tx['previous']) as idle:
                        if not idle:
                            return self._phase(tx, 'waiting_for_idle')
                        self._phase(tx, 'draining')
                        self._phase(tx, 'backing_up')
                        self._backup(tx)
                        self._phase(tx, 'switching')
                        self._verify_backups(tx)
                        self._publish(tx, tx['proposed'], True)
                else:
                    # A process may have survived its helper. Quarantine stays
                    # closed until it is stopped with verified ownership.
                    self.host.stop(current)
                    with self.host.stopped(current) as idle:
                        if not idle:
                            raise UpdateError('Candidate processes could not be reconciled')
                        self._verify_backups(tx)
                        self._publish(tx, tx['proposed'], True)
                if tx['attempts'] >= 2:
                    raise UpdateError('Health retry limit reached')
                self._phase(tx, 'health_check', attempts=tx['attempts'] + 1)
                result = self.host.health(tx['proposed'])
                required = {'hub_api', 'worker', 'database', 'client_api', 'router_identity'}
                if set(result) != required or not all(v is True for v in result.values()):
                    raise UpdateError('Candidate health failed')
                # End health in a stopped state. No user job has been admitted,
                # and no unfinished external tool is retried by the helper.
                self.host.stop(tx['proposed'])
                with self.host.stopped(tx['proposed']) as idle:
                    if not idle:
                        raise UpdateError('Health shutdown unverified')
                    self._verify(tx, tx['proposed'], True)
                    return self._phase(tx, 'completed', health=result)
            except Exception as error:
                if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                    # Nothing has switched. In particular, a corrupt download
                    # must never cause rollback to stop a still-busy old Hub.
                    return self._phase(tx, 'failed', reason=type(error).__name__)
                self._phase(tx, 'rollback_pending', reason=type(error).__name__)
                return self._rollback(tx)

    def _rollback(self, tx):
        try:
            current = self.registry.read()
            if current not in (tx['previous'], tx['proposed']):
                raise UpdateError('Registry changed outside this transaction')
            self.host.stop(current)
            with self.host.stopped(current) as idle:
                if not idle:
                    raise UpdateError('Rollback consumers still running')
                # Same-schema only. Keep all data, including candidate startup
                # writes. Backups require explicit repair, never silent restore.
                self._publish(tx, tx['previous'], False)
                if not self.host.recovery_health(tx['previous']):
                    raise UpdateError('Previous database/schema needs explicit repair')
                return self._phase(tx, 'rolled_back')
        except Exception as error:
            return self._phase(tx, 'repair_required', reason=type(error).__name__)

    def rollback(self):
        """Explicit repair after resolving the recorded local access/lock fault."""
        with operation_lock(self.registry.path, timeout=0):
            tx = self.status()
            if tx is None or tx['phase'] in TERMINAL:
                raise UpdateError('No pending operation to roll back')
            if tx['proposed'] is None or tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                return self._phase(tx, 'failed', reason='Preparation cancelled; active version unchanged')
            self._phase(tx, 'rollback_pending')
            return self._rollback(tx)


class NativeHost:
    """Only fixed, registered native lifecycle operations; never arbitrary argv."""
    def __init__(self, registry):
        self.registry = registry

    def stopped(self, record):
        return StoppedPackages(record)

    def stop(self, record):
        from .launcher import launch_environment
        from .native_services import stop_hub, stop_router
        from .process_lifecycle import Runtime
        operation = json.loads((self.registry.path.parent / 'updates/active.json').read_text(encoding='utf-8'))['operation_id']
        with launch_environment(self.registry):
            for item in sorted(record['components'], key=lambda c: c['kind'] != 'hub'):
                if item['kind'] == 'client':
                    continue
                mode = 'hub' if item['kind'] == 'hub' else 'router-prod'
                state = Runtime(item['code_path'], mode).read()
                if state is None:
                    # A candidate may fail before its first Runtime context.
                    # Absence alone is insufficient: rule out untracked users
                    # of this exact executable tree before skipping shutdown.
                    from .migration_host import StoppedInstallation
                    check = StoppedInstallation.__new__(StoppedInstallation)
                    check.record = {**record, 'components': [item]}
                    check._unmanaged_consumers()
                    continue
                stop = (lambda: stop_hub(item['code_path'], update_operation=operation)) if item['kind'] == 'hub' else (
                    lambda: stop_router(str(Path(item['code_path']) / 'cli.py')))
                stop_and_wait(state, stop)

    def health(self, record):
        # Implemented in a dedicated module to keep the transaction core free
        # from runtime/DB imports and make health evidence independently testable.
        from .update_health import probe
        return probe(self.registry, record)

    def recovery_health(self, record):
        """Read-only DB check with old runtime; never starts an old Worker/API."""
        import subprocess
        from .launcher import launch_environment
        hub = next(c for c in record['components'] if c['kind'] == 'hub')
        root = Path(hub['code_path'])
        program = '''from services.shared.native_services import hub_database
from services.shared.environment import get_env
db=hub_database('.',get_env)
try:
    db.ensure()
    assert db.ready()
    db.sql('SELECT user_id,key,value FROM orion_settings LIMIT 0')
    db.sql('SELECT id FROM orion_users LIMIT 0')
    db.sql('SELECT id FROM orion_chats LIMIT 0')
finally:
    db.stop()
'''
        with launch_environment(self.registry):
            result = subprocess.run([str(root / 'runtime/python/python.exe'), '-B', '-c', program],
                                    cwd=root, capture_output=True, timeout=90,
                                    **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        return result.returncode == 0


def stop_and_wait(state, stop):
    """A stopped journal can precede the supervisor's final OS/Job cleanup.

    Hold its verified process reference before requesting stop and wait for that
    exact process, never a PID name match or an arbitrary sleep/forced signal.
    """
    from psutil import NoSuchProcess
    from .process_lifecycle import ProcessReference
    with ExitStack() as stack:
        reference = None
        if state.get('status') != 'stopped':
            try:
                reference = stack.enter_context(ProcessReference(state['supervisor']))
            except NoSuchProcess:
                pass
        stop()
        if reference is not None:
            reference.wait(15)


class StoppedPackages:
    def __init__(self, record):
        self.record, self.stack = record, ExitStack()

    def __enter__(self):
        from .process_lifecycle import InstanceLock, Runtime, child_tree_alive, LifecycleError
        try:
            for item in sorted(self.record['components'], key=lambda c: c['component_id']):
                if item['kind'] == 'client':
                    continue
                modes = ['hub'] if item['kind'] == 'hub' else ['router-dev', 'router-prod']
                for mode in modes:
                    state_path = canonical_path(Path(item['state_path']) / mode)
                    self.stack.enter_context(InstanceLock(state_path, 'operation', timeout=0))
                    self.stack.enter_context(InstanceLock(state_path, 'instance', timeout=0))
                    state = Runtime(item['code_path'], mode).read()
                    if state:
                        # The instance lock is held. As in StoppedInstallation,
                        # a process can have left Runtime and continue unrelated
                        # work; its historic supervisor PID is no longer a gate.
                        # Current children, DB and exact executable roots still
                        # require independent quiescence checks below.
                        if state['status'] != 'stopped' or any(
                                child_tree_alive(c) for c in state['children'].values()):
                            self.stack.close()
                            return False
                        if state.get('database'):
                            from .native_postgres import Postgres
                            if Postgres(**state['database']).owner() is not None:
                                self.stack.close()
                                return False
            # Include unknown/unmanaged processes using these exact roots/data.
            from .migration_host import StoppedInstallation
            check = StoppedInstallation.__new__(StoppedInstallation)
            check.record = self.record
            check._unmanaged_consumers()
            return True
        except LifecycleError:
            self.stack.close()
            return False
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()
