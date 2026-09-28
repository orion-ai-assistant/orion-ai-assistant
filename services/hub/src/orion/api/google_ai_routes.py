"""Native desktop session controls; cookies never cross HTTP."""

import ipaddress
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from orion.api.auth_routes import get_current_user
from urllib.parse import urlencode
from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable

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


@router.get('')
async def session_status(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    return await BrowserManager(user).status()


@router.post('')
async def session_open(payload: OpenSession, request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    url = 'https://www.google.com/search?' + urlencode({'q': payload.query, 'udm': '50', 'hl': 'tr'}) if payload.verify else None
    try:
        await BrowserManager(user).open_manual(url=url, tag='google_ai' if payload.verify else None)
    except BrowserBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except BrowserUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'message': 'Mevcut tarayıcınızda doğrulamayı tamamlayın, “Devam et” düğmesine basın ve sorunuzu yeniden gönderin.'}


@router.delete('')
async def session_delete(request: Request, user: str = Depends(get_current_user)):
    require_local(request)
    raise HTTPException(409, 'Bu profil diğer sitelerle ortaktır. Google oturumunu tarayıcı içinden kapatın.')
