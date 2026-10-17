"""Authenticated status; local desktop authority for preference changes only."""
import asyncio
from contextlib import suppress
import subprocess
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool
from typing import Literal

from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import is_desktop_peer, local_flutter_origin
from services.shared.installation_registry import Registry
from services.shared import update_now
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
    return bool((AutomaticUpdates(registry).source() or {}).get('targets', {}).get('router')
                and any(c['kind'] == 'router' and c['ownership'] == 'owned'
                        and c['management'] == 'local' for c in record['components']))


def read_status():
    registry = Registry()
    if registry.read() is None:
        return {'available': False, 'configured': False, 'automatic_updates': False,
                'message': 'Kayıtlı paket kurulumu yok.'}
    data = AutomaticUpdates(registry).status()
    data['available'] = runtime_command(registry) is not None
    record = registry.read()
    data['launcher_preferences'] = {k: record['preferences'][k] for k in ('start_with_windows', 'show_client')}
    data['router_automatic_updates'] = AutomaticUpdates(registry, scope='router').policy()['enabled']
    data['router_configured'] = router_managed(registry, record)
    data['apply_now'] = update_now.view(registry)
    return data


@router.get('')
async def status(request: Request, user: str = Depends(get_current_user)):
    try:
        result = await asyncio.to_thread(read_status)
        return {**result, 'can_manage': local_control(request)}
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(503, 'Güncelleme bilgileri okunamadı; yerel onarma kaydını kontrol edin.') from error


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
    # A concurrent check holds the per-installation lock and the extra process exits at once.
    process = subprocess.Popen(command + ['check', '--now'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                               close_fds=True)
    # A bootstrap too old for these arguments exits at once (argparse code 2). Make that
    # visible in automatic.json for the card instead of silently doing nothing.
    threading.Thread(target=_report_failure, args=(process, registry), daemon=True).start()


def _report_failure(process, registry):
    try:
        code = process.wait()
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
        raise HTTPException(409, 'Tercih kaydedilemedi. Güncelleme kaynağını ve bekleyen işlemi kontrol edip yeniden deneyin.') from error


def watcher_alive(registry):
    """The retained watcher holds this lock for as long as it runs (outside the Hub's Job)."""
    try:
        with operation_lock(registry.path.parent / 'updates/watcher.json', timeout=0):
            return False
    except LifecycleError:
        return True


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
