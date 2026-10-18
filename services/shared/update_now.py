"""Explicit "update now" for an already staged update.

Two owners, one small state file (`updates/apply-now.json`):

* the Hub waits (never interrupting) until chat, voice, tool, transfer and remote-client work
  is idle, closes admission for new work, and hands the request over (`requested`);
* the retained bootstrap's watcher, which lives outside the Hub-owned Job, stops the services,
  runs the normal helper transaction (backup, switch, health, automatic rollback), restarts
  everything and records the outcome. The Hub is down during that time.

HTTP never supplies a command, path or package: only the fixed request below is honoured and it
only applies to the transaction that is staged at that moment.
"""
import time

from .automatic_updates import read_json, write_json
from .installation_registry import operation_lock
from .process_lifecycle import LifecycleError
from .update_helper import UpdateError
from . import update_log

STAGED = {'staged', 'waiting_for_idle'}
HUB_PHASES = {'waiting_for_idle', 'requested'}
WATCHER_PHASES = {'stopping', 'applying', 'restarting'}
ACTIVE = HUB_PHASES | WATCHER_PHASES
FINAL = {'completed', 'failed', 'cancelled'}
HEARTBEAT_TIMEOUT = 30      # the Hub task that waits for idle writes at least this often
HANDOFF_TIMEOUT = 90        # the watcher must claim a handed-over request within this time
STALL_TIMEOUT = 30 * 60     # the watcher must finish (or fail) within this time
LOCK_WAIT = 300             # how long the watcher waits for another operation before it gives up (nothing stopped yet)
ACTIVITY_TIMEOUT = 10 * 60  # a watcher-owned phase without any journal/state movement for this long is abandoned


def _path(registry):
    return registry.path.parent / 'updates/apply-now.json'


def _lock(registry):
    return operation_lock(registry.path.parent / 'updates/apply-now.lock', timeout=10)


def read(registry):
    value = read_json(_path(registry), None)
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('schema') != 1
            or value.get('phase') not in ACTIVE | FINAL):
        raise UpdateError('Invalid update-now state')
    return value


def _save(registry, state, clock, **fields):
    state = {**state, **fields, 'updated_at': clock()}
    write_json(_path(registry), state)
    return state


def update(registry, *, only=None, clock=time.time, **fields):
    """Atomic read-modify-write; `only` restricts it to states in that phase set."""
    with _lock(registry):
        state = read(registry)
        if state is None or (only is not None and state['phase'] not in only):
            return None
        return _save(registry, state, clock, **fields)


def begin(registry, helper, *, clock=time.time):
    """Start waiting for idle. Only a currently staged transaction may be applied."""
    with _lock(registry):
        tx = helper.status()
        if tx is None or tx['phase'] not in STAGED or not tx.get('proposed'):
            raise UpdateError('NothingStaged')
        state = read(registry)
        if state is not None and state['phase'] in ACTIVE:
            raise UpdateError('AlreadyRequested')
        return _save(registry, {'schema': 1}, clock, installation_id=tx['installation_id'],
                     operation_id=tx['operation_id'], phase='waiting_for_idle', requested_at=clock(),
                     error=None, busy={})


def cancel(registry, *, clock=time.time):
    """Cancelling is possible until the watcher has claimed the request."""
    with _lock(registry):
        state = read(registry)
        if state is None or state['phase'] not in HUB_PHASES:
            raise UpdateError('NotCancellable')
        return _save(registry, state, clock, phase='cancelled', error=None)


def handoff(registry, *, clock=time.time):
    """Hub: idle confirmed and admission closed -> the watcher may take over."""
    state = update(registry, only={'waiting_for_idle'}, clock=clock, phase='requested', handoff_at=clock())
    return state is not None


def abandon(registry, *, clock=time.time):
    """Cancel a request that only a now-dead Hub task could have honoured (Hub restart, "Çık").

    waiting_for_idle needs the Hub's idle watcher and its admission gate; `requested` was verified idle
    by a Hub that no longer runs. Neither may be acted on by a later session."""
    try:
        with _lock(registry):
            state = read(registry)
            if state is None or state['phase'] not in HUB_PHASES:
                return None
            return _save(registry, state, clock, phase='cancelled', error=None)
    except (OSError, ValueError, UpdateError):
        return None


def claim(registry, *, clock=time.time):
    """Watcher: take a handed-over request exactly once."""
    return update(registry, only={'requested'}, clock=clock, phase='stopping', claimed_at=clock())


def reconcile(registry, *, clock=time.time):
    """Turn requests that nobody is working on any more into an explicit failure.

    Only called where the Hub is serving, so a watcher-owned phase that stopped
    moving while the Hub is up again was abandoned."""
    state = read(registry)
    if state is None:
        return None
    now = clock()
    reason = None
    if state['phase'] == 'waiting_for_idle' and now - state['updated_at'] > HEARTBEAT_TIMEOUT:
        reason = 'RequestLost'
    elif state['phase'] == 'requested' and now - state.get('handoff_at', 0) > HANDOFF_TIMEOUT:
        reason = 'WatcherDidNotRespond'
    elif state['phase'] in WATCHER_PHASES and now - state['updated_at'] > STALL_TIMEOUT:
        reason = 'Stalled'
    if reason is None:
        return state
    return update(registry, only={state['phase']}, clock=clock, phase='failed', error=reason) or read(registry)


