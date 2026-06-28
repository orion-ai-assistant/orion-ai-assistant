from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

class DeviceInfo(BaseModel):
    """Information about a connected device."""
    model_config = ConfigDict(from_attributes=True) # Nesne tabanlı erişim için
    
    session_id: str
    connected_at: datetime = Field(default_factory=datetime.now)
    last_activity: datetime = Field(default_factory=datetime.now)
    chat_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

class UserActivity(BaseModel):
    """User activity summary."""
    user_id: str
    devices: List[DeviceInfo] = Field(default_factory=list)
    total_messages: int = 0
    first_seen: datetime = Field(default_factory=datetime.now)
    last_seen: datetime = Field(default_factory=datetime.now)
    is_active: bool = False
    
    @property
    def active_device_count(self) -> int:
        return len(self.devices)
