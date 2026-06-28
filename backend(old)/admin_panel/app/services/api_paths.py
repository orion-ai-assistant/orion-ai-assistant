"""Centralized API endpoint paths used by admin panel services."""

API_HEALTH = "/api/health"
ADMIN_AUTH_VERIFY = "/admin/auth/verify"

ADMIN_CONFIG_BASE = "/admin/config"
ADMIN_CONFIG_ROOT = "/admin/config/"
ADMIN_CONFIG_RESET = "/admin/config/reset"
ADMIN_CONFIG_THINKING_TOGGLE = "/admin/config/thinking/toggle"

ADMIN_USERS_ACTIVE = "/admin/users/active"
ADMIN_USERS_STATS = "/admin/users/stats"


def admin_config_model_default(model_name: str) -> str:
    return f"/admin/config/model/{model_name}/default"


def admin_config_model_toggle(model_name: str) -> str:
    return f"/admin/config/model/{model_name}/toggle"


def admin_config_tool_toggle(tool_name: str) -> str:
    return f"/admin/config/tool/{tool_name}/toggle"