def view(registry, *, clock=time.time):
    """Fixed, path-free summary for the API."""
    state = reconcile(registry, clock=clock)
    if state is None:
        return None
    result = {key: state.get(key) for key in ('phase', 'error', 'busy', 'requested_at', 'updated_at', 'outcome')}
    result['message'] = update_log.describe(state.get('error'))
    return result


def watcher_alive(registry):
    """The retained watcher holds this lock for as long as it runs (outside the Hub's Job)."""
    try:
        with operation_lock(registry.path.parent / 'updates/watcher.json', timeout=0):
            return False
    except LifecycleError:
        return True


def _journal_time(registry):
    import json
    base = registry.path.parent / 'updates'
    try:
        identity = json.loads((base / 'active.json').read_text(encoding='utf-8'))['operation_id']
        return json.loads((base / identity / 'journal.json').read_text(encoding='utf-8')).get('updated_at', 0)
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def in_progress(registry, *, clock=time.time):
    """True while the retained watcher is stopping/applying/restarting for an "update now" request.

    Every other launcher (a second start, the tray, Windows startup) waits for this instead of racing the
    apply for the operation lock or restarting services between the stop and the switch. A request whose
    watcher is gone or that has not moved for a long time is not progress (the Hub reports it as failed)."""
    try:
        state = read(registry)
    except (OSError, ValueError, UpdateError):
        return False
    if state is None or state['phase'] not in WATCHER_PHASES:
        return False
    if clock() - max(state.get('updated_at', 0), _journal_time(registry)) > ACTIVITY_TIMEOUT:
        return False
    return watcher_alive(registry)


def default_stop(registry):
    from .launcher import launch
    # The updating window already replaced the app window; the caller holds the operation lock.
    launch(registry, stop=True, keep_window=True, apply_owner=True)


def default_start(registry):
    from .launcher import launch
    # Opens the app window again (it shows the update result); `apply_owner` = do not wait for ourselves.
    launch(registry, apply_owner=True)


def process_request(automatic, *, stop=default_stop, start=default_start, clock=time.time,
                    window=None, lock_wait=LOCK_WAIT):
    """Watcher side. Returns the final state, or None when nothing was requested.

    One owner for the whole sequence: the operation lock is taken BEFORE anything is stopped and kept
    until the helper transaction ended, so no other launcher can grab it between "stopped" and "switch"
    (that race made the helper fail with "Lifecycle busy" after the Hub was already gone). Services
    are restarted only after the lock is released and after the outcome was recorded; every other
    launcher waits meanwhile (`in_progress`)."""
    from . import update_ui
    registry, helper = automatic.registry, automatic.helper
    base = registry.path.parent / 'updates'
    window = window or update_ui.updating_window
    # A hand-over the watcher did not pick up in time is stale (the Hub that verified idleness may be gone):
    # it becomes an explicit failure here instead of stopping a freshly started Hub.
    state = reconcile(registry, clock=clock)
    if (state is None or state['phase'] != 'requested'
            or state.get('installation_id') != registry.read()['installation_id']):
        return None
    state = claim(registry, clock=clock)
    if state is None:
        return None  # Cancelled in the meantime.
    update_log.log(base, 'apply-now.claimed', operation=state['operation_id'])
    outcome = error = tx = None
    with window(registry):
        try:
            tx = helper.status()
            if (tx is None or tx['operation_id'] != state['operation_id'] or tx['phase'] not in STAGED):
                raise UpdateError('StagedUpdateChanged')
            try:
                lock = operation_lock(registry.path, timeout=lock_wait)
                lock.__enter__()
            except LifecycleError:
                raise UpdateError('OperationBusy')
            try:
                tx = helper.status()
                if tx is not None and tx['operation_id'] == state['operation_id'] and tx['phase'] == 'completed':
                    outcome = 'completed'  # Another launcher applied exactly this update meanwhile.
                elif tx is None or tx['operation_id'] != state['operation_id'] or tx['phase'] not in STAGED:
                    raise UpdateError('StagedUpdateChanged')
                else:
                    update_log.log(base, 'apply-now.stopping')
                    stop(registry)
                    update(registry, clock=clock, phase='applying')
                    update_log.log(base, 'apply-now.resume')
                    # Explicit user action: the automatic-update preference/deferral does not apply.
                    result = helper.resume(automatic=False)
                    outcome = result['phase'] if result else None
                    tx = helper.status() or tx
                    if outcome != 'completed':
                        error = (result or {}).get('reason') or outcome or 'UpdateNotApplied'
                        if outcome == 'waiting_for_idle':
                            error = 'ServicesStillRunning'
            finally:
                lock.__exit__(None, None, None)
        except Exception as exception:
            # UpdateError texts here are fixed codes; anything else may carry paths: class only.
            error = str(exception) if type(exception) is UpdateError else type(exception).__name__
            update_log.log_exception(base, 'apply-now', exception)
        update(registry, clock=clock, phase='restarting', outcome=outcome)
        update_ui.write_result(registry, tx, outcome, error, clock=clock)
        try:
            if outcome != 'repair_required':
                start(registry)  # Also after a rollback or failure: never leave the PC without Orion.
        except Exception as exception:
            error = error or type(exception).__name__
            update_log.log_exception(base, 'apply-now.start', exception)
            update_ui.write_result(registry, tx, outcome, error, clock=clock)
    update_log.log(base, 'apply-now.finished', outcome=outcome, error=error)
    return update(registry, clock=clock, phase='failed' if error else 'completed', error=error,
                  outcome=outcome, finished_at=clock())
