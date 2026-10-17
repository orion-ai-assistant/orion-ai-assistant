"""Authenticated status; local desktop authority for preference changes only."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool
from typing import Literal

from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import is_desktop_peer, local_flutter_origin
from services.shared.installation_registry import Registry
from services.shared.automatic_updates import AutomaticUpdates, runtime_command

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
    data['router_configured'] = bool((AutomaticUpdates(registry).source() or {}).get('targets', {}).get('router')
                                     and any(c['kind'] == 'router' and c['ownership'] == 'owned'
                                             and c['management'] == 'local' for c in record['components']))
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
    model_config = {'extra': 'forbid'}


def change_preferences(payload):
    registry = Registry()
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
    return read_status()


@router.put('')
async def preferences(payload: Preferences, request: Request, user: str = Depends(get_current_user)):
    if not local_control(request):
        raise HTTPException(403, 'PC güncelleme tercihi yalnızca bu bilgisayardan değiştirilebilir.')
    try:
        return await asyncio.to_thread(change_preferences, payload)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(409, 'Tercih kaydedilemedi. Güncelleme kaynağını ve bekleyen işlemi kontrol edip yeniden deneyin.') from error
