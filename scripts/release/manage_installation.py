"""Explicit local conversion/removal planning and owner-reviewed actions."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.installation_registry import Registry
from services.shared.legacy_conversion import preflight, convert, revision
from services.shared.package_maintenance import removal_plan, uninstall, cleanup_plan, purge_old_code, restore_registration
from services.shared.release_packages import ReleaseClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('action', choices=['plan-conversion', 'convert', 'plan-uninstall', 'uninstall', 'plan-cleanup', 'purge-code', 'restore-registration'])
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--expected-revision')
    parser.add_argument('--components', nargs='+', choices=['hub', 'router', 'client'], default=['hub', 'router', 'client'])
    parser.add_argument('--package-id')
    parser.add_argument('--operation-id')
    parser.add_argument('--metadata-url')
    parser.add_argument('--targets-url')
    parser.add_argument('--targets', nargs='+')
    args = parser.parse_args()
    if args.action in {'plan-conversion', 'convert'} and args.destination is None:
        parser.error('Conversion requires --destination')
    if args.action in {'convert', 'uninstall', 'purge-code', 'restore-registration'} and not args.expected_revision:
        parser.error('Apply requires a reviewed --expected-revision from the corresponding plan')
    if args.action == 'convert' and not (args.metadata_url and args.targets_url and args.targets):
        parser.error('Conversion requires signed repository URLs and target names')
    if args.action == 'purge-code' and not args.package_id:
        parser.error('Code cleanup requires --package-id')
    if args.action == 'restore-registration' and not args.operation_id:
        parser.error('Registration restore requires --operation-id')
    registry = Registry(args.registry)
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry.path)
    if args.action == 'plan-conversion':
        result, _ = preflight(registry, args.destination)
    elif args.action == 'convert':
        client = ReleaseClient(trusted_root=registry.path.parent / 'bootstrap/trusted-root.json',
                               cache=registry.path.parent / 'release-cache', metadata_url=args.metadata_url, targets_url=args.targets_url)
        tx = convert(registry, client, args.targets, args.destination, expected_revision=args.expected_revision)
        result = {k: tx.get(k) for k in ('operation_id', 'phase', 'reason')}
    elif args.action == 'plan-uninstall':
        result = removal_plan(registry, args.components)
    elif args.action == 'uninstall':
        result = uninstall(registry, args.components, expected_revision=args.expected_revision)
    elif args.action == 'plan-cleanup':
        result = cleanup_plan(registry)
    elif args.action == 'purge-code':
        result = purge_old_code(registry, args.package_id, expected_revision=args.expected_revision)
    else:
        result = restore_registration(registry, args.operation_id, expected_revision=args.expected_revision)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
