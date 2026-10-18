"""Authenticated status; local desktop authority for preference changes only."""
import asyncio
from contextlib import suppress
import subprocess
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool
from typing import Literal

from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import is_desktop_peer, local_flutter_origin
from services.shared.installation_registry import Registry
from services.shared import update_now, update_ui
from services.shared.automatic_updates import AutomaticUpdates, record_check_failure, runtime_command
from services.shared.process_lifecycle import LifecycleError
from services.shared.installation_registry import operation_lock
from services.shared.update_helper import UpdateError, UpdateHelper

router = APIRouter(prefix='/api/v1/updates')


def local_control(request):
    # No JWT role, paired phone token, proxy header or payload can grant this.
    if not is_desktop_peer(request.scope) or getattr(request.app.state, 'mobile', False):
        return False
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme}://{request.url.netloc}'
    flutter = bool(origin and local_flutter_origin(origin))
    return not ((origin and origin != expected and not flutter)
                or (request.headers.get('sec-fetch-site') == 'cross-site' and not flutter))


def router_managed(registry, record=None):
    """True when this installation owns a local Router that the update feed also covers."""
    record = record or registry.read()
    try:
        has_router_feed = AutomaticUpdates(registry).has_target('router')
    except UpdateError:
        return False  # Unrecognized source: the card says so (source_error); never a failed status.
    return bool(has_router_feed
                and any(c['kind'] == 'router' and c['ownership'] == 'owned'
                        and c['management'] == 'local' for c in record['components']))


# When the refresh button last started a check process in this Hub. The check writes its own pid only after
# it started, so the card would otherwise flicker from "pressed" to "idle" before "checking" appears.
_requested = {'at': 0.0}
_REQUEST_GRACE = 20
# After the retained updater renewed itself its new watcher checks every feed within seconds. Until it
# reports, the card says "denetleniyor"; past this window the card says the check has not started.
_RENEWAL_GRACE = 90


def read_status():
    registry = Registry()
    if registry.read() is None:
        return {'available': False, 'configured': False, 'automatic_updates': False,
                'message': 'Kayıtlı paket kurulumu yok.'}
    data = AutomaticUpdates(registry).status()
    data['available'] = runtime_command(registry) is not None
    record = registry.read()
    data['launcher_preferences'] = {k: record['preferences'][k] for k in ('start_with_windows', 'show_client')}
    try:
        data['router_automatic_updates'] = AutomaticUpdates(registry, scope='router').policy()['enabled']
    except UpdateError:
        data['router_automatic_updates'] = False
    data['router_configured'] = router_managed(registry, record)
    data['apply_now'] = update_now.view(registry)
    data['last_result'] = update_ui.read_result(registry)
    attempt = max(data.get('last_attempt') or 0, data.get('check_outcome_at') or 0)
    if (not data.get('checking') and _requested['at'] and time.time() - _requested['at'] < _REQUEST_GRACE
            and attempt < _requested['at']):
        data['checking'] = True  # Started but not yet running: still "denetleniyor".
    if not data.get('checking'):
        # A Router check runs in its own scope; it is still a check of this PC (and may take minutes).
        data['checking'] = AutomaticUpdates(registry, scope='router').running() is True
    if (not data.get('checking') and not data.get('pending') and data.get('check_outcome') == 'bootstrap_updated'
            and time.time() - (data.get('check_outcome_at') or 0) < _RENEWAL_GRACE):
        data['checking'] = True  # The renewed updater's watcher is about to check: not a finished outcome.
    data['activity'] = activity(data)
    return data


PREPARING = {'checking', 'downloading', 'verifying'}


def activity(data):
    """What the PC is doing about updates, from its own files whichever process started the check:
    `downloading` (a release was found and is being fetched), `ready` (staged, "Şimdi güncelle"),
    `checking`, or `idle`. The journal phases checking/downloading/verifying only exist once a newer
    signed release was found, so they are never "just checking"."""
    pending = data.get('pending') or {}
    phase = pending.get('phase')
    if phase in PREPARING:
        return 'downloading'
    if phase in {'staged', 'waiting_for_idle'}:
        return 'ready'
    if data.get('checking'):
        return 'found' if data.get('check_outcome') == 'update_found' else 'checking'
    return 'idle'


_last = {'data': None}


