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
    model_config = {'extra': 'forbid'}


def change_preferences(payload):
    registry = Registry()
    if payload.automatic_updates is True and runtime_command(registry) is None:
        raise ValueError('Retained update runtime unavailable')
    return AutomaticUpdates(registry).preferences(enabled=payload.automatic_updates,
                                                 paused=payload.paused, defer_hours=payload.defer_hours)


@router.put('')
async def preferences(payload: Preferences, request: Request, user: str = Depends(get_current_user)):
    if not local_control(request):
        raise HTTPException(403, 'PC güncelleme tercihi yalnızca bu bilgisayardan değiştirilebilir.')
    try:
        return await asyncio.to_thread(change_preferences, payload)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(409, 'Tercih kaydedilemedi. Güncelleme kaynağını ve bekleyen işlemi kontrol edip yeniden deneyin.') from error
