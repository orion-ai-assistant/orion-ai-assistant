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
from .process_lifecycle import LifecycleError
from .update_gate import TERMINAL

INTERVAL = 6 * 60 * 60


def read_json(path, default):
    path = canonical_path(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else deepcopy(default)


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
    if not isinstance(targets, dict) or not targets or set(targets) - {'hub', 'client', 'router'}:
        raise UpdateError('Unknown stable component target')
    from .release_packages import archive_name
    for name in targets.values():
        archive_name(name)
    if len(set(targets.values())) != len(targets):
        raise UpdateError('Duplicate release targets')
    return value


class AutomaticUpdates:
    def __init__(self, registry, *, helper=None, client_factory=None, clock=None, scope='hub',
                 bootstrap_root=None, bootstrap_probe=None):
        if scope not in {'hub', 'router'}:
            raise UpdateError('Unknown update preference scope')
        self.scope = scope
        self.registry = registry if isinstance(registry, Registry) else Registry(registry)
        self.helper = helper or UpdateHelper(self.registry)
        self.base = self.helper.base
        self.clock = clock or time.time
        self.client_factory = client_factory
        # Root of the retained bootstrap this process runs from (None outside a bootstrap).
        self.bootstrap_root = bootstrap_root
        self.bootstrap_probe = bootstrap_probe

    def new_client(self, source):
        # python-tuf's Updater refreshes once per instance; every check needs its own.
        from .release_packages import ReleaseClient
        return self.client_factory() if self.client_factory else ReleaseClient(
            trusted_root=self.registry.path.parent / 'bootstrap/trusted-root.json',
            cache=self.registry.path.parent / 'release-cache',
            metadata_url=source['metadata_url'], targets_url=source['targets_url'])

    def policy(self):
        defaults = {'schema': 1, 'paused': False, 'deferred_until': 0}
        if self.scope == 'router':
            defaults['enabled'] = False
        value = read_json(self.base / ('router-policy.json' if self.scope == 'router' else 'policy.json'), defaults)
        if (not isinstance(value, dict) or set(value) != set(defaults)
                or value['schema'] != 1 or type(value['paused']) is not bool
                or (self.scope == 'router' and type(value['enabled']) is not bool)
                or type(value['deferred_until']) not in (int, float) or value['deferred_until'] < 0):
            raise UpdateError('Invalid automatic update policy')
        return value

    def source(self):
        value = read_json(self.registry.path.parent / 'bootstrap/release-source.json', None)
        return validate_source(value) if value is not None else None

    def preferences(self, *, enabled=None, paused=None, defer_hours=None):
        with operation_lock(self.registry.path, timeout=0):
            policy = self.policy()
            if enabled is not None:
                if type(enabled) is not bool:
                    raise UpdateError('Expected boolean update preference')
                if enabled and self.source() is None:
                    raise UpdateError('Release source is not configured')
                def change(record):
                    record['preferences']['automatic_updates'] = enabled
                    return record
                if self.scope == 'hub':
                    self.registry.update(change)
                else:
                    policy['enabled'] = enabled
            if paused is not None:
                if type(paused) is not bool:
                    raise UpdateError('Expected boolean network pause')
                policy['paused'] = paused
            if defer_hours is not None:
                if defer_hours not in (0, 24):
                    raise UpdateError('Deferral must be zero or 24 hours')
                policy['deferred_until'] = self.clock() + 86400 if defer_hours else 0
            write_json(self.base / ('router-policy.json' if self.scope == 'router' else 'policy.json'), policy)
        return self.status()

    def allowed(self, *, activate=False):
        policy = self.policy()
        enabled = policy['enabled'] if self.scope == 'router' else self.registry.read()['preferences']['automatic_updates']
        return (enabled
                and (policy['deferred_until'] <= self.clock() if activate else not policy['paused']))

    def status(self):
        record = self.registry.read()
        tx = self.helper.status()
        state = read_json(self.state_path, {'schema': 1})
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
        return {'schema': 1, 'automatic_updates': self.policy().get('enabled', record['preferences']['automatic_updates']),
                'configured': self.source() is not None, 'channel': 'stable', 'versions': versions,
                'policy': self.policy(), 'last_attempt': state.get('last_attempt'),
                'last_success': state.get('last_success'), 'bootstrap': self._bootstrap_summary(state), 'error': state.get('error') or
                (tx['phase'] if tx and tx['phase'] in {'failed', 'rolled_back', 'repair_required'} else None),
                'pending': pending, 'history': history, 'observed_at': self.clock(),
                'activation': 'next_safe_launch', 'router_independent': True}

    def _bootstrap_summary(self, state):
        from . import bootstrap_update
        try:
            return {**bootstrap_update.summary(self.registry), 'error': state.get('bootstrap_error')}
        except (OSError, ValueError):
            return None

    @property
    def state_path(self):
        return self.base / ('router-automatic.json' if self.scope == 'router' else 'automatic.json')

    def check(self, *, manual=False, now=False):
        """`now` skips only the 6-hour spacing (user pressed refresh); policy still applies."""
        # Separate OS lock prevents two schedulers sharing TUF metadata/cache.
        with operation_lock(self.base / 'automatic.json', timeout=0):
            source = self.source()
            if source is None or (not manual and not self.allowed()):
                return
            try:
                state = read_json(self.state_path, {'schema': 1})
            except ValueError:
                state = {'schema': 1}  # An unreadable state file must never stop update checks.
            if not manual and not now and self.clock() - state.get('last_attempt', 0) < INTERVAL:
                return
            pending = self.helper.status()
            if pending and pending['phase'] not in TERMINAL:
                return
            state.update(last_attempt=self.clock(), error=None, bootstrap_error=None)
            write_json(self.state_path, state)
            try:
                if self.scope == 'hub' and self._adopt_bootstrap(source, state):
                    # The new bootstrap's watcher takes over and checks components at once.
                    state['last_attempt'] = 0
                    return
                client = self.new_client(source)
                record = self.registry.read()
                selected = {k: v for k, v in source['targets'].items()
                            if (k == 'router') == (self.scope == 'router')
                            and any(c['kind'] == k for c in record['components'])}
                if not selected:
                    return
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
                write_json(self.state_path, state)
                if self.bootstrap_root is not None:
                    from . import bootstrap_update
                    bootstrap_update.confirm(self.registry, self.bootstrap_root)
                if names:
                    # Recheck under the helper's operation lock, including user disabling it.
                    class Selection:
                        def check(self, requested):
                            return checked, {name: targets[name] for name in requested}

                        def prepare(self, target, destination):
                            return client.prepare(target, destination)
                    self.helper.prepare(Selection(), names, automatic=not manual, automatic_scope=self.scope)
            except Exception as error:
                # Never persist URLs, subprocess output, credentials or server error bodies.
                state['error'] = type(error).__name__
            finally:
                write_json(self.state_path, state)

    def _adopt_bootstrap(self, source, state):
        """Newer signed bootstrap -> prepared beside the running one and made active."""
        from . import bootstrap_update
        try:
            return bootstrap_update.adopt(self, source, **(
                {'probe': self.bootstrap_probe} if self.bootstrap_probe else {})) is not None
        except Exception as error:
            # A failed bootstrap update must never stop Hub/Client/Router checks.
            state['bootstrap_error'] = getattr(error, 'code', type(error).__name__)
            return False

    def startup(self):
        tx = self.helper.status()
        if tx and tx['phase'] in {'checking', 'downloading', 'verifying'}:
            # A running download owns the operation lock. Never race it.
            return self.helper.resume(automatic=True)
        if tx and tx['phase'] in {'staged', 'waiting_for_idle'}:
            return self.helper.resume(automatic=True)
        # Critical recovery remains explicitly accessible even when preference is off.
        return tx


def _usable_root(value):
    root = canonical_path(value, must_exist=True)
    validate_private_directory(root)
    for name in ('bootstrap.json', 'runtime/python/python.exe', 'scripts/release/update_automatic.py'):
        canonical_path(root / name, must_exist=True)
    return root


def runtime_command(registry):
    """Only an installer-owned, independently retained bootstrap may run updates."""
    pointer = read_json(registry.path.parent / 'bootstrap/automatic-runtime.json', None)
    if pointer is None:
        return None
    if not isinstance(pointer, dict) or set(pointer) != {'schema', 'root'} or pointer['schema'] != 1:
        raise UpdateError('Invalid automatic runtime pointer')
    try:
        root = _usable_root(pointer['root'])
    except (OSError, ValueError):
        # An unconfirmed new bootstrap that vanished or is incomplete: use the previous one.
        from . import bootstrap_update
        if not bootstrap_update.fallback(registry, pointer['root']):
            raise
        root = _usable_root(read_json(registry.path.parent / 'bootstrap/automatic-runtime.json', None)['root'])
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


def record_check_failure(registry, returncode):
    """A Hub-triggered check process that exits non-zero must not fail silently.

    Exit 2 is argparse refusing the arguments: the retained bootstrap predates them.
    Only a fixed error class is stored, never process output.
    """
    automatic = AutomaticUpdates(registry)
    try:
        with operation_lock(automatic.base / 'automatic.json', timeout=0):
            try:
                state = read_json(automatic.state_path, {'schema': 1})
            except ValueError:
                state = {'schema': 1}
            state['error'] = 'BootstrapIncompatible' if returncode == 2 else 'CheckProcessFailed'
            write_json(automatic.state_path, state)
    except (LifecycleError, OSError):
        pass  # A running check owns the state file and records its own outcome.


def watch(automatic):
    # Launched by the outer launcher, never by a Hub-owned Job descendant.
    # A user may close Hub while a download is in flight; preparation finishes
    # here and never turns into activation. The next poll exits when stopped.
    with operation_lock(automatic.base / 'watcher.json', timeout=0):
        if automatic.bootstrap_root is not None:
            from . import bootstrap_update
            bootstrap_update.note_started(automatic.registry, automatic.bootstrap_root)
        while automatic.source() is not None:
            if automatic.bootstrap_root is not None and bootstrap_update.handover_due(
                    automatic.registry, automatic.bootstrap_root):
                return 'handover'  # Lock is released on return; the caller starts the new watcher.
            with automatic.helper.host.stopped(automatic.registry.read()) as stopped:
                if stopped:
                    return
            from . import update_now
            # Explicit "update now" runs here, outside the Hub-owned Job that its own stop ends.
            update_now.process_request(automatic)
            try:
                automatic.check()
                router = AutomaticUpdates(automatic.registry, helper=automatic.helper, scope='router')
                request = canonical_path(automatic.base / 'router-request.json')
                if request.exists():
                    value = read_json(request, {})
                    if value == {'schema': 1, 'installation_id': automatic.registry.read()['installation_id'], 'action': 'prepare'}:
                        request.unlink()
                        router.check(manual=True)
                    else:
                        raise UpdateError('Invalid Router update request')
                router.check()
            except (UpdateError, OSError, ValueError):
                pass  # A bad state/request file must not silently kill the watcher; retry next poll.
            time.sleep(5)
