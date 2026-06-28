"""Statistics and monitoring service."""
import httpx
from datetime import datetime
from typing import Dict

from admin_panel.app.services.api_paths import API_HEALTH, ADMIN_USERS_STATS
from admin_panel.app.services.backend_client import BackendClient
from config.models import UserStats
from config.settings.admin import API_BACKEND_URL


class StatsService:
    """Service for collecting and computing system statistics."""
    
    def __init__(self, api_url: str = API_BACKEND_URL):
        self._client = BackendClient(api_url)

    async def _get(self, path: str, token: str = "") -> httpx.Response:
        return await self._client.request("GET", path, token=token)

    async def get_system_stats(self, token: str = "") -> Dict:
        """Get overall system statistics."""
        try:
            health_resp = await self._get(API_HEALTH)
            users_resp = await self._get(ADMIN_USERS_STATS, token=token)

            agent_ready = False
            if health_resp.status_code == 200:
                health_data = health_resp.json()
                agent_ready = str(health_data.get("status", "")).lower() == "ok"

            users_data = {}
            messages_today = 0
            active_clients = 0
            if users_resp.status_code == 200:
                users_data = users_resp.json()
                active_clients = users_data.get("unique_users", 0)
                messages_today = users_data.get("messages_today", 0)

            return {
                "status": "healthy" if agent_ready else "degraded",
                "timestamp": datetime.now().isoformat(),
                "backend": {
                    "url": self._client.api_url,
                    "reachable": True,
                    "agent_ready": agent_ready,
                },
                "users": UserStats(
                    total_users=active_clients,
                    active_users=active_clients,
                    total_sessions=users_data.get("total_sessions", 0),
                    total_messages_today=messages_today
                ).model_dump(),
            }
        except httpx.TimeoutException:
            return self._get_error_stats("Backend timeout")
        except Exception as e:
            return self._get_error_stats(f"Error: {str(e)}")
    
    def _get_error_stats(self, error_msg: str) -> Dict:
        """Return error statistics when backend is unreachable."""
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "backend": {
                "url": self._client.api_url,
                "reachable": False,
                "error": error_msg,
            },
            "users": UserStats(
                total_users=0,
                active_users=0,
                total_sessions=0,
                total_messages_today=0
            ).model_dump(),
        }
