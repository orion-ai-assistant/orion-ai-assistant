"""Admin endpoints for AI configuration management."""
from fastapi import APIRouter, HTTPException

from config.config_manager import get_config_manager
from config.schemas import AIConfig, ConfigUpdateRequest

router = APIRouter(prefix="/admin/config", tags=["Admin - Configuration"])


def _cm():
    """Config manager kısayolu."""
    return get_config_manager()


def _wrap_400(fn):
    try:
        return fn()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _wrap_404(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/", response_model=AIConfig)
async def get_config():
    """Get current AI configuration."""
    return _cm().get_config()


@router.put("/", response_model=AIConfig)
async def update_config(update: ConfigUpdateRequest):
    """Update AI configuration (partial update)."""
    return _wrap_400(lambda: _cm().update_config(update))


@router.post("/reset", response_model=AIConfig)
async def reset_config(admin_name: str = "admin"):
    """Reset configuration to default values."""
    return _cm().reset_to_default(admin_name=admin_name)


@router.post("/thinking/toggle")
async def toggle_thinking(enabled: bool, admin_name: str = "admin"):
    """Toggle AI thinking mode on/off."""
    config = _cm().toggle_thinking(enabled, admin_name)
    return {
        "success": True,
        "thinking_enabled": config.thinking_enabled,
        "updated_by": config.updated_by,
        "last_updated": config.last_updated
    }


@router.post("/model/{model_name}/default")
async def set_default_model(model_name: str, admin_name: str = "admin"):
    """Set a specific model as the default."""
    config = _wrap_404(lambda: _cm().set_default_model(model_name, admin_name))
    return {
        "success": True,
        "default_model": model_name,
        "models": config.models,
        "updated_by": config.updated_by
    }


@router.post("/model/{model_name}/toggle")
async def toggle_model(model_name: str, enabled: bool, admin_name: str = "admin"):
    """Enable or disable a specific model."""
    config = _wrap_404(lambda: _cm().toggle_model(model_name, enabled, admin_name))
    return {
        "success": True,
        "model": model_name,
        "enabled": enabled,
        "models": config.models,
        "updated_by": config.updated_by
    }


@router.post("/tool/{tool_name}/toggle")
async def toggle_tool(tool_name: str, enabled: bool, admin_name: str = "admin"):
    """Enable or disable a specific tool."""
    config = _wrap_404(lambda: _cm().toggle_tool(tool_name, enabled, admin_name))
    return {
        "success": True,
        "tool": tool_name,
        "enabled": enabled,
        "tools": config.tools,
        "updated_by": config.updated_by
    }

@router.post("/agent/{agent_name}/tool/{tool_name}/toggle")
async def toggle_agent_tool(agent_name: str, tool_name: str, enabled: bool, admin_name: str = "admin") -> dict:
    """Enable/disable a tool for a specific agent."""
    config = _cm().get_config()
    agent = config.agents.get(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    current_tools = getattr(agent, "tools", None)
    if current_tools is None or len(current_tools) == 0:
        current_tools = [t.name for t in config.tools if getattr(t, "enabled", False)]

    if enabled and tool_name not in current_tools:
        current_tools.append(tool_name)
    elif not enabled and tool_name in current_tools:
        current_tools.remove(tool_name)

    updated_agents = {}
    for name, profile in config.agents.items():
        if name == agent_name:
            updated_agents[name] = profile.model_copy(update={"tools": current_tools})
        else:
            updated_agents[name] = profile

    updated_config = _wrap_400(
        lambda: _cm().update_config(
            ConfigUpdateRequest(
                agents={name: profile.model_dump() for name, profile in updated_agents.items()},
                updated_by=admin_name,
            )
        )
    )
    return {
        "success": True,
        "agent": agent_name,
        "tool": tool_name,
        "enabled": enabled,
        "agent_tools": updated_config.agents.get(agent_name).tools,
        "updated_by": updated_config.updated_by,
    }
