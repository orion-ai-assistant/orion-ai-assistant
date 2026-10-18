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
from services.shared import update_log


def main():
    # No console exists for these processes: whatever goes wrong must land in updates/watcher.log.
    registry = None
    try:
        registry = _registry_argument()
        return run()
    except SystemExit:
        raise
    except BaseException as error:
        if registry is not None:
            update_log.log_exception(registry.parent / 'updates', 'update_automatic', error)
        raise


def _registry_argument():
    for index, value in enumerate(sys.argv):
        if value == '--registry' and index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1])
    return None


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('action', choices=['check', 'startup', 'watch'])
    parser.add_argument('--now', action='store_true', help='check: skip the 6-hour spacing')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--only-check', action='store_true',
                        help='check: find newer signed releases, download nothing (refresh button)')
    choice.add_argument('--download', action='store_true',
                        help='check: download and stage every newer signed release (download button)')
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
    elif args.action == 'check' and (args.only_check or args.download):
        return _user_check(automatic, download=args.download)
    elif args.action == 'check':
        automatic.check(now=args.now)
        if automatic.replaced():
            # The Hub check adopted a newer bootstrap. Its watcher checks every feed at once and stages ONE
            # transaction; a Router check from here would stage a Router-only update and split the round.
            if args.now and _outdated_and_failed(automatic):
                return _recheck_with_new_bootstrap(automatic)
            update_log.log(automatic.base, 'check.handed-over')
            return 0
        AutomaticUpdates(Registry(args.registry), helper=automatic.helper, scope='router').check(now=args.now)
        if args.now and _outdated_and_failed(automatic):
            return _recheck_with_new_bootstrap(automatic)
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


def _user_check(automatic, *, download):
    """The settings card's buttons: one round for Hub, Client and the local Router (one transaction). The
    user's own action, so the automatic-update preference does not apply; signatures, locks and the
    no-interruption rules do."""
    try:
        automatic.check(manual=True, with_router=True, download=download, consent=download)
    except LifecycleError:
        return 0  # Another check or download is running; it records its own outcome for the card.
    if download and automatic.replaced():
        # The download renewed the retained updater first; the new one downloads the components.
        return _recheck_with_new_bootstrap(automatic, ['check', '--download'])
    if not download and _outdated_and_failed(automatic):
        return _recheck_with_new_bootstrap(automatic, ['check', '--only-check'])
    return 0


def _outdated_and_failed(automatic):
    """This check ran from a bootstrap that was replaced meanwhile and ended in an error."""
    try:
        from services.shared.automatic_updates import read_json
        state = read_json(automatic.state_path, {})
    except (OSError, ValueError):
        return False
    return bool(state.get('error')) and bootstrap_update.handover_due(automatic.registry, automatic.bootstrap_root)


def _recheck_with_new_bootstrap(automatic, arguments=('check', '--now')):
    """One more check through the now-active bootstrap instead of leaving the user with an error."""
    import subprocess
    from services.shared.automatic_updates import runtime_command
    command = runtime_command(automatic.registry)
    if command is None:
        return 1
    update_log.log(automatic.base, 'check.retry', reason='bootstrap-replaced')
    return subprocess.run(command + list(arguments), stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode


if __name__ == '__main__':
    raise SystemExit(main())
