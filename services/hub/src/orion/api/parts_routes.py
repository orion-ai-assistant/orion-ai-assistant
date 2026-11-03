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
from services.shared import local_parts, parts_location

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


# --- Where the models are (before the /{part_id} routes, which would take "location" for a part) ---

class Place(BaseModel):
    path: str


async def _answer(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except local_parts.PartError as error:
        raise HTTPException(400, str(error)) from error


@router.post('/location/check')
async def check_location(body: Place, request: Request, current_user: str = Depends(get_current_user)):
    """Where the models would go for a folder the user typed, that disk's free space, why it cannot be."""
    _manage(request)
    return await _answer(parts_location.check, body.path)


@router.post('/location/pick')
async def pick_location(request: Request, current_user: str = Depends(get_current_user)):
    """A folder dialog on the Hub PC; answers its id at once (the choice: GET /location/pick/<id>)."""
    _manage(request)
    return {'id': await _answer(parts_location.pick)}


@router.get('/location/pick/{key}')
async def picked_location(key: str, request: Request, current_user: str = Depends(get_current_user)):
    _manage(request)
    return await _answer(parts_location.picked, key)


@router.post('/location/move')
async def move_location(body: Place, request: Request, current_user: str = Depends(get_current_user)):
    """Move the models there: in the background, the running parts stop and start again from the new place."""
    _manage(request)
    return await _act(parts_location.start_move, body.path)


@router.post('/location/old/delete')
async def delete_old(body: Place, request: Request, current_user: str = Depends(get_current_user)):
    """Delete an old copy a move left."""
    _manage(request)
    return await _act(parts_location.drop_old, body.path)


@router.post('/location/old/keep')
async def keep_old(body: Place, request: Request, current_user: str = Depends(get_current_user)):
    """Keep an old copy (its files stay; the page stops asking)."""
    _manage(request)
    return await _act(parts_location.keep_old, body.path)


@router.post('/{part_id}/install')
async def install(part_id: str, change: PartChange, request: Request, current_user: str = Depends(get_current_user)):
    """Fetch what is missing (engine, model), turn the part on and start it."""
    _manage(request)
    return await _act(local_parts.start_job, part_id, 'install', model=change.model, settings=change.settings)


@router.post('/{part_id}/models/{model_id}/download')
async def download(part_id: str, model_id: str, request: Request, file: str | None = None,
                   current_user: str = Depends(get_current_user)):
    """Fetch a model (`?file=model` or `?file=extra`: only that file)."""
    _manage(request)
    return await _act(local_parts.start_job, part_id, 'download', model=model_id, fetch=[file] if file else None)


@router.delete('/{part_id}/models/{model_id}')
async def delete_model(part_id: str, model_id: str, request: Request, extra: bool = False, file: str | None = None,
                       current_user: str = Depends(get_current_user)):
    """Delete a model's files (`?extra=true`: only its extra file; `?file=<name>`: one file of a user's model);
    a service holding them stops first."""
    _manage(request)
    return await _act(local_parts.delete_model, part_id, model_id, extra=extra, file=file)


class UserModel(BaseModel):
    name: str
    kind: str | None = None


@router.post('/{part_id}/user-models')
async def add_user_model(part_id: str, body: UserModel, request: Request,
                         current_user: str = Depends(get_current_user)):
    """A folder for a model the user brings (they copy its files in); answers its id with the status."""
    _manage(request)
    try:
        added = await asyncio.to_thread(local_parts.add_user_model, part_id, body.name, body.kind)
    except local_parts.PartError as error:
        raise HTTPException(400, str(error)) from error
    return {**await _status_body(), 'added': added}


@router.post('/{part_id}/user-models/open')
async def open_folder(part_id: str, model: str, request: Request, current_user: str = Depends(get_current_user)):
    """Show one of the user's model folders in this PC's file manager."""
    _manage(request)
    return await _act(local_parts.open_folder, part_id, model)


@router.put('/{part_id}')
async def change(part_id: str, change: PartChange, request: Request, current_user: str = Depends(get_current_user)):
    """Choose the model and/or advanced settings: saved only; a running part takes them on Uygula (/apply)."""
    _manage(request)
    return await _act(local_parts.select, part_id, model=change.model, settings=change.settings)


class PartRemove(BaseModel):
    delete_models: bool = False
    delete_user_models: bool = False


@router.post('/{part_id}/remove')
async def remove(part_id: str, request: Request, body: PartRemove | None = None,
                 current_user: str = Depends(get_current_user)):
    """Turn the part off; with delete_models its downloaded models go too (the user's own only when asked)."""
    _manage(request)
    return await _act(local_parts.remove, part_id, delete_models=bool(body and body.delete_models),
                      delete_user_models=bool(body and body.delete_user_models))


@router.post('/{part_id}/{action}')
async def act(part_id: str, action: str, request: Request, current_user: str = Depends(get_current_user)):
    _manage(request)
    function = {'cancel': local_parts.cancel, 'start': local_parts.start, 'stop': local_parts.stop,
                'apply': local_parts.apply_choice}.get(action)
    if function is None:
        raise HTTPException(404, 'Bu işlem yok.')
    return await _act(function, part_id)
