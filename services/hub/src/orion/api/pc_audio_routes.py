"""Desktop-only authenticated control of the Hub PC's default playback endpoint."""
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import is_desktop_peer
from orion.api.pc_audio import lease

router = APIRouter(prefix="/api/v1/pc-audio", tags=["pc-audio"])

def supported(request):
    return os.name == "nt" and is_desktop_peer(request.scope) and getattr(request.app.state, 'local_entry', False)

def require_pc(request):
    if not supported(request):
        raise HTTPException(403, "PC mute is available only on a local Windows Hub")

class Token(BaseModel):
    token: str = Field(min_length=32, max_length=128)

@router.get("")
async def status(request: Request, user: str = Depends(get_current_user)):
    return {"supported": supported(request)}

@router.post("/acquire")
async def acquire(request: Request, user: str = Depends(get_current_user)):
    require_pc(request)
    try:
        return {"token": await lease.acquire(user), "ttl_seconds": lease.ttl}
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(503, "Windows audio is unavailable") from e

@router.post("/renew")
async def renew(body: Token, request: Request, user: str = Depends(get_current_user)):
    require_pc(request)
    try:
        await lease.renew(user, body.token)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

@router.post("/release")
async def release(body: Token, request: Request, user: str = Depends(get_current_user)):
    require_pc(request)
    try:
        return {"ok": await lease.release(user, body.token)}
    except Exception as e:
        raise HTTPException(503, "Windows audio could not be restored") from e
