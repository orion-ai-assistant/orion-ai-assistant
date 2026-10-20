"""Stable checks and staging; activation is confined to a stopped launch boundary.

No release service is configured by default. Only a trusted installer supplies
the feed and retained bootstrap runtime. API callers cannot choose URLs or code.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

from .installation_registry import Registry, RegistryError, operation_lock, _atomic_write
from .installation_paths import canonical_path, ensure_private_directory, validate_private_directory
from .update_helper import UpdateHelper, UpdateError
from .process_lifecycle import LifecycleError
from .update_gate import APPLYING, TERMINAL
from .release_packages import PackageError
from . import update_log

INTERVAL = 6 * 60 * 60
# Downloaded and verified, waiting to be applied: a check still runs and a newer release replaces it.
WAITING = {'staged', 'waiting_for_idle'}
RETRY_PAUSE = 3  # seconds before the single retry of a feed that failed while the bootstrap replaced itself
# Last unattended skip logged per (updates folder, scope): the watcher polls every 5 s and a staged
# update would otherwise write the same "check.skipped" line twelve times a minute.
_logged_skips = {}


def read_json(path, default):
    path = canonical_path(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else deepcopy(default)


def write_json(path, value):
    canonical_path(path)
    _atomic_write(path, (json.dumps(value, ensure_ascii=True) + '\n').encode())


LEGACY_CACHE = 'release-cache'
COMPONENT_TARGETS = {'hub', 'client', 'router'}


def _validate_feed(value, keys):
    if not isinstance(value, dict) or not {'metadata_url', 'targets_url', 'targets'} <= set(value) <= keys:
        raise UpdateError('Invalid stable release source')
    for key in ('metadata_url', 'targets_url'):
        url = urlsplit(value[key])
        if (not isinstance(value[key], str) or url.scheme != 'https' or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise UpdateError('Release source requires credential-free HTTPS')
    targets = value['targets']
    if not isinstance(targets, dict) or not targets or set(targets) - COMPONENT_TARGETS:
        raise UpdateError('Unknown stable component target')
    from .release_packages import archive_name
    for name in targets.values():
        archive_name(name)
    if len(set(targets.values())) != len(targets):
        raise UpdateError('Duplicate release targets')
    cache = value.get('cache')
    if cache is not None and not (isinstance(cache, str) and re.fullmatch(r'release-cache(-[a-z0-9]{1,16})?', cache)):
        raise UpdateError('Invalid release cache name')


def validate_source(value):
    """Schema 1 is one feed (the original form); schema 2 lists several feeds, each with its own
    metadata/targets URL, targets and TUF cache (Hub/Client feed + Router feed)."""
    if not isinstance(value, dict) or value.get('channel') != 'stable':
        raise UpdateError('Invalid stable release source')
    if value.get('schema') == 1:
        if set(value) != {'schema', 'channel', 'metadata_url', 'targets_url', 'targets'}:
            raise UpdateError('Invalid stable release source')
        _validate_feed(value, {'schema', 'channel', 'metadata_url', 'targets_url', 'targets'})
        return value
    if value.get('schema') != 2 or set(value) != {'schema', 'channel', 'sources'}:
        raise UpdateError('Invalid stable release source')
    sources = value['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        raise UpdateError('Invalid stable release source list')
    seen_urls, seen_kinds, seen_caches = set(), set(), set()
    for source in sources:
        _validate_feed(source, {'metadata_url', 'targets_url', 'targets', 'cache'})
        cache = _cache_name(source, legacy=False)
        if source['metadata_url'] in seen_urls or cache in seen_caches or seen_kinds & set(source['targets']):
            raise UpdateError('Release sources must be distinct and own distinct components')
        seen_urls.add(source['metadata_url'])
        seen_caches.add(cache)
        seen_kinds |= set(source['targets'])
    return value


def _cache_name(source, *, legacy):
    if source.get('cache'):
        return source['cache']
    if legacy:
        return LEGACY_CACHE
    return LEGACY_CACHE + '-' + hashlib.sha256(source['metadata_url'].encode()).hexdigest()[:8]


def normalize_sources(value):
    """A validated source value (either schema) -> list of feeds with an explicit TUF cache name each."""
    if value is None:
        return []
    value = validate_source(value)
    if value['schema'] == 1:
        return [{'metadata_url': value['metadata_url'], 'targets_url': value['targets_url'],
                 'targets': dict(value['targets']), 'cache': LEGACY_CACHE}]
    return [{'metadata_url': s['metadata_url'], 'targets_url': s['targets_url'], 'targets': dict(s['targets']),
             'cache': _cache_name(s, legacy=False)} for s in value['sources']]


def source_value(sources):
    """The smallest valid source file for these feeds (one default-cache feed keeps the original form)."""
    sources = [dict(s) for s in sources if s['targets']]
    if len(sources) == 1 and sources[0].get('cache', LEGACY_CACHE) == LEGACY_CACHE:
        s = sources[0]
        return validate_source({'schema': 1, 'channel': 'stable', 'metadata_url': s['metadata_url'],
                                'targets_url': s['targets_url'], 'targets': s['targets']})
    return validate_source({'schema': 2, 'channel': 'stable', 'sources': sources})


def merge_sources(existing, incoming):
    """Union by feed URL. The incoming (newer bootstrap's) feed definition wins and claims its components,
    so a component that moved to another feed (Router) is not looked up in the old one any more."""
    incoming = normalize_sources(incoming)
    claimed = {k for s in incoming for k in s['targets']}
    urls = {s['metadata_url'] for s in incoming}
    kept = []
    for source in normalize_sources(existing):
        if source['metadata_url'] in urls:
            continue
        source['targets'] = {k: v for k, v in source['targets'].items() if k not in claimed}
        kept.append(source)
    caches = {s['cache'] for s in kept}
    merged = list(kept)
    for source in incoming:
        if source['cache'] in caches:  # Same cache name for a different feed would mix TUF state.
            source['cache'] = _cache_name({'metadata_url': source['metadata_url']}, legacy=False)
        merged.append(source)
    return source_value(merged)


# Files in `<Orion>/bootstrap/` are shared by every component version on the PC: the retained bootstrap may be
# newer than the installed Hub (it updates itself first) or older (after a bootstrap rollback). A newer writer
# must never turn a shared file into a format an older reader refuses. So the feed list lives in two files:
#   release-source.json   schema 1, one feed - the only form Hub <= 0.1.11 and bootstraps <= 0.1.3 accept;
#   release-sources.json  every feed (schema 1 or 2) - read first by this code.
# Both are always written together (full list first). Readers merge whatever is valid in either, so a schema 2
# release-source.json written by bootstrap 0.1.4 still counts, and one broken file never hides the other.
SOURCE_FILE = 'release-source.json'
SOURCES_FILE = 'release-sources.json'


def legacy_source(value):
    """Schema 1 projection of a source value: the feed old code already used (TUF cache `release-cache`),
    else the Hub feed, else the first one."""
    feeds = normalize_sources(value)
    feed = (next((f for f in feeds if f['cache'] == LEGACY_CACHE), None)
            or next((f for f in feeds if 'hub' in f['targets']), None) or feeds[0])
    return validate_source({'schema': 1, 'channel': 'stable', 'metadata_url': feed['metadata_url'],
                            'targets_url': feed['targets_url'], 'targets': dict(feed['targets'])})


def read_sources(folder):
    """The installation's source value from `folder` (`<Orion>/bootstrap`), None when not configured.
    Raises UpdateError only when a file exists and none is usable."""
    folder = Path(folder)
    found, broken = [], False
    for name in (SOURCE_FILE, SOURCES_FILE):
        try:
            value = read_json(folder / name, None)
            if value is not None:
                found.append(validate_source(value))
        except (UpdateError, ValueError, OSError):
            broken = True
    if not found:
        if broken:
            raise UpdateError('Invalid stable release source')
        return None
    return found[0] if len(found) == 1 else merge_sources(found[0], found[1])


def write_sources(folder, value):
    """Store a source value in both files; returns True when anything changed on disk."""
    folder = Path(folder)
    value = validate_source(value)
    wanted = ((SOURCES_FILE, value), (SOURCE_FILE, legacy_source(value)))
    changed = False
    for name, content in wanted:  # Full list first: a crash in between leaves new readers complete.
        try:
            current = read_json(folder / name, None)
        except (ValueError, OSError):
            current = None
        if current != content:
            write_json(folder / name, content)
            changed = True
    return changed


def add_sources(folder, incoming):
    """Merge `incoming` feeds into the installation's source (incoming wins, see merge_sources)."""
    try:
        current = read_sources(folder)
    except UpdateError:
        current = None  # Unreadable files are replaced by the trusted bundle's feeds.
    return write_sources(folder, merge_sources(current, incoming) if current is not None else incoming)


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

    def replaced(self):
        """A newer bootstrap became active after this process started: its watcher owns the next round
        (every feed in one transaction), so this process starts no further scope."""
        if self.bootstrap_root is None:
            return False
        from . import bootstrap_update
        try:
            return bootstrap_update.handover_due(self.registry, self.bootstrap_root)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def new_client(self, source):
        # python-tuf's Updater refreshes once per instance; every check needs its own, and every feed
        # keeps its own TUF metadata/target cache (metadata versions are counted per feed).
        from .release_packages import ReleaseClient
        if self.client_factory:
            try:
                import inspect
                takes_source = bool(inspect.signature(self.client_factory).parameters)
            except (TypeError, ValueError):
                takes_source = False
            return self.client_factory(source) if takes_source else self.client_factory()
        return ReleaseClient(
            trusted_root=self.registry.path.parent / 'bootstrap/trusted-root.json',
            cache=self.registry.path.parent / source.get('cache', LEGACY_CACHE),
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
        return read_sources(self.registry.path.parent / 'bootstrap')

    def sources(self):
        """Every configured feed (Hub/Client feed, Router feed) as a normalized list."""
        return normalize_sources(self.source())

    def has_target(self, kind):
        return any(kind in feed['targets'] for feed in self.sources())

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

    @staticmethod
    def may_activate(registry, helper, tx):
        """A staged update may be applied at a safe launch: one the user downloaded ("İndir") unless deferred,
        one prepared in the background only while automatic updates are on and not deferred."""
        scope = 'router' if tx['changes'] == ['router'] else 'hub'
        automatic = AutomaticUpdates(registry, helper=helper, scope=scope)
        if tx.get('requested_by') == 'user':
            return automatic.policy()['deferred_until'] <= automatic.clock()
        return automatic.allowed(activate=True)

    def status(self):
        record = self.registry.read()
        try:
            tx = self.helper.status()
        except UpdateError:
            # Unreadable active journal: show "onarım gerekli" with the history instead of no card at all.
            tx = {'phase': 'repair_required', 'operation_id': None, 'changes': [], 'reason': 'repair_required'}
        try:
            state = read_json(self.state_path, {'schema': 1})
        except (ValueError, OSError):
            state = {'schema': 1}  # Rewritten by the next check; never a reason to hide the card.
        if not isinstance(state, dict):
            state = {'schema': 1}
        versions = {}
        for item in record['components']:
            versions[item['kind']] = {'package_id': item.get('package', {}).get('id'),
                                      'distribution': item['distribution']}
        try:
            # The retained updater itself (installs, starts and updates the components): shown for diagnosis.
            pointer = read_json(self.registry.path.parent / 'bootstrap/automatic-runtime.json', None)
            bundle = read_json(Path(pointer['root']) / 'bootstrap.json', None)
            versions['bootstrap'] = {'package_id': bundle['package_id'], 'distribution': 'retained'}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        history = []
        # Journal is the durable source, including operations finished while UI was closed.
        for path in sorted(self.base.glob('*/journal.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                value = read_json(path, {})
            except (ValueError, OSError):
                continue  # One damaged old journal never hides the others.
            if isinstance(value, dict) and value.get('installation_id') == record['installation_id']:
                history.append({k: value.get(k) for k in ('operation_id', 'phase', 'changes', 'updated_at')})
        # The updater replaces itself outside any journal (bootstrap/self-update.json, the latest renewal only):
        # without this the history never showed it, although "Kurulu sürümler" had the new version.
        try:
            from . import bootstrap_update
            renewal = read_json(bootstrap_update.state_path(self.registry), {})
            if isinstance(renewal, dict) and renewal.get('state') == 'confirmed' and isinstance(
                    renewal.get('confirmed_at'), (int, float)):
                history.append({'operation_id': None, 'phase': 'completed', 'changes': ['bootstrap'],
                                'updated_at': renewal['confirmed_at']})
        except (OSError, ValueError, TypeError):
            pass
        history.sort(key=lambda entry: entry.get('updated_at') or 0, reverse=True)
        pending = None
        if tx and tx['phase'] not in TERMINAL:
            pending = {'phase': tx['phase'], 'operation_id': tx['operation_id'],
                       'progress': tx.get('progress'), 'requested_by': tx.get('requested_by'),
                       'packages': {c['kind']: c.get('package', {}).get('id')
                                    for c in (tx.get('proposed') or {}).get('components', [])
                                    if c['kind'] in tx['changes']}}
        error = state.get('error') or (tx['phase'] if tx and tx['phase'] in {'failed', 'rolled_back', 'repair_required'} else None)
        if error is None and tx and tx['phase'] == 'failed':
            error = tx.get('reason')
        # A shared file this version does not understand (written by a newer or broken component) degrades
        # the card - "kaynak tanınmadı" - instead of failing the whole status: history and pending stay visible.
        try:
            configured, source_error = self.source() is not None, None
        except (UpdateError, ValueError, OSError):
            configured, source_error = True, 'SourceUnrecognized'
        try:
            policy = self.policy()
        except (UpdateError, ValueError, OSError):
            policy = {'schema': 1, 'paused': False, 'deferred_until': 0,
                      **({'enabled': False} if self.scope == 'router' else {})}
            source_error = source_error or 'PolicyUnrecognized'
        if error is None:
            error = source_error
        return {'schema': 1, 'automatic_updates': policy.get('enabled', record['preferences']['automatic_updates']),
                'configured': configured, 'source_error': source_error, 'channel': 'stable', 'versions': versions,
                'policy': policy, 'last_attempt': state.get('last_attempt'),
                'last_success': state.get('last_success'), 'bootstrap': self._bootstrap_summary(state), 'error': error,
                'error_message': update_log.describe(state.get('error') or (tx.get('reason') if tx and tx['phase'] in {'failed', 'rolled_back', 'repair_required'} else None) or error),
                'checking': self._checking(state), 'check_outcome': state.get('outcome'), 'feed_errors': state.get('feed_errors'),
                'check_outcome_at': state.get('outcome_at'),
                'pending': pending, 'history': history, 'observed_at': self.clock(),
                'newer': self._newer(state, record, pending),
                'activation': 'next_safe_launch', 'router_independent': True}

    def _newer(self, state, record, pending):
        """Newer signed releases the last check found that are not installed yet (an applied update or a
        renewed updater drops out). `staged` = already downloaded in the waiting update; `bytes` = what
        "İndir" still has to download; `download` = there is something left to download."""
        found = state.get('newer')
        if not isinstance(found, dict) or not isinstance(found.get('components'), dict):
            return None
        installed = {}
        for item in record['components']:
            try:
                installed[item['kind']] = read_json(Path(item['code_path']) / 'orion-release.json', {}).get('sequence', 0)
            except (OSError, ValueError, AttributeError):
                installed[item['kind']] = 0
        try:
            from . import bootstrap_update
            root = bootstrap_update.pointer_root(self.registry)
            if root is not None:
                installed['bootstrap'] = bootstrap_update.bundle_sequence(root)
        except (OSError, ValueError):
            pass
        pending = pending or {}
        waiting = (pending.get('packages') or {}) if pending.get('phase') in WAITING else {}
        components = {}
        for kind, offer in found['components'].items():
            if not isinstance(offer, dict) or type(offer.get('sequence')) is not int:
                continue
            if kind not in installed or offer['sequence'] <= installed[kind]:
                continue
            components[kind] = {**{k: offer.get(k) for k in ('package_id', 'version', 'sequence', 'bytes')},
                                'staged': waiting.get(kind) == offer.get('package_id')}
        if not components:
            return None
        left = [c for c in components.values() if not c['staged']]
        return {'components': components, 'checked_at': found.get('checked_at'), 'download': bool(left),
                'bytes': sum(c['bytes'] for c in left if isinstance(c['bytes'], int))}

    def running(self):
        """A check of this scope is running now; never raises (the status card only shows it)."""
        try:
            state = read_json(self.state_path, {})
            return isinstance(state, dict) and self._checking(state)
        except (OSError, ValueError):
            return False

    def _checking(self, state):
        """A check process of this installation is running right now (its pid is recorded while it works)."""
        pid = state.get('running_pid')
        if type(pid) is not int or self.clock() - state.get('last_attempt', 0) > 30 * 60:
            return False
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return False

    def _bootstrap_summary(self, state):
        from . import bootstrap_update
        try:
            return {**bootstrap_update.summary(self.registry), 'error': state.get('bootstrap_error')}
        except (OSError, ValueError):
            return None

    @property
    def state_path(self):
        return self.base / ('router-automatic.json' if self.scope == 'router' else 'automatic.json')

    def _kinds(self, record, with_router=False):
        """Components this scheduler may stage. One transaction covers every component with a newer signed
        sequence (Hub, Client and the local Router together), so the user gets a single restart."""
        if self.scope == 'router':
            return {'router'}
        kinds = {'hub', 'client'}
        if with_router and any(c['kind'] == 'router' for c in record['components']):
            kinds.add('router')
        elif any(c['kind'] == 'hub' for c in record['components']):
            try:
                if AutomaticUpdates(self.registry, helper=self.helper, scope='router').policy()['enabled']:
                    kinds.add('router')
            except UpdateError:
                pass  # A broken Router policy file must not hold back Hub/Client.
        return kinds

    def _sync_bundled_source(self):
        """The running bootstrap's feeds (e.g. the Router feed) are merged into this installation's source."""
        if self.bootstrap_root is None:
            return
        from . import bootstrap_update
        try:
            if bootstrap_update.sync_source(self.registry, self.bootstrap_root):
                update_log.log(self.base, 'source.synced')
        except LifecycleError:
            # Another operation (often the check that just staged an update) holds the lifecycle lock: the feeds
            # are merged at the next check. Normal, so not an error entry.
            update_log.log(self.base, 'source.sync-busy')
        except Exception as error:  # A bad bundled file must never stop update checks.
            update_log.log_exception(self.base, 'source.sync', error)

    def _skip(self, outcome, explicit):
        """An explicit refresh that did nothing must say why (no silent no-op)."""
        if explicit:
            try:
                state = read_json(self.state_path, {'schema': 1})
            except ValueError:
                state = {'schema': 1}
            state.update(outcome=outcome, outcome_at=self.clock(), error=None)
            write_json(self.state_path, state)
        key = (str(self.base), self.scope)
        if explicit or _logged_skips.get(key) != outcome:  # Unattended: once per change, not every poll.
            _logged_skips[key] = outcome
            update_log.log(self.base, 'check.skipped', scope=self.scope, outcome=outcome)

    def check(self, *, manual=False, now=False, with_router=False, download=True, consent=False):
        """`now` skips only the 6-hour spacing; policy still applies. `manual` is the user's own action and
        ignores the automatic-update preference. `download=False` (the card's refresh button) only checks:
        it records which components have a newer signed release (`newer`: version and bytes to download,
        the retained updater included) and downloads, stages and adopts nothing. `consent` (the card's "İndir")
        marks the staged update as wanted by the user: it is applied at the next safe start even with automatic
        updates off. Any other explicit check (the Router panel) still needs the preference for that."""
        explicit = manual or now
        # Separate OS lock prevents two schedulers sharing TUF metadata/cache.
        with operation_lock(self.base / 'automatic.json', timeout=0):
            self._sync_bundled_source()
            sources = self.sources()
            if not sources:
                return self._skip('no_source', explicit)
            if not manual and not self.allowed():
                enabled = (self.policy()['enabled'] if self.scope == 'router'
                           else self.registry.read()['preferences']['automatic_updates'])
                return self._skip('paused' if enabled else 'disabled', explicit)
            try:
                state = read_json(self.state_path, {'schema': 1})
            except ValueError:
                state = {'schema': 1}  # An unreadable state file must never stop update checks.
            # The background schedule counts only checks that may download: a refresh that just looked
            # never postpones the next automatic download round.
            if not manual and not now and self.clock() - state.get('last_round', state.get('last_attempt', 0)) < INTERVAL:
                return
            pending = self.helper.status()
            if pending and pending['phase'] not in TERMINAL and pending['phase'] not in WAITING:
                return self._skip('pending', explicit)  # Downloading or being applied: never race it.
            # Keep the schedule an older check started (it only knew `last_attempt`) before a refresh moves it.
            state['last_round'] = self.clock() if download else state.get('last_round', state.get('last_attempt', 0))
            state.update(last_attempt=self.clock(), error=None, bootstrap_error=None, outcome=None,
                         running_pid=os.getpid())
            write_json(self.state_path, state)
            _logged_skips.pop((str(self.base), self.scope), None)
            update_log.log(self.base, 'check.start', scope=self.scope, manual=manual, now=now, download=download)
            try:
                if download and self.scope == 'hub' and self._adopt_bootstrap(sources, state):
                    # The new bootstrap's watcher takes over and checks components at once.
                    state['last_attempt'] = state['last_round'] = 0
                    state['outcome'] = 'bootstrap_updated'
                    return
                record = self.registry.read()
                kinds = self._kinds(record, with_router)
                installed = {c['kind'] for c in record['components']}
                clients, found, failures, polled, names = {}, {}, [], 0, []
                checked = None
                for feed in sources:
                    selected = {k: v for k, v in feed['targets'].items() if k in kinds and k in installed}
                    if not selected:
                        continue
                    polled += 1
                    try:
                        client, result, targets = self._poll_feed(feed, selected)
                    except Exception as error:
                        # One feed that cannot even start (bad cache, bad URL) must not block the others.
                        failures.append(type(error).__name__)
                        continue
                    if targets is None:
                        failures.append(result.get('reason', 'CheckUnavailable'))
                        continue
                    checked = checked or result
                    for kind, name in selected.items():
                        target = targets[name]
                        item = next(c for c in record['components'] if c['kind'] == kind)
                        current = read_json(Path(item['code_path']) / 'orion-release.json', {})
                        if target.custom['component'] != kind:
                            raise UpdateError('Stable target component mismatch')
                        clients[name], found[name] = client, target
                        if target.custom['sequence'] > current['sequence']:
                            names.append(name)
                if not polled:
                    state['outcome'] = 'up_to_date'
                    return
                if failures and checked is None:
                    raise UpdateError(failures[0])  # Every feed failed: that is a failed check.
                if failures:
                    state['feed_errors'] = failures  # Reachable feeds still update; the failure stays visible.
                else:
                    state.pop('feed_errors', None)
                targets = found
                newer = {targets[n].custom['component']: self._offer(targets[n], clients[n]) for n in names}
                if not download and self.scope == 'hub':
                    newer.update(self._bootstrap_offer(sources, state))
                state['newer'] = {'components': newer, 'checked_at': self.clock()}
                state['last_success'] = self.clock()
                state['outcome'] = 'update_found' if newer else 'up_to_date'
                write_json(self.state_path, state)
                if self.bootstrap_root is not None:
                    from . import bootstrap_update
                    bootstrap_update.confirm(self.registry, self.bootstrap_root)
                if not download:
                    if names:
                        update_log.log(self.base, 'check.update-found', scope=self.scope, count=len(names), download=False)
                    return  # Only checked: the user downloads with "İndir".
                if names and pending and pending['phase'] in WAITING:
                    if not self._supersedes(pending, {targets[n].custom['component']: targets[n] for n in names}):
                        state['outcome'] = 'pending'  # What is waiting is already the newest: keep it.
                        return
                    # A newer release than the one waiting to be applied: drop the waiting one (nothing was
                    # switched; its download is discarded) and prepare the whole newer set in its place.
                    self.helper.supersede(pending['operation_id'])
                    update_log.log(self.base, 'check.superseded', scope=self.scope)
                if names:
                    update_log.log(self.base, 'check.update-found', scope=self.scope, count=len(names))
                    # Recheck under the helper's operation lock, including user disabling it.
                    class Selection:
                        def check(self, requested):
                            return checked, {name: targets[name] for name in requested}

                        def prepare(self, target, destination, progress=None):
                            return clients[target.path].prepare(target, destination, progress=progress)

                        def download_length(self, target):
                            owner = clients[target.path]
                            return owner.download_length(target) if hasattr(owner, 'download_length') else target.length
                    # A download the user asked for is applied at the next safe start even with automatic updates off.
                    self.helper.prepare(Selection(), names, automatic=not manual, automatic_scope=self.scope,
                                        **({'requested_by': 'user'} if manual and consent else {}))
                    update_log.log(self.base, 'check.staged', scope=self.scope)
            except Exception as error:
                # Never persist URLs, subprocess output, credentials or server error bodies.
                state['error'] = type(error).__name__
                state['outcome'] = None
                update_log.log_exception(self.base, 'check', error)
            finally:
                state.pop('running_pid', None)
                state['outcome_at'] = self.clock()
                write_json(self.state_path, state)

    @staticmethod
    def _offer(target, client):
        """What the card lists for one newer component: version and the bytes a download still needs."""
        size = getattr(target, 'length', None)
        try:
            if hasattr(client, 'download_length'):
                size = client.download_length(target)
        except Exception:
            pass  # Only the size shown on the card: the signed length is a fine estimate.
        return {'package_id': target.custom.get('package_id'), 'version': target.custom.get('version'),
                'sequence': target.custom['sequence'], 'bytes': size if type(size) is int else None}

    def _bootstrap_offer(self, sources, state):
        """A newer signed retained updater, found without downloading it (adopted only by a download)."""
        from . import bootstrap_update
        try:
            found = bootstrap_update.newer(self, sources)
        except Exception as error:
            state['bootstrap_error'] = getattr(error, 'code', type(error).__name__)
            return {}
        if found is None:
            return {}
        _, target, _, _ = found
        return {'bootstrap': {'package_id': target.custom['package_id'], 'version': target.custom.get('version'),
                              'sequence': target.custom['sequence'], 'bytes': target.length}}

    @staticmethod
    def _supersedes(pending, newest):
        """True when `newest` (kind -> signed target newer than installed) differs from what is waiting and covers
        every component it would update (a Router-only check never drops a waiting Hub+Router set)."""
        if not set(pending['changes']) <= set(newest):
            return False
        waiting = {c['kind']: c['package'] for c in pending['proposed']['components'] if c['kind'] in pending['changes']}
        return set(newest) != set(waiting) or any(
            target.custom['package_id'] != waiting[kind]['id'] or target.hashes.get('sha256') != waiting[kind]['sha256']
            for kind, target in newest.items())

    def covers_router(self):
        """This Hub-scope scheduler stages the local Router together with Hub and Client: the watcher's separate
        Router check would only find a Router-only half of a release first."""
        if self.scope != 'hub':
            return False
        record = self.registry.read()
        return 'router' in self._kinds(record) and any(c['kind'] == 'router' for c in record['components']) \
            and self.allowed()

    def _poll_feed(self, feed, selected):
        """One signed check of one feed. A PackageError is retried once: the retained bootstrap replacing
        itself (trust root, cache) at that very moment makes the first read fail, the second one succeeds."""
        for attempt in (1, 2):
            try:
                client = self.new_client(feed)
                result, targets = client.check(list(selected.values()))
                if targets is None and attempt == 1 and result.get('reason') == 'PackageError':
                    raise PackageError('transient')
                return client, result, targets
            except PackageError as error:
                if attempt == 2:
                    raise
                update_log.log(self.base, 'check.retry', reason=type(error).__name__)
                time.sleep(RETRY_PAUSE)

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
            return self._apply_at_launch(tx)
        if tx and tx['phase'] in APPLYING:
            return self._recover_at_launch(tx)
        # Critical recovery remains explicitly accessible even when preference is off.
        return tx

    def _recover_at_launch(self, tx):
        """An apply that was cut off (power loss, Task Manager, a closed window) and that no process is
        working on: finish it (switch/health again) or roll it back, before Orion starts. Never depends on
        the automatic-update preference - the installation is mid-switch and cannot start as it is."""
        from . import update_ui
        from .update_gate import interrupted
        if not interrupted(self.registry.path):
            return tx
        with update_ui.updating_window(self.registry):
            update_log.log(self.base, 'startup.recover', operation=tx['operation_id'], phase=tx['phase'])
            recorded = []
            try:
                result = self.helper.resume(automatic=True, before_admission=self._record_completed(recorded))
            except LifecycleError:
                return tx  # Someone else started working on it after all.
        phase = (result or {}).get('phase')
        if phase in {'completed', 'rolled_back', 'failed', 'repair_required'}:
            reason = (result or {}).get('reason') if phase != 'completed' else None
            if not (recorded and phase == 'completed'):
                update_ui.write_result(self.registry, result, phase, reason or (phase if phase != 'completed' else None))
            update_log.log(self.base, 'startup.recovered', outcome=phase, reason=reason)
        return result

    def _apply_at_launch(self, tx):
        """Apply the staged update at a safe launch. The user sees an "Orion is updating" window, and the
        outcome is recorded for the app window that the launcher opens right after."""
        if not AutomaticUpdates.may_activate(self.registry, self.helper, tx):
            return tx
        from . import update_ui
        base = self.base
        # Decide cheaply and first: the window, the 300 MB re-hash and the stop attempt only happen when the
        # update can really be applied now. A running Hub, a tree that is still exiting or another update
        # operation means "not now": launch normally, the staged update stays for the next opportunity or
        # for "Şimdi güncelle".
        if not self.helper.idle_now(tx):
            update_log.log(base, 'startup.apply-skipped', operation=tx['operation_id'],
                           busy=getattr(self.helper.host, 'refused', None) or 'running')
            return tx
        with update_ui.updating_window(self.registry):
            update_log.log(base, 'startup.apply', operation=tx['operation_id'])
            recorded = []
            try:
                # A healthy new version keeps running (admitted in place); the launcher then only attaches to it.
                result = self.helper.resume(automatic=True, before_admission=self._record_completed(recorded))
            except LifecycleError:
                result = None  # Another operation won the lock between the probe and now.
            if result is None:
                return tx
        phase = (result or {}).get('phase')
        if phase in {'completed', 'rolled_back', 'failed', 'repair_required'}:
            reason = (result or {}).get('reason') if phase != 'completed' else None
            if not (recorded and phase == 'completed'):
                update_ui.write_result(self.registry, result or tx, phase, reason or (phase if phase != 'completed' else None))
            update_log.log(base, 'startup.applied', outcome=phase, reason=reason)
        return result

    def _record_completed(self, recorded):
        """The helper's last step before admission opens: the result the app window shows is on disk first."""
        from . import update_ui
        def record(tx):
            recorded.append(update_ui.write_result(self.registry, tx, 'completed', None))
        return record


def _usable_root(value):
    root = canonical_path(value, must_exist=True)
    ensure_private_directory(root)
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
    # Nothing staged, downloading or mid-apply: `startup` would only find no transaction. Skipping it saves a
    # whole retained-runtime Python start on every launch.
    if not (registry.path.parent / 'updates/active.json').exists():
        return
    command = runtime_command(registry)
    if command:
        result = subprocess.run(command + ['startup'], capture_output=True, timeout=600,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RegistryError('Update launch boundary failed; inspect local update status/repair')


def spawn_watcher(registry, *, hosted_only=False):
    """`hosted_only`: start it only as a child of the resident Orion.exe (from inside a Hub-owned Job, where a
    watcher must never run: its own "update now" stop would end it). Raises OSError when that is not possible."""
    command = runtime_command(registry)
    if command:
        # The separately retained worker survives Hub/browser closure, never activates. It runs in its own
        # bundle folder: the launcher's working directory is the Hub tree, and a watcher whose cwd is inside it
        # counted as a process using the installation ("busy=python.exe:<watcher>"), so a staged update was
        # skipped at the next launch.
        # Under the installation's resident Orion.exe when it runs: shown with it, ended with it.
        from .orion_host import popen
        popen(command + ['watch'], registry=registry, require_host=hosted_only, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, cwd=str(Path(command[2]).parents[2]),
              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def record_check_failure(registry, returncode, code=None):
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
            state['error'] = code or ('BootstrapIncompatible' if returncode == 2 else 'CheckProcessFailed')
            state['outcome'] = None
            state.pop('running_pid', None)
            write_json(automatic.state_path, state)
    except (LifecycleError, OSError):
        pass  # A running check owns the state file and records its own outcome.


WATCHER_LOCK_WAIT = 3


def watch(automatic):
    # Launched by the outer launcher, never by a Hub-owned Job descendant.
    # A user may close Hub while a download is in flight; preparation finishes
    # here and never turns into activation. The next poll exits when stopped.
    base = automatic.base
    # A short wait, not none: "is a watcher running?" probes (the Hub, a handover) take this lock for a moment,
    # and a new watcher that met one of them gave up and left none running.
    with operation_lock(automatic.base / 'watcher.json', timeout=WATCHER_LOCK_WAIT):
        update_log.log(base, 'watcher.start')
        if automatic.bootstrap_root is not None:
            from . import bootstrap_update
            bootstrap_update.note_started(automatic.registry, automatic.bootstrap_root)
            automatic._sync_bundled_source()
        while automatic.source() is not None:
            if automatic.bootstrap_root is not None and bootstrap_update.handover_due(
                    automatic.registry, automatic.bootstrap_root):
                update_log.log(base, 'watcher.handover')
                return 'handover'  # Lock is released on return; the caller starts the new watcher.
            if _exit_requested(automatic):
                return
            try:
                # Never waits: the old check gave exiting processes 20 s to settle, so a "Çık" was noticed late.
                host = automatic.helper.host
                with getattr(host, 'stopped_now', host.stopped)(automatic.registry.read()) as stopped:
                    if stopped:
                        update_log.log(base, 'watcher.stop', reason='installation stopped')
                        return
            except Exception as error:
                # A process scan that cannot decide (access denied, a lock held for a moment) must not kill the watcher.
                update_log.log_exception(base, 'watch.stopped-check', error)
            if _exit_requested(automatic):
                return
            from . import update_now
            # One failing step (a busy lock, a bad state file, a failed check) must never end the watcher:
            # a manual check from the Hub holds the check lock for minutes, and the old loop died on it.
            applied = False
            try:
                # Explicit "update now" runs here, outside the Hub-owned Job that its own stop ends.
                applied = (update_now.process_request(automatic) or {}).get('phase') == 'completed'
            except Exception as error:
                update_log.log_exception(base, 'watch.apply-now', error)
            try:
                # Right after an applied update, look again at once (a newer release may have come out meanwhile).
                automatic.check(now=applied)
            except Exception as error:
                if type(error) is not LifecycleError:  # Busy: another check is running; that is normal.
                    update_log.log_exception(base, 'watch.check', error)
            if automatic.replaced():
                continue  # This check adopted a newer bootstrap: hand over now, no Router-only round.
            try:
                router = AutomaticUpdates(automatic.registry, helper=automatic.helper, scope='router')
                request = canonical_path(automatic.base / 'router-request.json')
                if request.exists():
                    value = read_json(request, {})
                    if value == {'schema': 1, 'installation_id': automatic.registry.read()['installation_id'], 'action': 'prepare'}:
                        request.unlink()
                        if any(c['kind'] == 'hub' for c in automatic.registry.read()['components']):
                            # Explicit check from the Router panel: Hub, Client and Router in one transaction.
                            automatic.check(manual=True, with_router=True)
                        else:
                            router.check(manual=True)
                    else:
                        request.unlink()
                        raise UpdateError('Invalid Router update request')
                if not automatic.covers_router():
                    router.check()  # Router-only install (or Hub updates off): the Router checks on its own.
            except Exception as error:
                if type(error) is not LifecycleError:
                    update_log.log_exception(base, 'watch.router', error)
            for _ in range(WATCH_PAUSE * 4):  # The pause between rounds ends as soon as a quit asks for it.
                if (automatic.base / 'watcher.exit').exists():
                    break
                time.sleep(.25)
            if automatic.bootstrap_root is not None:
                try:
                    # A retired bootstrap that the previous watcher was still running from when it was confirmed.
                    bootstrap_update.prune(automatic.registry, automatic.bootstrap_root)
                except Exception as error:
                    update_log.log_exception(base, 'watch.prune', error)


WATCH_PAUSE = 5  # seconds between watcher rounds


def _exit_requested(automatic):
    marker = automatic.base / 'watcher.exit'
    if marker.exists():  # Written by an explicit stop ("Çık" / -Stop), which waits for this process to end.
        marker.unlink(missing_ok=True)
        update_log.log(automatic.base, 'watcher.stop', reason='exit requested')
        return True
    return False
