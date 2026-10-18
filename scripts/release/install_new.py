"""Explicit package installation: a new installation, or components added to an existing one.

First installation refuses existing registries or data. When an installation already exists the same
command takes the *add* path (`services/shared/component_setup.py`): only components that are not
registered yet are installed (Hub+Client next to a Router-only install, or a Router next to a Hub),
the registered ones stay untouched. No update, migration or Startup preference changes. A trusted
installer ships the TUF root out of band: never download a root from a catalog URL.

Feeds: `--release-source` points at the bundle's release-source.json (one or several feeds: Hub/Client
feed and Router feed). Each feed gets its own ReleaseClient and TUF cache; components are chosen by
`--components`. The legacy `--metadata-url/--targets-url` + target names form still installs from one feed.
"""
import argparse
import json
import os
import platform
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.installation_registry import Registry, new_registry, operation_lock
from services.shared.installation_paths import validate_destination, canonical_path, validate_private_directory
from services.shared.release_packages import FeedSet, ReleaseClient, compatible
from services.shared import component_setup
from services.shared.component_setup import AddError, KIND_DIRS, add_components, materialize


def feed_client(source, trusted_root, cache_root, kinds, factory=ReleaseClient):
    """One ReleaseClient per feed (own TUF cache each) behind a single check/prepare interface.

    Returns (client, target names) for the requested component kinds; a kind that no configured feed
    offers is a clear error instead of a silent partial installation."""
    from services.shared.automatic_updates import normalize_sources
    routes, names, found = [], [], set()
    for feed in normalize_sources(source):
        wanted = [k for k in kinds if k in feed['targets']]
        if not wanted:
            continue
        routes.append((factory(trusted_root=trusted_root, cache=Path(cache_root) / feed['cache'],
                               metadata_url=feed['metadata_url'], targets_url=feed['targets_url']),
                       [feed['targets'][k] for k in wanted]))
        names += [feed['targets'][k] for k in wanted]
        found |= set(wanted)
    if set(kinds) - found:
        raise AddError('Bu kurucunun kaynak listesinde şu bileşen yok: ' + ', '.join(sorted(set(kinds) - found)))
    return FeedSet(routes), names


def install(client, names, root, registry, *, settings=None, release_source=None):
    if sys.platform != 'win32' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise ValueError('Only native Windows x64 package installations are supported')
    windows = sys.getwindowsversion()
    if windows.major < 10 or windows.build < 18362:
        raise ValueError('Prepared PostgreSQL runtime requires Windows 10 1903 or later')
    root = canonical_path(root)
    registry = Registry(canonical_path(registry))
    validate_private_directory(registry.path.parent)
    with operation_lock(registry.path, timeout=180):
        if registry.read() is not None or registry.backup_path.exists():
            raise ValueError('Existing installation: use the add path (install_new without a fresh registry), '
                             'or a separate Stage 06/08 transaction for updates')
        status, targets = client.check(names)
        if targets is None:
            raise ValueError('Release check unavailable: ' + status['reason'])
        releases = [t.custom for t in targets.values()]
        compatible(releases)
        settings = settings or {}
        for values in settings.values():
            for key, value in values.items():
                if not key.replace('_', '').isalnum() or any(c in str(value) for c in '\r\n\x00'):
                    raise ValueError('Invalid installation setting')
        if len(releases) != len({r['component'] for r in releases}):
            raise ValueError('Choose one package per component')
        if 'client' in {r['component'] for r in releases} and 'hub' not in {r['component'] for r in releases}:
            raise ValueError('A new Client installation requires a Hub; later independent updates are supported by metadata')
        for release in releases:
            component_setup.check_destination(root / KIND_DIRS[release['component']])
        components = [materialize(client, target, root, settings) for target in targets.values()]
        record = new_registry(root, components, preferences={'automatic_updates': False, 'start_with_windows': False})
        record['locations'] = {key: str(canonical_path(root / name)) for key, name in
            {'models': 'Models', 'downloads': 'Downloads', 'runtime': 'Runtime', 'backups': 'backups'}.items()}
        for path in record['locations'].values():
            Path(path).mkdir(parents=True, exist_ok=True)
        registry.write(record)
        trust = registry.path.parent / 'bootstrap'
        trust.mkdir(exist_ok=True)
        (trust / 'trusted-root.json').write_bytes(client.bootstrap_root)
        # Keep the independently delivered bootstrap in place. Never copy over a
        # running/sole recovery runtime or infer one from the candidate Hub.
        if (ROOT / 'bootstrap.json').is_file():
            from services.shared.automatic_updates import validate_source, write_json
            validate_private_directory(ROOT)
            write_json(trust / 'automatic-runtime.json', {'schema': 1, 'root': str(ROOT)})
            if (ROOT / 'release-source.json').is_file():
                source = validate_source(json.loads((ROOT / 'release-source.json').read_text(encoding='utf-8')))
                write_json(trust / 'release-source.json', source)
        # Registry must exist before setup, so every executable resolves exactly
        # the new data directory. Failures retain it for explicit repair, never wipe.
        component_setup.finish_setup(registry, components)
        if not any(c['kind'] == 'hub' for c in components):
            from services.shared.launcher import install_entry
            install_entry(registry, desktop=False)
        return registry.read()


