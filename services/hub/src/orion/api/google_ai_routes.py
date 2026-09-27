"""Native desktop session controls; cookies never cross HTTP."""

import asyncio
import ipaddress
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from orion.api.auth_routes import get_current_user
from orion.worker.tools.google_ai.profile import ProfileBusy, delete_profile, profile_status

router = APIRouter(prefix='/api/v1/google-ai/session')


def require_local(request: Request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    desktop = os.name == 'nt' or sys.platform == 'darwin' or bool(os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY'))
    if not local or not desktop or Path('/.dockerenv').exists():
        raise HTTPException(403, 'Google giriş penceresi yalnızca bu bilgisayardaki yerel Orion üzerinden açılabilir.')


class OpenSession(BaseModel):
    verify: bool = False
    query: str = Field(default='Türkiye’nin başkenti neresidir?', min_length=1)


def launch_login(user_id, verify, query):
    command = [sys.executable, '-m', 'orion.worker.tools.google_ai.login', '--user=' + user_id]
    if verify:
        command.append('--verify')
    command.append('--query=' + query)
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[2])
    env['PYTHONPATH'] = os.pathsep.join([src, env.get('PYTHONPATH', '')])
    process = subprocess.Popen(
        command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    # Reap the helper after the user closes its browser (at most 15 minutes).
    threading.Thread(target=process.wait, daemon=True).start()
    with process.stdout:
        ready = process.stdout.readline().strip() == b'ready'
    if not ready:
        raise HTTPException(503, 'Tarayıcı açılamadı. Açık Google penceresini kapatın; Playwright ve pencereli Chromium kurulumunu kontrol edin.')


@router.get('')
async def session_status(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    return await asyncio.to_thread(profile_status, user)


@router.post('')
async def session_open(payload: OpenSession, request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    if (await asyncio.to_thread(profile_status, user))['busy']:
        raise HTTPException(409, 'Google profili kullanımda. Açık pencereyi kapatın veya aramanın bitmesini bekleyin.')
    await asyncio.to_thread(launch_login, user, payload.verify, payload.query)
    return {'message': 'Google penceresi açıldı. Giriş veya doğrulamayı tamamlayıp pencereyi kapatın; ardından sorunuzu yeniden gönderin.'}


@router.delete('')
async def session_delete(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    try:
        await asyncio.to_thread(delete_profile, user)
    except ProfileBusy as exc:
        raise HTTPException(409, 'Önce Google penceresini kapatın ve aramanın bitmesini bekleyin.') from exc
    return {'saved': False, 'busy': False}
