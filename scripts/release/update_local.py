"""Explicit local update helper. Use a separately retained trusted bootstrap.

No arbitrary command, script or package directory inputs are supported. Repository
URLs supply signed targets only; the root is fixed beside the installed launcher.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.installation_registry import Registry
from services.shared.update_helper import UpdateHelper
from services.shared.release_packages import ReleaseClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('action', choices=['prepare', 'resume', 'status', 'rollback'])
    parser.add_argument('--metadata-url')
    parser.add_argument('--targets-url')
    parser.add_argument('targets', nargs='*')
    args = parser.parse_intermixed_args(argv)
    registry = Registry(args.registry)
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry.path)
    helper = UpdateHelper(registry)
    if args.action == 'prepare':
        if not args.metadata_url or not args.targets_url or not args.targets:
            parser.error('prepare requires HTTPS repository URLs and signed target names')
        client = ReleaseClient(trusted_root=registry.path.parent / 'bootstrap/trusted-root.json',
            cache=registry.path.parent / 'release-cache', metadata_url=args.metadata_url, targets_url=args.targets_url)
        result = helper.prepare(client, args.targets)
    else:
        result = {'resume': helper.resume, 'status': helper.status, 'rollback': helper.rollback}[args.action]()
    # Keep paths, receipts and backup file inventories local, not in CLI output.
    print(json.dumps(None if result is None else {k: result.get(k) for k in
        ('schema', 'operation_id', 'phase', 'attempts', 'verification', 'health', 'reason')}))
    return 1 if result and result['phase'] in {'repair_required', 'failed', 'rolled_back'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