def finish(registry, args, *, added=False):
    """Everything after the registry is final: links, preferences, updates, firewall, optional start."""
    from services.shared.launcher import install_entry, preferences, launch, register_uninstall_entry
    record = registry.read()
    install_entry(registry, desktop=True)
    register_uninstall_entry(registry)  # Settings > Apps > Orion AI Assistant > Uninstall
    if added:
        # Hub joined a Router-only install (or a Router a Hub one): the links change identity, the user's
        # Startup / window choices stay exactly as they were.
        if record['preferences']['start_with_windows']:
            preferences(registry, start_with_windows=True, show_client=record['preferences']['show_client'])
    else:
        preferences(registry, start_with_windows=not args.no_start_with_windows, show_client=not args.no_window)
    updates = 'off'
    if not args.no_auto_update:
        from services.shared.automatic_updates import AutomaticUpdates
        automatic = AutomaticUpdates(registry)
        if automatic.source() is not None:  # Only an installer that shipped a release source can update.
            record = registry.read()
            if not added:
                automatic.preferences(enabled=True)
            if any(c['kind'] == 'router' for c in record['components']) and (
                    not added or record['preferences']['automatic_updates']):
                router = AutomaticUpdates(registry, scope='router')
                if router.policy().get('enabled') is not True:  # An added Router follows the installation's choice.
                    router.preferences(enabled=True)
            updates = 'on' if record['preferences']['automatic_updates'] or not added else 'off'
    firewall = 'skipped'
    if not args.no_firewall and os.environ.get('ORION_SKIP_FIREWALL') != '1':
        from services.shared.windows_firewall import ensure_rules
        print('Windows will ask for administrator permission once, to allow Orion on your private network '
              '(phone and other devices). Declining is fine: Orion still works on this computer.', flush=True)
        firewall = ensure_rules(registry.read())
    if args.launch:
        launch(registry, background=args.background)
    return updates, firewall


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-root', required=True, type=Path)
    parser.add_argument('--cache', type=Path, help='TUF cache (single-feed form); default: <registry folder>/release-cache')
    parser.add_argument('--metadata-url')
    parser.add_argument('--targets-url')
    parser.add_argument('--release-source', type=Path, help="The bundle's release-source.json (one or several feeds)")
    parser.add_argument('--components', nargs='+', choices=sorted(KIND_DIRS), help='With --release-source: kinds to install')
    parser.add_argument('--add-missing', action='store_true',
                        help='Existing installation: add only the listed components that are not registered yet')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--settings', type=Path, help='Optional private JSON component -> initial settings')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--background', action='store_true')
    # Defaults: updates and Windows start are ON (the owner's choice); each can be declined.
    parser.add_argument('--no-start-with-windows', action='store_true')
    parser.add_argument('--no-auto-update', action='store_true')
    parser.add_argument('--no-firewall', action='store_true',
                        help='Do not ask for the one-time private-network firewall permission')
    parser.add_argument('--no-window', action='store_true')
    parser.add_argument('targets', nargs='*')
    args = parser.parse_args(argv)
    registry = Registry(args.registry)
    source = None
    if args.release_source:
        from services.shared.automatic_updates import validate_source
        source = validate_source(json.loads(args.release_source.read_text(encoding='utf-8-sig')))
        if not args.components:
            parser.error('--release-source requires --components')
        cache_root = args.cache or registry.path.parent
        client, names = feed_client(source, args.trusted_root, cache_root, args.components)
    else:
        if not (args.metadata_url and args.targets_url and args.targets):
            parser.error('Provide --release-source with --components, or --metadata-url, --targets-url and targets')
        client = ReleaseClient(trusted_root=args.trusted_root, cache=args.cache or registry.path.parent / 'release-cache',
                               metadata_url=args.metadata_url, targets_url=args.targets_url)
        names = args.targets
    settings = json.loads(args.settings.read_text()) if args.settings else None
    try:
        if registry.path.exists() or registry.backup_path.exists():
            record = add_components(client, names, args.root, registry.path, settings=settings,
                                    skip_existing=args.add_missing, release_source=source)
            added = True
        else:
            record = install(client, names, args.root, args.registry, settings=settings)
            added = False
    except AddError as error:
        print('Orion: ' + str(error), flush=True)
        print(json.dumps({'status': 'refused'}))
        return 3
    registry = Registry(args.registry)
    updates, firewall = finish(registry, args, added=added)
    print(json.dumps({'status': 'added' if added else 'installed', 'installation_id': record['installation_id'],
                      'automatic_updates': updates, 'firewall': firewall,
                      'components': sorted(c['kind'] for c in registry.read()['components'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
