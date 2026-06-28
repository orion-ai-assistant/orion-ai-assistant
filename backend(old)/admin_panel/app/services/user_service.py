"""User activity tracking service."""
import httpx
from typing import Dict, List, Optional

from admin_panel.app.services.api_paths import ADMIN_USERS_ACTIVE
from admin_panel.app.services.backend_client import BackendClient
from config.models import DeviceInfo, UserActivity
from config.settings.admin import API_BACKEND_URL
from log import Logger

logger = Logger(__file__)


class UserService:
    """Service for tracking and managing user activities."""
    
    def __init__(self, api_url: str = API_BACKEND_URL):
        self._client = BackendClient(api_url)

    async def _get(self, path: str, token: str = "") -> httpx.Response:
        return await self._client.request("GET", path, token=token)

    async def get_active_users(self, token: str = "") -> List[UserActivity]:
        """Fetch active users from the main API backend with real device data."""
        try:
            response = await self._get(ADMIN_USERS_ACTIVE, token=token)
            if response.status_code == 200:
                data = response.json()
                users_data = data.get("users", [])
                return [UserActivity(**user) for user in users_data]
            else:
                logger.warning(lambda: f"API returned {response.status_code}")
                return []
        except Exception as e:
            logger.error(lambda: f"Error fetching users: {e}")
            return []
    
    async def get_user_details(self, user_id: str, token: str = "") -> Optional[UserActivity]:
        """Get details for a specific user."""
        users = await self.get_active_users(token=token)
        for user in users:
            if user.user_id == user_id:
                return user
        return None
    
    def get_user_from_session_map(self, session_data: Dict) -> List[UserActivity]:
        """
        Extract user activities from session_chat_map data.
        This is a helper for when we integrate directly with the API backend.
        """
        return []
