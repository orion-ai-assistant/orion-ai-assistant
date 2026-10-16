"""Explicit installation inventory actions; never starts a selected directory.

Run ``python -m services.shared.installation_control --help`` from the Hub tree.
Registration records filesystem provenance only, leaving connection trust and
process-management authorization to their existing independent controls.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .installation_registry import Registry, RegistryError, component_record, new_registry, import_legacy_installations


def register_router(registry, root, *, distribution='legacy-source'):
    """Explicit folder selection adds provenance; replacement is a separate flow."""
    from .router_installation import inspect_router_folder, select_router_candidate
    inspected = select_router_candidate([inspect_router_folder(root, distribution=distribution)],
                                        distribution='development' if distribution == 'development' else 'installed')
    path = Path(inspected.root)
    port = 9443
    for name in ('.env', '.env.local'):
        environment = path / name
        if environment.exists():
            for line in environment.read_text(encoding='utf-8').splitlines():
                key, separator, value = line.strip().partition('=')
                if separator and key == 'ORION_ROUTER_TLS_PORT':
                    port = int(value.strip().strip('\"\''))
    component = component_record(
        'router', path, distribution=distribution, component_id=inspected.identity,
        data_path=inspected.data_path, state_path=inspected.state_path,
        router_uuid=inspected.identity, tls_spki_sha256=inspected.spki,
        tls_port=port,
        identity_path=str(path / 'persistent/mdns-id'),
        certificate_path=str(path / 'persistent/tls/certificate.pem'))

    def merge(existing):
        if existing is None:
            existing = new_registry(registry.path.parent)
        same_mode = [item for item in existing['components'] if item['kind'] == 'router'
                     and (item['distribution'] == 'development') == (distribution == 'development')]
        if same_mode:
            if len(same_mode) != 1 or any(same_mode[0].get(key) != component[key]
                                        for key in ('code_path', 'router_uuid', 'tls_spki_sha256')):
                raise RegistryError('Another Router or identity is already selected; explicit replacement is required')
            return existing
        existing['components'].append(component)
        return existing

    return registry.update(merge)


def import_legacy(registry, *, hub_manifest=None, router_manifest=None):
    """Import selected public manifests once, retaining existing preferences/IDs."""
    imported = import_legacy_installations(hub_manifest, router_manifest, registry.path.parent)
    if not imported['components']:
        raise RegistryError('Select at least one legacy manifest')

    def merge(existing):
        if existing is None:
            return imported
        for component in imported['components']:
            same = [item for item in existing['components'] if item['kind'] == component['kind']
                    and item['distribution'] != 'development']
            if same:
                if len(same) != 1 or same[0] != component:
                    raise RegistryError('An installation is already selected; explicit replacement is required')
            else:
                existing['components'].append(component)
        return existing

    return registry.update(merge)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, help='Separate inventory file, e.g. for an isolated test installation')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Read the registry; missing/corrupt records are distinct')
    legacy = commands.add_parser('import-legacy', help='Record explicit existing installations in place')
    legacy.add_argument('--hub-manifest', type=Path)
    legacy.add_argument('--router-manifest', type=Path)
    inspect = commands.add_parser('inspect-router', help='Inspect a chosen folder without executing its contents')
    inspect.add_argument('root', type=Path)
    inspect.add_argument('--distribution', choices=['legacy-source', 'development', 'packaged'], default='legacy-source')
    select = commands.add_parser('register-router', help='Explicitly register an inspected source folder in place')
    select.add_argument('root', type=Path)
    select.add_argument('--distribution', choices=['legacy-source', 'development'], default='legacy-source')
    args = parser.parse_args(argv)
    registry = Registry(args.registry)
    try:
        if args.command == 'status':
            record = registry.read()
            result = {'status': 'registered' if record is not None else 'unregistered', 'record': record}
        elif args.command == 'import-legacy':
            result = import_legacy(registry, hub_manifest=args.hub_manifest, router_manifest=args.router_manifest)
        elif args.command == 'register-router':
            result = register_router(registry, args.root, distribution=args.distribution)
        else:
            from .router_installation import inspect_router_folder
            result = asdict(inspect_router_folder(args.root, distribution=args.distribution))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (RegistryError, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'repair-required', 'reason': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
