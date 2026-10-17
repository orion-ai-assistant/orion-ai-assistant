"""Stable checks and staging; activation is confined to a stopped launch boundary.

No release service is configured by default. Only a trusted installer supplies
the feed and retained bootstrap runtime. API callers cannot choose URLs or code.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from .installation_registry import Registry, RegistryError, operation_lock, _atomic_write
from .installation_paths import canonical_path, validate_private_directory
from .update_helper import UpdateHelper, UpdateError
from .update_gate import TERMINAL

INTERVAL = 6 * 60 * 60


def read_json(path, default):
    path = canonical_path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else deepcopy(default)


def write_json(path, value):
    canonical_path(path)
    _atomic_write(path, (json.dumps(value, ensure_ascii=True) + '\n').encode())


def validate_source(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'channel', 'metadata_url', 'targets_url', 'targets'}
            or value['schema'] != 1 or value['channel'] != 'stable'):
        raise UpdateError('Invalid stable release source')
    for key in ('metadata_url', 'targets_url'):
        url = urlsplit(value[key])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise UpdateError('Release source requires credential-free HTTPS')
    targets = value['targets']
    if not isinstance(targets, dict) or not targets or set(targets) - {'hub', 'client'}:
        raise UpdateError('Only Hub/Client stable targets supported')
    from .release_packages import archive_name
    for name in targets.values():
        archive_name(name)
    if len(set(targets.values())) != len(targets):
        raise UpdateError('Duplicate release targets')
    return value


class AutomaticUpdates:
    def __init__(self, registry, *, helper=None, client_factory=None, clock=None):
        self.registry = registry if isinstance(registry, Registry) else Registry(registry)
        self.helper = helper or UpdateHelper(self.registry)
        self.base = self.helper.base
        self.clock = clock or time.time
        self.client_factory = client_factory

    def policy(self):
        value = read_json(self.base / 'policy.json', {'schema': 1, 'paused': False, 'deferred_until': 0})
        if (not isinstance(value, dict) or set(value) != {'schema', 'paused', 'deferred_until'}
                or value['schema'] != 1 or type(value['paused']) is not bool
                or type(value['deferred_until']) not in (int, float) or value['deferred_until'] < 0):
            raise UpdateError('Invalid automatic update policy')
        return value

    def source(self):
        value = read_json(self.registry.path.parent / 'bootstrap/release-source.json', None)
        return validate_source(value) if value is not None else None

    def preferences(self, *, enabled=None, paused=None, defer_hours=None):
        with operation_lock(self.registry.path, timeout=0):
            if enabled is not None:
                if type(enabled) is not bool:
                    raise UpdateError('Expected boolean update preference')
                if enabled and self.source() is None:
                    raise UpdateError('Release source is not configured')
                def change(record):
                    record['preferences']['automatic_updates'] = enabled
                    return record
                self.registry.update(change)
            policy = self.policy()
            if paused is not None:
                if type(paused) is not bool:
                    raise UpdateError('Expected boolean network pause')
                policy['paused'] = paused
            if defer_hours is not None:
                if defer_hours not in (0, 24):
                    raise UpdateError('Deferral must be zero or 24 hours')
                policy['deferred_until'] = self.clock() + 86400 if defer_hours else 0
            write_json(self.base / 'policy.json', policy)
        return self.status()

    def allowed(self, *, activate=False):
        policy = self.policy()
        return (self.registry.read()['preferences']['automatic_updates']
                and (policy['deferred_until'] <= self.clock() if activate else not policy['paused']))

    def status(self):
        record = self.registry.read()
        tx = self.helper.status()
        state = read_json(self.base / 'automatic.json', {'schema': 1})
        versions = {}
        for item in record['components']:
            versions[item['kind']] = {'package_id': item.get('package', {}).get('id'),
                                      'distribution': item['distribution']}
        history = []
        # Journal is the durable source, including operations finished while UI was closed.
        for path in sorted(self.base.glob('*/journal.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            value = read_json(path, {})
            if value.get('installation_id') == record['installation_id']:
                history.append({k: value.get(k) for k in ('operation_id', 'phase', 'changes', 'updated_at')})
        pending = None
        if tx and tx['phase'] not in TERMINAL:
            pending = {'phase': tx['phase'], 'operation_id': tx['operation_id'],
                       'packages': {c['kind']: c.get('package', {}).get('id')
                                    for c in (tx.get('proposed') or {}).get('components', [])
                                    if c['kind'] in tx['changes']}}
        return {'schema': 1, 'automatic_updates': record['preferences']['automatic_updates'],
                'configured': self.source() is not None, 'channel': 'stable', 'versions': versions,
                'policy': self.policy(), 'last_attempt': state.get('last_attempt'),
                'last_success': state.get('last_success'), 'error': state.get('error') or
                (tx['phase'] if tx and tx['phase'] in {'failed', 'rolled_back', 'repair_required'} else None),
                'pending': pending, 'history': history, 'observed_at': self.clock(),
                'activation': 'next_safe_launch', 'router_independent': True}

    def check(self):
        # Separate OS lock prevents two schedulers sharing TUF metadata/cache.
        with operation_lock(self.base / 'automatic.json', timeout=0):
            source = self.source()
            if source is None or not self.allowed():
                return
            state = read_json(self.base / 'automatic.json', {'schema': 1})
            if self.clock() - state.get('last_attempt', 0) < INTERVAL:
                return
            pending = self.helper.status()
            if pending and pending['phase'] not in TERMINAL:
                return
            state.update(last_attempt=self.clock(), error=None)
            write_json(self.base / 'automatic.json', state)
            try:
                from .release_packages import ReleaseClient
                client = self.client_factory() if self.client_factory else ReleaseClient(
                    trusted_root=self.registry.path.parent / 'bootstrap/trusted-root.json',
                    cache=self.registry.path.parent / 'release-cache',
                    metadata_url=source['metadata_url'], targets_url=source['targets_url'])
                record = self.registry.read()
                selected = {k: v for k, v in source['targets'].items()
                            if any(c['kind'] == k for c in record['components'])}
                checked, targets = client.check(list(selected.values()))
                if targets is None:
                    raise UpdateError(checked.get('reason', 'CheckUnavailable'))
                names = []
                for kind, name in selected.items():
                    target = targets[name]
                    item = next(c for c in record['components'] if c['kind'] == kind)
                    current = read_json(Path(item['code_path']) / 'orion-release.json', {})
                    if target.custom['component'] != kind:
                        raise UpdateError('Stable target component mismatch')
                    if target.custom['sequence'] > current['sequence']:
                        names.append(name)
                state['last_success'] = self.clock()
                write_json(self.base / 'automatic.json', state)
                if names:
                    # Recheck under the helper's operation lock, including user disabling it.
                    class Selection:
                        def check(self, requested):
                            return checked, {name: targets[name] for name in requested}

                        def prepare(self, target, destination):
                            return client.prepare(target, destination)
                    self.helper.prepare(Selection(), names, automatic=True)
            except Exception as error:
                # Never persist URLs, subprocess output, credentials or server error bodies.
                state['error'] = type(error).__name__
            finally:
                write_json(self.base / 'automatic.json', state)

    def startup(self):
        tx = self.helper.status()
        if tx and tx['phase'] in {'checking', 'downloading', 'verifying'}:
            # A running download owns the operation lock. Never race it.
            return self.helper.resume(automatic=True)
        if tx and tx['phase'] in {'staged', 'waiting_for_idle'}:
            return self.helper.resume(automatic=True)
        # Critical recovery remains explicitly accessible even when preference is off.
        return tx


def runtime_command(registry):
    """Only an installer-owned, independently retained bootstrap may run updates."""
    pointer = read_json(registry.path.parent / 'bootstrap/automatic-runtime.json', None)
    if pointer is None:
        return None
    if not isinstance(pointer, dict) or set(pointer) != {'schema', 'root'} or pointer['schema'] != 1:
        raise UpdateError('Invalid automatic runtime pointer')
    root = canonical_path(pointer['root'], must_exist=True)
    validate_private_directory(root)
    for name in ('bootstrap.json', 'runtime/python/python.exe', 'scripts/release/update_automatic.py'):
        canonical_path(root / name, must_exist=True)
    return [str(root / 'runtime/python/python.exe'), '-B', str(root / 'scripts/release/update_automatic.py'),
            '--registry', str(registry.path)]


def run_startup(registry):
    command = runtime_command(registry)
    if command:
        result = subprocess.run(command + ['startup'], capture_output=True, timeout=600,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RegistryError('Update launch boundary failed; inspect local update status/repair')


def spawn_watcher(registry):
    command = runtime_command(registry)
    if command:
        # The separately retained worker survives Hub/browser closure, never activates.
        subprocess.Popen(command + ['watch'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def watch(automatic):
    # Launched by the outer launcher, never by a Hub-owned Job descendant.
    # A user may close Hub while a download is in flight; preparation finishes
    # here and never turns into activation. The next poll exits when stopped.
    with operation_lock(automatic.base / 'watcher.json', timeout=0):
        while automatic.source() is not None:
            with automatic.helper.host.stopped(automatic.registry.read()) as stopped:
                if stopped:
                    return
            automatic.check()
            time.sleep(300)
