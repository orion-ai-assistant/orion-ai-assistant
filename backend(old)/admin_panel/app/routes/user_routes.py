"""User activity and management endpoints."""
from fastapi import APIRouter, HTTPException, Request
from typing import List

from admin_panel.app.services.user_service import UserService
from config.models import UserActivity

router = APIRouter(prefix="/api/users", tags=["Users"])

# Service instance
user_service = UserService()


def _get_token(request: Request) -> str:
    return request.cookies.get("admin_session", "")


@router.get("/", response_model=List[UserActivity])
async def get_active_users(request: Request):
    """Get list of all active users with their devices."""
    return await user_service.get_active_users(token=_get_token(request))


@router.get("/{user_id}", response_model=UserActivity)
async def get_user_details(user_id: str, request: Request):
    """Get detailed information about a specific user."""
    user = await user_service.get_user_details(user_id, token=_get_token(request))
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    return user


@router.get("/{user_id}/devices")
async def get_user_devices(user_id: str, request: Request):
    """Get all devices connected by a specific user."""
    user = await user_service.get_user_details(user_id, token=_get_token(request))
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    
    return {
        "user_id": user_id,
        "device_count": user.active_device_count,
        "devices": user.devices
    }
