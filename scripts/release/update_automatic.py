"""Fixed retained-bootstrap entry. Never accepts a command, path or feed from HTTP."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.automatic_updates import AutomaticUpdates, spawn_watcher, watch
from services.shared import bootstrap_update
from services.shared.installation_registry import Registry
from services.shared.update_gate import blocked
from services.shared.process_lifecycle import LifecycleError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('action', choices=['check', 'startup', 'watch'])
    parser.add_argument('--now', action='store_true', help='check: skip the 6-hour spacing (refresh button)')
    args = parser.parse_args()
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(args.registry)
    # This bundle's own root: lets the watcher notice that a newer bootstrap became active.
    automatic = AutomaticUpdates(Registry(args.registry), bootstrap_root=Path(__file__).resolve().parents[2])
    if args.action == 'watch':
        while True:
            try:
                outcome = watch(automatic)
            except LifecycleError:
                break  # An existing per-installation watcher already owns polling.
            if outcome != 'handover':
                break
            # A newer bootstrap is active. The lock is free now: start its watcher and
            # keep this one until it proves it started, otherwise go back to polling here.
            if bootstrap_update.supervise_handover(automatic.registry, lambda: spawn_watcher(automatic.registry)):
                break
    elif args.action == 'check':
        automatic.check(now=args.now)
        AutomaticUpdates(Registry(args.registry), helper=automatic.helper, scope='router').check(now=args.now)
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
