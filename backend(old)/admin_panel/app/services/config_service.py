"""
Configuration management service.
Admin panel artık doğrudan JSON dosyasına değil, ana API'ye HTTP çağrısı yapıyor.
Bu sayede Docker container'lar arası haberleşme ve gerçek zamanlı güncelleme sağlanıyor.
"""
import httpx
from typing import Optional

from admin_panel.app.services.api_paths import (
    ADMIN_CONFIG_RESET,
    ADMIN_CONFIG_ROOT,
    ADMIN_CONFIG_THINKING_TOGGLE,
    admin_config_model_default,
    admin_config_model_toggle,
    admin_config_tool_toggle,
)
from config.schemas import AIConfig, ConfigUpdateRequest
from config.settings.admin import API_BACKEND_URL
from admin_panel.app.services.backend_client import BackendClient
from log import Logger

logger = Logger(__file__)


class ConfigService:
    """
    Service for managing AI configuration via API calls.
    Admin panel → API backend → In-memory config
    """
    
    def __init__(self, api_url: str = API_BACKEND_URL):
        self._client = BackendClient(api_url)

    async def _request(self, method: str, path: str, *, token: str = "", **kwargs) -> httpx.Response:
        return await self._client.request(method, path, token=token, **kwargs)

    async def get_config(self, token: str = "") -> AIConfig:
        """Load configuration from API backend."""
        try:
            response = await self._request("GET", ADMIN_CONFIG_ROOT, token=token)
            if response.status_code == 200:
                data = response.json()
                return AIConfig(**data)
            logger.warning(lambda: f"API returned {response.status_code}")
            return self._get_default_config()
        except Exception as e:
            logger.error(lambda: f"Error fetching config: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> AIConfig:
        """
        API erişilemediğinde dönen boş fallback config.
        Artık varsayılanlar merkezi AIConfig şemasından otomatik gelir.
        """
        return AIConfig()
    
    async def update_config(self, update: ConfigUpdateRequest, token: str = "") -> AIConfig:
        """Update configuration via API."""
        try:
            response = await self._request(
                "PUT",
                ADMIN_CONFIG_ROOT,
                token=token,
                json=update.model_dump(exclude_unset=True)
            )
            if response.status_code == 200:
                data = response.json()
                return AIConfig(**data)
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error updating config: {e}")
            raise
    
    async def toggle_thinking(self, enabled: bool, admin_name: Optional[str] = None, token: str = "") -> dict:
        """Toggle thinking mode via API."""
        try:
            response = await self._request(
                "POST",
                ADMIN_CONFIG_THINKING_TOGGLE,
                token=token,
                params={"enabled": enabled, "admin_name": admin_name or "admin"}
            )
            if response.status_code == 200:
                return response.json()
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error toggling thinking: {e}")
            raise
    
    async def set_default_model(self, model_name: str, admin_name: Optional[str] = None, token: str = "") -> dict:
        """Set default model via API."""
        try:
            response = await self._request(
                "POST",
                admin_config_model_default(model_name),
                token=token,
                params={"admin_name": admin_name or "admin"}
            )
            if response.status_code == 200:
                return response.json()
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error setting default model: {e}")
            raise
    
    async def toggle_model(self, model_name: str, enabled: bool, admin_name: Optional[str] = None, token: str = "") -> dict:
        """Toggle model via API."""
        try:
            response = await self._request(
                "POST",
                admin_config_model_toggle(model_name),
                token=token,
                params={"enabled": enabled, "admin_name": admin_name or "admin"}
            )
            if response.status_code == 200:
                return response.json()
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error toggling model: {e}")
            raise

    async def toggle_tool(self, tool_name: str, enabled: bool, admin_name: Optional[str] = None, token: str = "") -> dict:
        """Toggle tool via API."""
        try:
            response = await self._request(
                "POST",
                admin_config_tool_toggle(tool_name),
                token=token,
                params={"enabled": enabled, "admin_name": admin_name or "admin"}
            )
            if response.status_code == 200:
                return response.json()
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error toggling tool: {e}")
            raise

    async def reset_to_default(self, admin_name: Optional[str] = None, token: str = "") -> AIConfig:
        """Reset configuration via API."""
        try:
            response = await self._request(
                "POST",
                ADMIN_CONFIG_RESET,
                token=token,
                params={"admin_name": admin_name or "admin"}
            )
            if response.status_code == 200:
                data = response.json()
                return AIConfig(**data)
            raise Exception(f"API returned {response.status_code}")
        except Exception as e:
            logger.error(lambda: f"Error resetting config: {e}")
            raise
