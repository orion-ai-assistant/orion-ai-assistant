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
from .update_helper import UpdateError

STAGED = {'staged', 'waiting_for_idle'}
HUB_PHASES = {'waiting_for_idle', 'requested'}
WATCHER_PHASES = {'stopping', 'applying', 'restarting'}
ACTIVE = HUB_PHASES | WATCHER_PHASES
FINAL = {'completed', 'failed', 'cancelled'}
HEARTBEAT_TIMEOUT = 30      # the Hub task that waits for idle writes at least this often
HANDOFF_TIMEOUT = 90        # the watcher must claim a handed-over request within this time
STALL_TIMEOUT = 30 * 60     # the watcher must finish (or fail) within this time


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
    return {key: state.get(key) for key in ('phase', 'error', 'busy', 'requested_at', 'updated_at', 'outcome')}


def default_stop(registry):
    from .launcher import launch
    launch(registry, stop=True, keep_window=True)  # The app window stays; the UI reloads afterwards.


def default_start(registry):
    from .launcher import launch
    launch(registry, background=True)


def process_request(automatic, *, stop=default_stop, start=default_start, clock=time.time):
    """Watcher side. Returns the final state, or None when nothing was requested."""
    registry, helper = automatic.registry, automatic.helper
    state = read(registry)
    if (state is None or state['phase'] != 'requested'
            or state.get('installation_id') != registry.read()['installation_id']):
        return None
    state = claim(registry, clock=clock)
    if state is None:
        return None  # Cancelled in the meantime.
    outcome = error = None
    try:
        tx = helper.status()
        if (tx is None or tx['operation_id'] != state['operation_id'] or tx['phase'] not in STAGED):
            raise UpdateError('StagedUpdateChanged')
        stop(registry)
        update(registry, clock=clock, phase='applying')
        # Explicit user action: the automatic-update preference/deferral does not apply.
        result = helper.resume(automatic=False)
        outcome = result['phase'] if result else None
        if outcome != 'completed':
            error = (result or {}).get('reason') or outcome or 'UpdateNotApplied'
            if outcome == 'waiting_for_idle':
                error = 'ServicesStillRunning'
    except Exception as exception:
        # UpdateError texts here are fixed codes; anything else may carry paths: class only.
        error = str(exception) if type(exception) is UpdateError else type(exception).__name__
    update(registry, clock=clock, phase='restarting', outcome=outcome)
    try:
        if outcome != 'repair_required':
            start(registry)  # Also after a rollback or failure: never leave the PC without Orion.
    except Exception as exception:
        error = error or type(exception).__name__
    return update(registry, clock=clock, phase='failed' if error else 'completed', error=error,
                  outcome=outcome, finished_at=clock())
