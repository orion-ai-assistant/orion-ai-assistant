"""Bileşenler: optional local AI parts (services/shared/local_parts.py).

Anyone signed in sees what is installed and what runs; only this PC's own Orion window installs, removes or
changes a part (the same desktop authority as updates: no account, phone token or header grants it).
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from orion.api.auth_routes import get_current_user
from orion.api.update_routes import local_control
from services.shared import local_parts

router = APIRouter(prefix='/api/v1/parts')


class PartChange(BaseModel):
    model: str | None = None
    settings: dict | None = None


def _manage(request):
    if not local_control(request):
        raise HTTPException(403, 'Bileşenleri yalnızca Hub bilgisayarındaki Orion penceresi değiştirebilir.')


async def _act(function, *args, **kwargs):
    try:
        await asyncio.to_thread(function, *args, **kwargs)
    except local_parts.PartError as error:
        raise HTTPException(400, str(error)) from error
    return await _status_body()


async def _status_body():
    return await asyncio.to_thread(local_parts.status)


@router.get('')
async def parts(request: Request, current_user: str = Depends(get_current_user)):
    return JSONResponse({**await _status_body(), 'can_manage': local_control(request)},
                        headers={'Cache-Control': 'no-store'})


@router.post('/{part_id}/install')
async def install(part_id: str, change: PartChange, request: Request, current_user: str = Depends(get_current_user)):
    """Fetch what is missing (engine, model), turn the part on and start it."""
    _manage(request)
    return await _act(local_parts.start_job, part_id, 'install', model=change.model, settings=change.settings)


@router.post('/{part_id}/models/{model_id}/download')
async def download(part_id: str, model_id: str, request: Request, current_user: str = Depends(get_current_user)):
    _manage(request)
    return await _act(local_parts.start_job, part_id, 'download', model=model_id)


@router.delete('/{part_id}/models/{model_id}')
async def delete_model(part_id: str, model_id: str, request: Request, current_user: str = Depends(get_current_user)):
    _manage(request)
    return await _act(local_parts.delete_model, part_id, model_id)


@router.put('/{part_id}')
async def change(part_id: str, change: PartChange, request: Request, current_user: str = Depends(get_current_user)):
    """Choose the model and/or advanced settings; a running part restarts with them."""
    _manage(request)
    return await _act(local_parts.select, part_id, model=change.model, settings=change.settings)


class PartRemove(BaseModel):
    delete_models: bool = False


@router.post('/{part_id}/remove')
async def remove(part_id: str, request: Request, body: PartRemove | None = None,
                 current_user: str = Depends(get_current_user)):
    """Turn the part off; with delete_models its downloaded models go too."""
    _manage(request)
    return await _act(local_parts.remove, part_id, delete_models=bool(body and body.delete_models))


@router.post('/{part_id}/{action}')
async def act(part_id: str, action: str, request: Request, current_user: str = Depends(get_current_user)):
    _manage(request)
    function = {'cancel': local_parts.cancel, 'start': local_parts.start, 'stop': local_parts.stop}.get(action)
    if function is None:
        raise HTTPException(404, 'Bu işlem yok.')
    return await _act(function, part_id)
