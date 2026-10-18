"""Stage 06 local, restartable package transaction; no scheduler or remote API.

Activation is deliberately stopped-only for the Stage 05 baseline. A running
installation stays in waiting_for_idle; the helper never interrupts user work.
Hub/Client and independently selected Router packages use the same transaction.
Schema changes and bootstrap replacement fail closed, never restore a stale DB.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from .installation_registry import Registry, RegistryError, operation_lock, _atomic_write, validate_record
from .installation_paths import canonical_path, validate_private_directory
from .release_packages import (PackageError, compatible, record_prepared_package,
                               verify_package_record, sha256)
from .update_gate import TERMINAL
from . import update_log

CONSUMER_WAIT = 20  # seconds the product's own processes get to exit/settle before "not idle"
PHASES = ('checking', 'downloading', 'verifying', 'staged', 'waiting_for_idle',
          'draining', 'backing_up', 'switching', 'health_check', 'completed',
          'failed', 'rollback_pending', 'rolled_back', 'repair_required')


WORKERS = 8  # Per-file cost on Windows is mostly latency (Defender, open/close); threads overlap it.
# Share of the whole "update now" bar that each journal phase covers (the window reads `percent`).
SPANS = {'draining': (8, 10), 'backing_up': (10, 45), 'switching': (45, 55), 'health_check': (55, 95)}
# Release capability: the component waits for admission at runtime (requests, background work, discovery), so a
# candidate started quarantined for its health check may be admitted in place instead of being restarted.
IN_PLACE = 'lifecycle.update-admission-in-place.v1'


class UpdateError(ValueError):
    pass


def walk(tree):
    """(directories, files) relative to `tree`; links, junctions and missing disks are rejected."""
    tree = canonical_path(tree, must_exist=True)
    directories, files = [], []
    for path in tree.rglob('*'):
        canonical_path(path, must_exist=True)
        relative = path.relative_to(tree).as_posix()
        if path.is_file():
            files.append(relative)
        elif path.is_dir():
            directories.append(relative)
    return directories, files


def hash_files(tree, files, tick=None):
    tree = Path(tree)
    digests = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for name, digest in zip(files, pool.map(lambda name: sha256(tree / name), files)):
            digests[name] = digest
            if tick:
                tick()
    return digests


def inventory(tree):
    tree = canonical_path(tree, must_exist=True)
    return hash_files(tree, walk(tree)[1])


def percent(phase, done, total):
    low, high = SPANS[phase]
    return round(low + (high - low) * min(max(done, 0), total) / total) if total else low


def _copy_file(source, destination):
    # One open per file: copy, flush and fsync before close (no second pass over the backup).
    with open(source, 'rb') as reader, open(destination, 'wb') as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination)


def _signature(root, files):
    result = {}
    for name in files:
        info = (Path(root) / name).stat()
        result[name] = (info.st_size, info.st_mtime_ns)
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
        self._verified = set()  # (kind, inventory) already hashed in this stopped, locked window

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
        tx.update(fields, phase=phase, updated_at=time.time())
        self._json(self._directory(tx) / 'journal.json', tx, 'journal:' + phase)
        return tx

    @contextmanager
    def _timed(self, tx, label):
        """Wall-clock per step into watcher.log and the journal (`timings`); diagnostics only."""
        started = time.monotonic()
        try:
            yield
        finally:
            seconds = round(time.monotonic() - started, 2)
            tx.setdefault('timings', {})[label] = seconds
            update_log.log(self.base, 'update.timing', step=label, seconds=seconds)

    def _progress(self, tx, done, total, *, component=None, force=False, unit='steps'):
        """Real progress for the updating window. Throttled; never fails the transaction."""
        now = time.monotonic()
        if not force and now - getattr(self, '_progress_at', 0) < 0.4:
            return
        self._progress_at = now
        tx['progress'] = self._shape(tx['phase'], done, total, component=component, unit=unit)
        try:
            self._json(self._directory(tx) / 'journal.json', tx, 'progress')
        except OSError:
            pass

    @staticmethod
    def _shape(phase, done, total, *, component=None, unit='steps'):
        return {'done': done, 'total': total, 'component': component, 'unit': unit,
                'percent': percent(phase, done, total)}

    def _directory(self, tx):
        identity = tx['operation_id']
        if str(uuid.UUID(identity)) != identity:
            raise UpdateError('Invalid operation identity')
        return canonical_path(self.base / identity, must_exist=True)

    @staticmethod
    def _read_journal(path):
        # The journal is replaced many times a second while a download reports progress; on Windows a reader
        # can hit the replace for a moment. Only a read that keeps failing is a damaged journal.
        for attempt in range(5):
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                if attempt == 4:
                    raise
                time.sleep(0.05)

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
            tx = self._read_journal(path)
            if (tx['schema'] != 1 or tx['operation_id'] != identity or tx['phase'] not in PHASES
                    or tx['installation_id'] != self.registry.read()['installation_id']):
                raise ValueError()
            validate_record(tx['previous'])
            if tx.get('proposed') is not None:
                validate_record(tx['proposed'])
            return tx
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise UpdateError('Update journal invalid; explicit repair required') from error

    def idle_now(self, tx):
        """Cheap "would applying work right now" decision (no hashing, no waiting, nothing stopped):
        no other update operation is running and no service or leftover process holds the installation."""
        from .process_lifecycle import LifecycleError
        try:
            with operation_lock(self.registry.path, timeout=0):
                pass
        except LifecycleError:
            return False
        quiet = getattr(self.host, 'quiet_now', None)
        return True if quiet is None else bool(quiet(tx['previous']))

    def prepare(self, client, names, *, automatic=False, automatic_scope='hub'):
        with operation_lock(self.registry.path, timeout=0):
            if automatic:
                from .automatic_updates import AutomaticUpdates
                if not AutomaticUpdates(self.registry, helper=self, scope=automatic_scope).allowed():
                    return None
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
            self._guard_entry()
            try:
                status, targets = client.check(names)
                if targets is None:
                    raise UpdateError('Signed release check unavailable: ' + status['reason'])
                proposed = deepcopy(old)
                seen = set()
                for target in targets.values():
                    release = target.custom
                    kind = release['component']
                    if kind not in {'hub', 'client', 'router'} or kind in seen or kind not in manifests:
                        raise UpdateError('Select one signed target per registered component')
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
                if 'hub' in manifests and 'lifecycle.update-quarantine.v1' not in manifests['hub'].get('capabilities', []):
                    raise UpdateError('Proposed Hub needs lifecycle.update-quarantine.v1; update Hub with Client')
                if 'router' in manifests and 'lifecycle.update-quarantine.v1' not in manifests['router'].get('capabilities', []):
                    raise UpdateError('Proposed Router needs lifecycle.update-quarantine.v1')
                overall_total = sum(t.length for t in targets.values())
                completed_bytes = 0
                for number, target in enumerate(targets.values(), 1):
                    kind = target.custom['component']
                    item = next(c for c in proposed['components'] if c['kind'] == kind)
                    tree = canonical_path(Path(item['code_path']).parent / target.custom['package_id'])
                    # The app card shows "indiriliyor (1/3)": which component of how many is being fetched.
                    # After the bytes: "doğrulanıyor / hazırlanıyor (1/3)" (hash check, extraction, inventory).
                    verifying = {'done': number - 1, 'total': len(targets), 'component': kind}

                    def report(received, total, seconds, *, number=number, kind=kind, before=completed_bytes,
                               verifying=verifying):
                        if received >= total:
                            self._phase(tx, 'verifying', progress=verifying)
                            return
                        self._phase(tx, 'downloading', progress={
                            'done': number - 1, 'total': len(targets), 'component': kind,
                            'bytes': before + received, 'bytes_total': overall_total,
                            'rate': int(received / seconds) if seconds > 0 else None})
                    self._phase(tx, 'downloading', progress={
                        'done': number - 1, 'total': len(targets), 'component': kind,
                        'bytes': completed_bytes, 'bytes_total': overall_total, 'rate': None})
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
                        client.prepare(target, tree, progress=report)
                    completed_bytes += target.length
                    self._phase(tx, 'verifying', progress=verifying)
                    # The inventory is versioned with the transaction, not yet
                    # published as the active component's installed receipt.
                    # Process ownership records embed the immutable code root.
                    # Give each release its own external lifecycle state; never
                    # rewrite/adopt the old release's supervisor/Job identity.
                    new_state = canonical_path(tree.parent.parent / 'state/releases' / target.custom['package_id'])
                    validate_private_directory(new_state)
                    new_state.mkdir(parents=True, exist_ok=True)
                    receipt_dir = directory / kind
                    receipt_dir.mkdir()
                    # The full hash of the tree; its size/mtime cache lands in the release's state folder.
                    receipt = record_prepared_package(tree, receipt_dir, target, cache=new_state)
                    receipt_path = receipt_dir / 'installed-package.json'
                    self._write(directory / (kind + '-candidate.json'), receipt_path.read_bytes(), 'candidate:' + kind)
                    item.update(code_path=str(tree), state_path=str(new_state), package=receipt)
                    # Pins the receipt to the registry record; the bytes were hashed just above.
                    verify_package_record(item, inventory=directory / (kind + '-candidate.json'), quick=True)
                    tx['changes'].append(kind)
                    tx['verification'][kind] = {'sha256': receipt['sha256'], 'length': receipt['length'], 'verified': True}
                tx['proposed'] = proposed
                return self._phase(tx, 'staged', progress={'done': len(targets), 'total': len(targets), 'component': None})
            except Exception as error:
                self._phase(tx, 'failed', reason=type(error).__name__)
                raise

    def _verify(self, tx, record, candidate, *, quick=False, staged=False):
        # `quick` applies to components this update does not touch: their active release was fully
        # hashed by the launcher, so size+mtime against that cache is enough (falls back to a full hash
        # when no cache exists). `staged` extends it to the changed components, whose new and previous
        # trees were fully hashed at staging/launch; only a straight run from `staged` passes it, a
        # resume after an interruption hashes every changed file. A (kind, inventory) pair verified
        # since the last `_fresh()` is not verified again.
        directory = self._directory(tx)
        for item in record['components']:
            if item['distribution'] == 'legacy-source' and tx.get('conversion'):
                files = inventory(item['code_path'])
                files.pop('.orion-converted.json', None)
                if files != tx['legacy_inventories'][item['kind']]:
                    raise UpdateError('Legacy source/data changed; explicit repair required')
                continue
            changed = item['kind'] in tx['changes']
            suffix = 'candidate' if candidate and changed else 'previous'
            if (item['kind'], suffix) in self._verified:
                continue
            if quick and not changed and not tx.get('conversion'):
                verify_package_record(item, quick=True)
            elif staged and changed and not tx.get('conversion'):
                verify_package_record(item, inventory=directory / (item['kind'] + '-' + suffix + '.json'), quick=True)
            else:
                verify_package_record(item, inventory=directory / (item['kind'] + '-' + suffix + '.json'))
            self._verified.add((item['kind'], suffix))

    def _settle(self, tx, record):
        """Services are stopped: finish a crashed PostgreSQL's recovery (power loss, killed tree) before
        anything starts it, also for component versions whose own start cannot (see postgres_recovery)."""
        settle = getattr(self.host, 'settle', None)
        if settle is not None:
            with self._timed(tx, 'settle.databases'):
                settle(record)

    def _guard_entry(self):
        """This helper's own Orion.ps1: the guard (and the recovery of an apply cut off by power loss) also works
        when the installed Hub predates it. Every Hub launch rewrites the entry with the Hub's copy, so it is
        written again right before the switch starts."""
        entry = self.registry.path.parent / 'bootstrap/Orion.ps1'
        entry.parent.mkdir(exist_ok=True)
        self._write(entry, Path(__file__).with_name('launcher_entry.ps1').read_bytes(), 'launcher-guard')

    def _fresh(self):
        """Forget earlier verifications: something ran, or may have run, since they were made."""
        self._verified.clear()

    def _publish(self, tx, record, candidate):
        self._verify(tx, record, candidate)
        directory = self._directory(tx)
        for item in record['components']:
            if item['distribution'] == 'legacy-source' and tx.get('conversion'):
                continue
            suffix = 'candidate' if candidate and item['kind'] in tx['changes'] else 'previous'
            self._write(Path(item['state_path']) / 'installed-package.json',
                        (directory / (item['kind'] + '-' + suffix + '.json')).read_bytes(), 'inventory:' + item['kind'])
        self.fault('before:registry')
        if candidate and tx.get('conversion'):
            from .legacy_conversion import protect_sources
            protect_sources(self, tx)
        # Preferences may change while a staged release waits for the next launch.
        # Preserve them on both publication and rollback, under the operation lock.
        record = deepcopy(record)
        record['preferences'] = self.registry.read()['preferences']
        self.registry.write(record)
        self.fault('after:registry')

    @staticmethod
    def backup_kinds(tx):
        """Components whose data is backed up before the switch.

        Everything when the Hub changes (it owns identity, keys and the database) and for a legacy
        conversion; otherwise only the changed components. Data schemas cannot change here (prepare
        refuses it), so an untouched component's data is neither written by the switch nor needed
        by a rollback (rollback never restores data)."""
        kinds = [c['kind'] for c in tx['previous']['components']]
        if tx.get('conversion') or not tx['changes'] or 'hub' in tx['changes']:
            return kinds
        return [kind for kind in kinds if kind in tx['changes']]

    def _backup(self, tx):
        # Offline physical backup only: host's gate has verified supervisor,
        # descendants and PostgreSQL ownership and holds their lifecycle locks.
        directory = self._directory(tx)
        snapshots = {}
        with self._timed(tx, 'backup.scan'):
            plans = []
            for item in tx['previous']['components']:
                if item['kind'] in self.backup_kinds(tx):
                    source = canonical_path(item['data_path'], must_exist=True)
                    plans.append((item['kind'], source, *walk(source)))
        total = 3 * sum(len(files) for *_, files in plans)  # hash source, copy, hash copy
        done = 0

        def tick():
            nonlocal done
            done += 1
            self._progress(tx, done, total, unit='files')
        self._progress(tx, 0, total, unit='files', force=True)
        for kind, source, directories, files in plans:
            with self._timed(tx, 'backup.hash-source.' + kind):
                digests = hash_files(source, files, tick)
            signature = _signature(source, files)
            size = sum(value[0] for value in signature.values())
            if shutil.disk_usage(directory).free < size + 64 * 1024**2:
                raise UpdateError('Insufficient backup disk space')
            # An interrupted copy is retained as evidence; never reused as a
            # complete backup. No active data or previous release is deleted.
            dest = directory / ('backup-' + kind + '-' + str(uuid.uuid4()))
            with self._timed(tx, 'backup.copy.' + kind):
                dest.mkdir()
                for name in directories:
                    (dest / name).mkdir(parents=True, exist_ok=True)
                with ThreadPoolExecutor(WORKERS) as pool:
                    for _ in pool.map(lambda name: _copy_file(source / name, dest / name), files):
                        tick()
            with self._timed(tx, 'backup.hash-copy.' + kind):
                copied = hash_files(dest, files, tick)
            # The copy is proven byte-identical to the hashes taken before it. Services are stopped and
            # their locks held, so the source cannot legitimately change; a stat comparison (size and
            # mtime, not a third and fourth hash) still detects a straggler writing during the copy.
            if copied != digests or set(walk(dest)[1]) != set(files) or _signature(source, files) != signature:
                raise UpdateError('Offline backup changed during copy')
            snapshots[kind] = {'path': str(dest), 'files': digests}
            self.fault('after:backup:' + kind)
        tx['backups'] = snapshots
        self._json(directory / 'backups.json', snapshots, 'backups')

    def _verify_backups(self, tx):
        """Full proof of the backup; used when a transaction is resumed after an interruption."""
        if set(tx['backups']) != set(self.backup_kinds(tx)):
            raise UpdateError('Complete offline backup required')
        for snapshot in tx['backups'].values():
            path = canonical_path(snapshot['path'], must_exist=True)
            if not path.is_relative_to(self._directory(tx)) or inventory(path) != snapshot['files']:
                raise UpdateError('Backup inventory mismatch')

    @staticmethod
    def in_place(tx):
        """Kinds that stay running after a passing health check and are admitted in place.

        Only components whose own (just verified) manifest declares IN_PLACE: they gate every user-facing start
        on admission at runtime, not once at startup. The Client is served by the Hub's supervisor and follows
        it. A conversion, or a Hub without it, ends health fully stopped as before."""
        def capable(item):
            try:
                release = json.loads((Path(item['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))
                return IN_PLACE in release.get('capabilities', [])
            except (OSError, ValueError, AttributeError):
                return False
        items = {c['kind']: c for c in tx['proposed']['components']}
        if tx.get('conversion') or ('hub' in items and not capable(items['hub'])):
            return set()
        keep = {'hub', 'client'} & set(items)
        if 'router' in items and capable(items['router']):
            keep.add('router')
        return keep

    def _admit(self, tx, result, keep, before_admission):
        """The last step under closed admission: the caller's result record, then `completed` opens admission
        (for a candidate that kept running, right where it runs). A crash before the journal write resumes
        from health_check: the running candidate is stopped and checked again, or rolled back."""
        if before_admission is not None:
            try:
                before_admission(tx)
            except Exception as error:  # A result record is cosmetic; it never fails a healthy update.
                update_log.log_exception(self.base, 'update.before-admission', error)
        tx = self._phase(tx, 'completed', health=result, running=sorted(keep))
        update_log.log(self.base, 'update.admitted', running=','.join(sorted(keep)) or 'none')
        return tx

    def resume(self, *, automatic=False, before_admission=None):
        """`before_admission(tx)` runs once the update is certain to complete, while admission is still closed."""
        with operation_lock(self.registry.path, timeout=0):
            tx = self.status()
            if tx is None or tx['phase'] in TERMINAL or tx['phase'] == 'repair_required':
                return tx
            if tx.get('conversion') and not tx.get('conversion_data_ready'):
                return self._phase(tx, 'repair_required', reason='Interrupted legacy copy; original data retained; explicit rollback required')
            if automatic and tx['phase'] in {'staged', 'waiting_for_idle'}:
                from .automatic_updates import AutomaticUpdates
                scope = 'router' if tx['changes'] == ['router'] else 'hub'
                if not AutomaticUpdates(self.registry, helper=self, scope=scope).allowed(activate=True):
                    return tx
            if tx['phase'] in {'checking', 'downloading', 'verifying'}:
                return self._phase(tx, 'failed', reason='Preparation interrupted; active version unchanged; prepare again')
            current = self.registry.read()
            if not any(same_installation(current, value) for value in (tx['previous'], tx['proposed'])):
                return self._phase(tx, 'repair_required', reason='Registry changed outside this transaction')
            if tx['phase'] == 'rollback_pending':
                return self._rollback(tx)
            self._fresh()
            # Straight from staging (nothing switched or interrupted yet): the staged and previous trees keep
            # the size/mtime cache of their last full hash, so unchanged files are not hashed a second time.
            straight = tx['phase'] in {'staged', 'waiting_for_idle'}
            try:
                with self._timed(tx, 'verify.start'):
                    self._verify(tx, tx['proposed'], True, quick=True, staged=straight)
                    if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                        self._verify(tx, tx['previous'], False, quick=True, staged=straight)
                if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                    with self._timed(tx, 'stopped.enter'):
                        stopped = self.host.stopped(tx['previous'])
                        idle = stopped.__enter__()
                    try:
                        if not idle:
                            return self._phase(tx, 'waiting_for_idle')
                        self._settle(tx, tx['previous'])
                        self._guard_entry()
                        self._phase(tx, 'draining', progress=self._shape('draining', 0, 1))
                        self._phase(tx, 'backing_up', progress=self._shape('backing_up', 0, 1, unit='files'))
                        self._backup(tx)
                        self._phase(tx, 'switching', progress=self._shape('switching', 0, 2))
                        with self._timed(tx, 'switch'):
                            # Straight-through: `_backup` just hashed the copy against the source hashes,
                            # so no further backup pass here (a resumed run re-proves it in full).
                            self._progress(tx, 1, 2, force=True)
                            self._publish(tx, tx['proposed'], True)
                    finally:
                        stopped.__exit__(None, None, None)
                else:
                    # A process may have survived its helper. Quarantine stays
                    # closed until it is stopped with verified ownership.
                    self.host.stop(current)
                    with self.host.stopped(current) as idle:
                        if not idle:
                            raise UpdateError('Candidate processes could not be reconciled')
                        self._settle(tx, current)
                        self._verify_backups(tx)  # Recovery after a restart: full proof of the backup.
                        self._publish(tx, tx['proposed'], True)
                if tx['attempts'] >= 2:
                    raise UpdateError('Health retry limit reached')
                self._phase(tx, 'health_check', attempts=tx['attempts'] + 1, progress=self._shape('health_check', 0, 3))
                with self._timed(tx, 'health.probe'):
                    result = self.host.health(tx['proposed'])
                self._progress(tx, 1, 3, force=True)
                required = ({'hub_api', 'worker', 'database', 'client_api', 'router_identity'}
                            if any(c['kind'] == 'hub' for c in tx['proposed']['components'])
                            else {'database', 'router_identity'})
                if set(result) != required or not all(v is True for v in result.values()):
                    raise UpdateError('Candidate health failed')
                # A healthy candidate that opens admission in place keeps running: the launcher only attaches to
                # it. The rest ends health stopped. Either way no user job has been admitted yet, and nothing
                # below opens admission before `completed`; any failure until then stops it and rolls back.
                keep = self.in_place(tx)
                halt = dict(tx['proposed'], components=[c for c in tx['proposed']['components'] if c['kind'] not in keep])
                with ExitStack() as held:
                    if halt['components']:
                        with self._timed(tx, 'health.stop'):
                            self.host.stop(halt)
                            if not held.enter_context(self.host.stopped(halt)):
                                raise UpdateError('Health shutdown unverified')
                    self._progress(tx, 2, 3, force=True)
                    # The candidate ran: check every file again (size, mtime and file set against the
                    # cache, the same check every launch makes; a resumed run hashes everything).
                    self._fresh()
                    with self._timed(tx, 'verify.final'):
                        self._verify(tx, tx['proposed'], True, quick=True, staged=straight)
                    return self._admit(tx, result, keep, before_admission)
            except Exception as error:
                update_log.log_exception(self.base, 'update.resume', error)
                if tx['phase'] in {'staged', 'waiting_for_idle', 'draining', 'backing_up'}:
                    # Nothing has switched. In particular, a corrupt download
                    # must never cause rollback to stop a still-busy old Hub.
                    return self._phase(tx, 'failed', reason=type(error).__name__)
                self._phase(tx, 'rollback_pending', reason=type(error).__name__)
                return self._rollback(tx)

    def _rollback(self, tx):
        self._fresh()
        try:
            current = self.registry.read()
            if not any(same_installation(current, value) for value in (tx['previous'], tx['proposed'])):
                raise UpdateError('Registry changed outside this transaction')
            self.host.stop(current)
            with self.host.stopped(current) as idle:
                if not idle:
                    raise UpdateError('Rollback consumers still running')
                self._settle(tx, current)
                # Same-schema only. Keep all data, including candidate startup
                # writes. Backups require explicit repair, never silent restore.
                self._publish(tx, tx['previous'], False)
                if not self.host.recovery_health(tx['previous']):
                    raise UpdateError('Previous database/schema needs explicit repair')
                if tx.get('conversion'):
                    from .legacy_conversion import unprotect_sources
                    # Keep admission closed until the old source guard is removed.
                    if any(c['distribution'] == 'packaged' for c in current['components']):
                        with self.host.stopped(tx['previous']) as old_idle:
                            if not old_idle:
                                raise UpdateError('Legacy rollback consumers still running')
                            unprotect_sources(self, tx)
                            return self._phase(tx, 'rolled_back')
                    unprotect_sources(self, tx)
                return self._phase(tx, 'rolled_back')
        except Exception as error:
            update_log.log_exception(self.base, 'update.rollback', error)
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


def same_installation(left, right):
    return (isinstance(left, dict) and isinstance(right, dict)
            and {k: v for k, v in left.items() if k != 'preferences'}
            == {k: v for k, v in right.items() if k != 'preferences'})


class NativeHost:
    """Only fixed, registered native lifecycle operations; never arbitrary argv."""
    def __init__(self, registry):
        self.registry = registry

    def stopped(self, record):
        if any(c['distribution'] == 'legacy-source' for c in record['components']):
            from .migration_host import StoppedInstallation
            return StoppedInstallation(self.registry, operation_locked=True)
        return StoppedPackages(record)

    def stopped_now(self, record):
        """`stopped` without the settle wait: the watcher only asks "is Orion off?" between its rounds."""
        if any(c['distribution'] == 'legacy-source' for c in record['components']):
            return self.stopped(record)
        return StoppedPackages(record, wait=0)

    def quiet_now(self, record):
        """Immediate and side-effect free: could the installation be locked and stopped right now?
        Never waits: a Hub that is running, or a tree that is still exiting, is simply "not now"."""
        if any(c['distribution'] == 'legacy-source' for c in record['components']):
            return True  # Legacy installations are gated by their own, unchanged check.
        gate = StoppedPackages(record, wait=0)
        try:
            return bool(gate.__enter__())
        finally:
            gate.__exit__(None, None, None)
            self.refused = gate.refused

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
                    check._unmanaged_consumers(wait=10)
                    continue
                stop = (lambda: stop_hub(item['code_path'], update_operation=operation)) if item['kind'] == 'hub' else (
                    lambda: stop_router(str(Path(item['code_path']) / 'cli.py')))
                stop_and_wait(state, stop)

    def settle(self, record):
        from .postgres_recovery import recover
        recovered = recover(record, log_dir=self.registry.path.parent / 'updates')
        if recovered:
            update_log.log(self.registry.path.parent / 'updates', 'update.database-recovered', count=len(recovered))

    def health(self, record):
        # Implemented in a dedicated module to keep the transaction core free
        # from runtime/DB imports and make health evidence independently testable.
        from .update_health import probe
        return probe(self.registry, record)

    def recovery_health(self, record):
        """Read-only DB check with old runtime; never starts an old Worker/API."""
        import subprocess
        from .launcher import launch_environment
        if not any(c['kind'] == 'hub' for c in record['components']):
            from .update_health import router_database_probe
            return router_database_probe(record)
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
            from .legacy_conversion import runtime_python
            result = subprocess.run([str(runtime_python(hub)), '-B', '-c', program],
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
    def __init__(self, record, wait=CONSUMER_WAIT):
        self.record, self.stack, self.wait = record, ExitStack(), wait
        self.refused = None  # "name:pid" of the process that kept the installation busy, for the log

    def __enter__(self):
        from .process_lifecycle import InstanceLock, Runtime, child_tree_alive, LifecycleError
        from .content_migration import MigrationError
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
            try:
                check._unmanaged_consumers(wait=self.wait)
            except MigrationError as error:
                self.refused = getattr(error, 'offender', None)
                # Still in use after the grace period: not idle yet. The transaction stays
                # waiting_for_idle and is retried; it is not failed for a process that is about to exit.
                self.stack.close()
                return False
            return True
        except LifecycleError:
            self.stack.close()
            return False
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()
