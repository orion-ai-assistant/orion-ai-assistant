"""Configuration management endpoints - proxies to main API."""
from fastapi import APIRouter, HTTPException, Request

from admin_panel.app.services.config_service import ConfigService
from config.schemas import AIConfig, ConfigUpdateRequest

router = APIRouter(prefix="/api/config", tags=["Configuration"])

# Service instance
config_service = ConfigService()


def _get_token(request: Request) -> str:
    return request.cookies.get("admin_session", "")


@router.get("/", response_model=AIConfig)
async def get_config(request: Request):
    """Get current AI configuration from main API."""
    return await config_service.get_config(token=_get_token(request))


@router.put("/", response_model=AIConfig)
async def update_config(update: ConfigUpdateRequest, request: Request):
    """Update AI configuration via main API."""
    try:
        return await config_service.update_config(update, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reset", response_model=AIConfig)
async def reset_config(request: Request, admin_name: str = "admin"):
    """Reset configuration to default values via main API."""
    try:
        return await config_service.reset_to_default(admin_name=admin_name, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/thinking/toggle")
async def toggle_thinking(request: Request, enabled: bool, admin_name: str = "admin"):
    """Toggle AI thinking mode on/off via main API."""
    try:
        return await config_service.toggle_thinking(enabled, admin_name, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/model/{model_name}/default")
async def set_default_model(model_name: str, request: Request, admin_name: str = "admin"):
    """Set a specific model as the default via main API."""
    try:
        return await config_service.set_default_model(model_name, admin_name, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/model/{model_name}/toggle")
async def toggle_model(model_name: str, request: Request, enabled: bool, admin_name: str = "admin"):
    """Enable or disable a specific model via main API."""
    try:
        return await config_service.toggle_model(model_name, enabled, admin_name, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/tool/{tool_name}/toggle")
async def toggle_tool(tool_name: str, request: Request, enabled: bool, admin_name: str = "admin"):
    """Enable or disable a specific tool via main API."""
    try:
        return await config_service.toggle_tool(tool_name, enabled, admin_name, token=_get_token(request))
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
