"""
AI Configuration models for the main API.
Facade module that exports the actual models.
"""
from .user_activity import DeviceInfo, UserActivity
from config.schemas import ModelConfig, ToolConfig, AIConfig, ConfigUpdateRequest

_models = [
    DeviceInfo, UserActivity, ModelConfig, 
    ToolConfig, AIConfig, ConfigUpdateRequest
]

__all__ = [m.__name__ for m in _models]