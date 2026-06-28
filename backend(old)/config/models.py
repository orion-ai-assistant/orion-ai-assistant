"""Shared Pydantic models used across services."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DeviceInfo(BaseModel):
    """Information about a connected device."""
    session_id: str = Field(..., description="WebSocket session ID")
    connected_at: datetime = Field(default_factory=datetime.now, description="Connection timestamp")
    last_activity: datetime = Field(default_factory=datetime.now, description="Last message timestamp")
    chat_id: Optional[str] = Field(default=None, description="Current chat ID")
    user_agent: Optional[str] = Field(default=None, description="Client user agent")
    ip_address: Optional[str] = Field(default=None, description="Client IP address")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UserActivity(BaseModel):
    """User activity summary."""
    user_id: str = Field(..., description="User ID")
    devices: List[DeviceInfo] = Field(default_factory=list, description="Connected devices")
    total_messages: int = Field(default=0, ge=0, description="Total messages sent")
    first_seen: datetime = Field(default_factory=datetime.now, description="First connection time")
    last_seen: datetime = Field(default_factory=datetime.now, description="Last activity time")
    is_active: bool = Field(default=False, description="Currently has active connections")

    @property
    def active_device_count(self) -> int:
        """Number of currently active devices."""
        return len(self.devices)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UserStats(BaseModel):
    """Overall user statistics."""
    total_users: int = Field(default=0, ge=0)
    active_users: int = Field(default=0, ge=0)
    total_sessions: int = Field(default=0, ge=0)
    total_messages_today: int = Field(default=0, ge=0)
