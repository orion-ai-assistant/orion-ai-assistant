"""Authenticated local desktop browser controls. No cookies cross HTTP."""

from typing import Literal, Optional
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orion.api.auth_routes import get_current_user
import os
import sys
from pathlib import Path
from orion.api.access_boundary import is_desktop_peer
from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable
from services.shared.flutter_launcher import open_window
from services.shared.environment import get_env

router = APIRouter(prefix='/api/v1/browser')


def require_local(request):
    desktop = os.name == 'nt' or sys.platform == 'darwin' or bool(os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY'))
    if not is_desktop_peer(request.scope) or not desktop or Path('/.dockerenv').exists():
        raise HTTPException(403, 'Tarayıcı bağlantısı yalnızca Hub bilgisayarından yönetilebilir.')


class OpenBrowser(BaseModel):
    browser: Optional[Literal['chrome', 'edge', 'brave']] = None


@router.post('/panel/open')
async def open_panel(request: Request, user: str = Depends(get_current_user)):
    """Open a fixed local panel; this grants no owner session or API authority."""
    require_local(request)
    port = int(get_env('ORION_LOCAL_PORT', default='8910', required=False))
    try:
        opened = await asyncio.to_thread(open_window, f'http://localhost:{port}/panel', allow_tab_fallback=False)
    except OSError as error:
        raise HTTPException(503, 'Hub panelinin uygulama penceresi açılamadı. Yeniden deneyin.') from error
    if not opened:
        raise HTTPException(503, 'Uygulama penceresi için bu bilgisayarda Chrome, Edge veya Brave kurulu olmalı.')
    return {'message': 'Hub yönetim paneli ayrı Orion penceresinde açıldı.'}


@router.get('')
async def status(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    client_browser = request.headers.get('x-client-browser')
    return await BrowserManager(user).status(client_browser=client_browser)


@router.post('/open')
async def open_browser(payload: OpenBrowser, request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    client_browser = request.headers.get('x-client-browser')
    target = payload.browser
    if not target:
        saved = await BrowserManager(user).status(client_browser=client_browser)
        target = saved.get('browser') if saved.get('saved') else None
    target = target or (client_browser if client_browser in ('brave', 'edge', 'chrome') else None) or 'chrome'
    try:
        await BrowserManager(user).open_manual(target)
    except BrowserBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except BrowserUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'message': 'Mevcut tarayıcınıza bağlanıldı. Açık site oturumlarınız kullanılacak.'}


@router.post('/resume')
async def resume(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    try:
        await BrowserManager(user).resume()
    except BrowserBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except BrowserUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'message': 'Mevcut tarayıcı kullanıma hazır. Sorunuzu yeniden gönderebilirsiniz.'}


@router.post('/disconnect')
async def disconnect(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    await BrowserManager(user).disconnect()
    return {'message': 'Tarayıcı bağlantısı kapatıldı ve otomatik bağlanma devre dışı bırakıldı.'}