def read_status_or_last():
    """Never an HTTP error for the card: while an update operation (a launch-time apply, a check) holds
    files or locks for a while, answer with the last known state flagged `busy`. The real exception, with
    its location, goes to the update log (never to the response)."""
    try:
        data = read_status()
        _last['data'] = data
        return data
    except Exception as error:
        try:
            from services.shared import update_log
            update_log.log_exception(Registry().path.parent / 'updates', 'status.read', error)
        except Exception:
            pass  # Diagnostics only.
        if isinstance(error, LifecycleError):  # A lock held by a running update operation: wait for it.
            return {**(_last['data'] or {}), 'busy': True, 'busy_message': 'Güncelleme işlemi sürüyor.'}
        # Anything else is not "busy" (that would spin forever): show the last state with the reason.
        from services.shared import update_log
        code = type(error).__name__
        return {**(_last['data'] or {'available': False, 'configured': False, 'automatic_updates': False}),
                'error': code, 'error_message': update_log.describe(code)}


@router.get('')
async def status(request: Request, user: str = Depends(get_current_user)):
    result = await asyncio.to_thread(read_status_or_last)
    return {**result, 'can_manage': local_control(request)}


class Preferences(BaseModel):
    automatic_updates: StrictBool | None = None
    paused: StrictBool | None = None
    defer_hours: Literal[0, 24] | None = None
    router_automatic_updates: StrictBool | None = None
    start_with_windows: StrictBool | None = None
    show_client: StrictBool | None = None
    check_now: StrictBool | None = None
    model_config = {'extra': 'forbid'}


