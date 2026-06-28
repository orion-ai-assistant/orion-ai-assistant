"""Admin endpoints for user management and monitoring."""
from fastapi import APIRouter
from datetime import datetime
from typing import List, Dict

from api.app.shared_state import manager, session_chat_map, get_user_message_count, get_messages_today
from api.app.models import UserActivity, DeviceInfo

router = APIRouter(prefix="/admin/users", tags=["Admin - Users"])


@router.get("/active")
async def get_active_users():
    """Get all active users with their devices grouped by user_id."""
    # Group sessions by user_id
    user_map: Dict[str, List[str]] = {}  # user_id -> [session_ids]
    
    for session_id in manager.active_connections.keys():
        user_id = manager.get_user_id(session_id)
        if user_id:
            if user_id not in user_map:
                user_map[user_id] = []
            user_map[user_id].append(session_id)
    
    # Build user activity list
    users = []
    for user_id, session_ids in user_map.items():
        devices = []
        for sid in session_ids:
            chat_id = session_chat_map.get(sid)
            devices.append(DeviceInfo(
                session_id=sid,
                connected_at=datetime.now(),
                last_activity=datetime.now(),
                chat_id=chat_id
            ))
        
        users.append(UserActivity(
            user_id=user_id,
            devices=devices,
            total_messages=get_user_message_count(user_id),
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            is_active=True
        ))
    
    return {
        "status": "ok",
        "count": len(users),
        "users": [user.model_dump() for user in users]
    }


@router.get("/stats")
async def get_user_stats():
    """Get overall user statistics."""
    active_count = len(manager.active_connections)
    
    # Count unique users
    unique_users = set()
    for session_id in manager.active_connections.keys():
        user_id = manager.get_user_id(session_id)
        if user_id:
            unique_users.add(user_id)
    
    return {
        "total_sessions": active_count,
        "unique_users": len(unique_users),
        "messages_today": get_messages_today(),
        "average_devices_per_user": active_count / len(unique_users) if unique_users else 0
    }
