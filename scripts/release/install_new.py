"""Explicit new package installation; refuses existing registries or data.

No update, migration, Startup changes or automatic checks. A trusted installer
ships the TUF root out of band: never download a root from a catalog URL.
"""
import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.installation_registry import Registry, component_record, new_registry, operation_lock
from services.shared.installation_paths import validate_destination, canonical_path, validate_private_directory
from services.shared.release_packages import ReleaseClient, compatible, record_prepared_package


def install(client, names, root, registry, *, settings=None):
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
            raise ValueError('Existing installation requires a separate Stage 06/08 transaction')
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
        components = []
        for release in releases:
            directory = root / {'hub': 'Hub', 'router': 'Router', 'client': 'Client'}[release['component']]
            validate_destination(directory, private=True)
        for target in targets.values():
            release = target.custom
            directory = root / {'hub': 'Hub', 'router': 'Router', 'client': 'Client'}[release['component']]
            tree = client.prepare(target, directory / 'releases' / release['package_id'])
            data, state = directory / 'data', directory / 'state'
            data.mkdir()
            state.mkdir()
            if release['component'] == 'hub':
                (data / 'services').mkdir()
                # Generated secrets remain in data; static package defaults are
                # not copied from any development or existing user installation.
                import secrets
                (data / 'services/.env.global.local').write_text(
                    'ORION_INSTALL_MODE=local\nORION_LOCAL_SHORTCUTS=0\n'
                    'JWT_SECRET_KEY=' + secrets.token_urlsafe(48) + '\n', encoding='utf-8')
                with (data / 'services/.env.global.local').open('a', encoding='utf-8') as stream:
                    stream.write(''.join(f'{k}={v}\n' for k, v in settings.get('hub', {}).items()))
            elif release['component'] == 'router':
                # Preserve defaults as data on first install, never rewrite them
                # on package preparation or any later component change.
                values = settings.get('router', {})
                (data / '.env').write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
            components.append(component_record(release['component'], tree, distribution='packaged',
                data_path=data, state_path=state, ownership='owned', management='local',
                package=record_prepared_package(tree, state, target)))
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
        for item in components:
            if item['kind'] == 'router':
                tree = Path(item['code_path'])
                env = dict(os.environ, ORION_INSTALLATION_REGISTRY=str(registry.path), ORION_SKIP_GLOBAL_REGISTRATION='1')
                result = subprocess.run([str(tree / 'runtime/python/python.exe'), '-c',
                    'import json; from core import config; from core.mdns import router_id; '
                    'from core.tls_identity import load_identity; '
                    'i=load_identity(config.TLS_DIRECTORY,router_id(config.MDNS_ID_FILE)); '
                    'print(json.dumps(dict(router_uuid=i.id,tls_spki_sha256=i.fp,identity_path=str(config.MDNS_ID_FILE),'
                    'certificate_path=str(i.certificate_path),tls_port=config.TLS_PORT)))'],
                    cwd=tree, env=env, check=True, capture_output=True, timeout=30)
                identity = json.loads(result.stdout.decode('utf-8').splitlines()[-1])
                def save_identity(current):
                    target = next(c for c in current['components'] if c['component_id'] == item['component_id'])
                    target.update(identity)
                    return current
                registry.update(save_identity)
            if item['kind'] == 'hub':
                tree = Path(item['code_path'])
                env = dict(os.environ, ORION_INSTALLATION_REGISTRY=str(registry.path), ORION_SKIP_GLOBAL_REGISTRATION='1')
                subprocess.run([str(tree / 'runtime/python/python.exe'), str(tree / 'local_db_setup.py')],
                               cwd=tree, env=env, check=True, timeout=180)
                subprocess.run([str(tree / 'runtime/python/python.exe'), '-c',
                    'from services.shared.launcher import install_entry; '
                    'from services.shared.installation_registry import Registry; install_entry(Registry(),desktop=False)'],
                    cwd=tree, env=env, check=True, timeout=30)
        if not any(c['kind'] == 'hub' for c in components):
            from services.shared.launcher import install_entry
            install_entry(registry, desktop=False)
        return registry.read()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-root', required=True, type=Path)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--metadata-url', required=True)
    parser.add_argument('--targets-url', required=True)
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
    parser.add_argument('targets', nargs='+')
    args = parser.parse_args()
    client = ReleaseClient(trusted_root=args.trusted_root, cache=args.cache,
        metadata_url=args.metadata_url, targets_url=args.targets_url)
    result = install(client, args.targets, args.root, args.registry,
                     settings=json.loads(args.settings.read_text()) if args.settings else None)
    from services.shared.launcher import install_entry, preferences, launch
    registry = Registry(args.registry)
    install_entry(registry, desktop=True)
    preferences(registry, start_with_windows=not args.no_start_with_windows, show_client=not args.no_window)
    updates = 'off'
    if not args.no_auto_update:
        from services.shared.automatic_updates import AutomaticUpdates
        automatic = AutomaticUpdates(registry)
        if automatic.source() is not None:  # Only an installer that shipped a release source can update.
            automatic.preferences(enabled=True)
            if any(c['kind'] == 'router' for c in registry.read()['components']):
                AutomaticUpdates(registry, scope='router').preferences(enabled=True)
            updates = 'on'
    firewall = 'skipped'
    if not args.no_firewall and os.environ.get('ORION_SKIP_FIREWALL') != '1':
        from services.shared.windows_firewall import ensure_rules
        print('Windows will ask for administrator permission once, to allow Orion on your private network '
              '(phone and other devices). Declining is fine: Orion still works on this computer.', flush=True)
        firewall = ensure_rules(registry.read())
    if args.launch:
        launch(registry, background=args.background)
    print(json.dumps({'status': 'installed', 'installation_id': result['installation_id'],
                      'automatic_updates': updates, 'firewall': firewall}))