def check_now(registry):
    """Ask the retained update runtime for an immediate check; never takes commands from HTTP."""
    command = runtime_command(registry)
    if command is None:
        raise ValueError('Retained update runtime unavailable')
    _requested['at'] = time.time()
    # A concurrent check holds the per-installation lock and the extra process exits at once.
    try:
        process = subprocess.Popen(command + ['check', '--now'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   close_fds=True)
    except OSError:
        _requested['at'] = 0.0
        record_check_failure(registry, 1, 'CheckProcessNotStarted')
        return
    # A bootstrap too old for these arguments exits at once (argparse code 2). Make that
    # visible in automatic.json for the card instead of silently doing nothing.
    threading.Thread(target=_report_failure, args=(process, registry), daemon=True).start()


def _report_failure(process, registry):
    try:
        code = process.wait()
        _requested['at'] = 0.0
        if isinstance(code, int) and code:
            record_check_failure(registry, code)
    except Exception:
        pass  # Diagnostics only; the user can still read the status and retry.


def change_preferences(payload):
    registry = Registry()
    if payload.check_now is not None:
        if payload.check_now is not True or any(v is not None for k, v in payload.model_dump().items() if k != 'check_now'):
            raise ValueError('check_now must be the only field and true')
        check_now(registry)
        return read_status()
    groups = [any(value is not None for value in (payload.automatic_updates, payload.paused, payload.defer_hours)),
              payload.router_automatic_updates is not None,
              any(value is not None for value in (payload.start_with_windows, payload.show_client))]
    if sum(groups) > 1:
        raise ValueError('Save each preference group separately')
    if payload.automatic_updates is True and runtime_command(registry) is None:
        raise ValueError('Retained update runtime unavailable')
    if groups[1]:
        if payload.router_automatic_updates and (runtime_command(registry) is None or not read_status()['router_configured']):
            raise ValueError('Independent Router update source/runtime unavailable')
        AutomaticUpdates(registry, scope='router').preferences(enabled=payload.router_automatic_updates)
    elif groups[2]:
        from services.shared.launcher import preferences
        previous = registry.read()['preferences']
        preferences(registry, start_with_windows=payload.start_with_windows if payload.start_with_windows is not None else previous['start_with_windows'],
                    show_client=payload.show_client if payload.show_client is not None else previous['show_client'])
    else:
        AutomaticUpdates(registry).preferences(enabled=payload.automatic_updates, paused=payload.paused, defer_hours=payload.defer_hours)
        if payload.automatic_updates is not None and router_managed(registry):
            # One switch for the whole product: Hub, web client and its local Router.
            AutomaticUpdates(registry, scope='router').preferences(enabled=payload.automatic_updates)
    return read_status()


@router.put('')
async def preferences(payload: Preferences, request: Request, user: str = Depends(get_current_user)):
    if not local_control(request):
        raise HTTPException(403, 'PC güncelleme tercihi yalnızca bu bilgisayardan değiştirilebilir.')
    try:
        return await asyncio.to_thread(change_preferences, payload)
    except (OSError, ValueError, RuntimeError) as error:
        if payload.check_now:
            raise HTTPException(409, 'Güncelleme denetimi başlatılamadı: güncelleme yardımcısı bu kurulumda hazır değil. Orion’u yeniden başlatıp tekrar deneyin.') from error
        raise HTTPException(409, 'Tercih kaydedilemedi. Güncelleme kaynağını ve bekleyen işlemi kontrol edip yeniden deneyin.') from error


watcher_alive = update_now.watcher_alive


def begin_apply(registry):
    if runtime_command(registry) is None or not watcher_alive(registry):
        raise UpdateError('WatcherUnavailable')
    return update_now.begin(registry, UpdateHelper(registry))


async def wait_for_idle_and_hand_over(redis, registry, *, interval=2.0, quiet_checks=3):
    """Never interrupts work: waits for several quiet readings, closes admission, re-checks."""
    from orion.kernel import activity
    quiet = 0
    try:
        while True:
            state = await asyncio.to_thread(update_now.read, registry)
            if state is None or state['phase'] != 'waiting_for_idle':
                return  # Cancelled or taken over.
            seen = await activity.snapshot(redis)
            await asyncio.to_thread(update_now.update, registry, only={'waiting_for_idle'}, busy=seen['counts'])
            quiet = 0 if seen['busy'] else quiet + 1
            if quiet >= quiet_checks:
                await activity.set_draining(redis, True)
                if not (await activity.snapshot(redis))['busy'] and await asyncio.to_thread(update_now.handoff, registry):
                    return
                await activity.set_draining(redis, False)
                quiet = 0
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        # The Hub cannot judge idleness: stay safe and report instead of applying.
        await asyncio.to_thread(update_now.update, registry, only={'waiting_for_idle'}, phase='failed', error='ActivityCheckFailed')
        with suppress(Exception):
            await activity.set_draining(redis, False)


APPLY_MESSAGES = {
    'NothingStaged': 'Uygulanacak hazır bir güncelleme yok.',
    'AlreadyRequested': 'Güncelleme isteği zaten alındı.',
    'WatcherUnavailable': 'Güncelleme yardımcısı çalışmıyor. Orion’u yeniden başlatın; hazır sürüm açılışta uygulanır.',
    'NotCancellable': 'İstek artık iptal edilemez.',
}


def _apply_error(error):
    return HTTPException(409, APPLY_MESSAGES.get(str(error), 'Güncelleme başlatılamadı. Durumu kontrol edip yeniden deneyin.'))


@router.post('/apply')
async def apply_now(request: Request, user: str = Depends(get_current_user)):
    """Explicit "update now": waits for idle, then the retained watcher applies the staged update."""
    if not local_control(request):
        raise HTTPException(403, 'Güncelleme yalnızca bu bilgisayardan başlatılabilir.')
    registry = Registry()
    try:
        await asyncio.to_thread(begin_apply, registry)
    except (UpdateError, OSError, ValueError, RuntimeError) as error:
        raise _apply_error(error) from error
    tasks = request.app.state.__dict__.setdefault('apply_tasks', set())
    task = asyncio.create_task(wait_for_idle_and_hand_over(request.app.state.redis, registry))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return await asyncio.to_thread(read_status)


@router.post('/result/seen')
async def result_seen(request: Request, user: str = Depends(get_current_user)):
    """The app showed the outcome of the last update once; do not show it again."""
    if not local_control(request):
        raise HTTPException(403, 'Güncelleme sonucu yalnızca bu bilgisayardan işaretlenebilir.')
    try:
        return {'last_result': await asyncio.to_thread(update_ui.mark_seen, Registry())}
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(409, 'Güncelleme sonucu işaretlenemedi.') from error


@router.post('/apply/cancel')
async def cancel_apply(request: Request, user: str = Depends(get_current_user)):
    if not local_control(request):
        raise HTTPException(403, 'Güncelleme yalnızca bu bilgisayardan iptal edilebilir.')
    try:
        await asyncio.to_thread(update_now.cancel, Registry())
    except (UpdateError, OSError, ValueError, RuntimeError) as error:
        raise _apply_error(error) from error
    from orion.kernel import activity
    await activity.set_draining(request.app.state.redis, False)
    return await asyncio.to_thread(read_status)
