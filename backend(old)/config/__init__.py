"""Shared config package exports.

Uses lazy imports so that importing schemas/models does NOT trigger
config_manager (which depends on heavy API env vars).
"""


def __getattr__(name: str):
    """Lazy attribute lookup — avoids eager config_manager import chain."""
    _exports = {
        # config_manager
        "ConfigManager": "config.config_manager",
        "get_config_manager": "config.config_manager",
        # schemas
        "AIConfig": "config.schemas",
        "ConfigUpdateRequest": "config.schemas",
        "LogSettings": "config.schemas",
        "ModelConfig": "config.schemas",
        "ToolConfig": "config.schemas",
        "AgentProfile": "config.schemas",
        # models
        "DeviceInfo": "config.models",
        "UserActivity": "config.models",
        "UserStats": "config.models",
    }

    if name in _exports:
        import importlib
        module = importlib.import_module(_exports[name])
        value = getattr(module, name)
        globals()[name] = value
        return value

    raise AttributeError(f"module 'config' has no attribute {name}")


__all__ = [
    "ConfigManager", "get_config_manager",
    "AIConfig", "ConfigUpdateRequest", "LogSettings", "ModelConfig", "ToolConfig", "AgentProfile",
    "DeviceInfo", "UserActivity", "UserStats",
]