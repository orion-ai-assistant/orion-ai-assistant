"""Authenticated local desktop browser controls. No cookies cross HTTP."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orion.api.auth_routes import get_current_user
from orion.api.google_ai_routes import require_local
from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable

router = APIRouter(prefix='/api/v1/browser')


class OpenBrowser(BaseModel):
    browser: Optional[Literal['chrome', 'edge', 'brave']] = None


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
    if client_browser in ('brave', 'edge', 'chrome'):
        if not target or (target == 'chrome' and client_browser != 'chrome'):
            target = client_browser
    target = target or client_browser or 'chrome'
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
