"""Fixed retained-bootstrap entry. Never accepts a command, path or feed from HTTP."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.automatic_updates import AutomaticUpdates, watch
from services.shared.installation_registry import Registry
from services.shared.update_gate import blocked
from services.shared.process_lifecycle import LifecycleError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('action', choices=['check', 'startup', 'watch'])
    args = parser.parse_args()
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(args.registry)
    automatic = AutomaticUpdates(Registry(args.registry))
    if args.action == 'watch':
        try:
            watch(automatic)
        except LifecycleError:
            pass  # An existing per-installation watcher already owns polling.
    elif args.action == 'check':
        automatic.check()
    else:
        try:
            automatic.startup()
        except LifecycleError:
            # Another helper/launcher won the lock. Running preparation is safe
            # to keep using; critical activation remains closed by admission.
            pass
        if blocked(args.registry):
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
