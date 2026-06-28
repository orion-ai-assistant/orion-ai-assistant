from __future__ import annotations

from typing import Any, Dict, Optional, Union

from log.settings_models import LogComponents, LogLevels, LogSettings
from log.logger import LogLevel
from log.registry import get_registered_components

_LEVEL_KEYS = [level.name for level in LogLevel]


def get_log_levels() -> list[str]:
    """Returns the authoritative ordered log levels."""
    return list(_LEVEL_KEYS)


def _to_level_key(level: Union[LogLevel, str]) -> Optional[str]:
    if isinstance(level, LogLevel):
        return level.name
    key = str(level).strip().upper()
    return key if key in _LEVEL_KEYS else None


def _default_levels_enabled() -> dict[str, bool]:
    return {key: True for key in _LEVEL_KEYS}


def _normalize_levels(levels: Optional[Union[LogLevels, Dict[Union[LogLevel, str], bool]]]) -> dict[str, bool]:
    normalized = _default_levels_enabled()
    if not levels:
        return normalized

    if isinstance(levels, LogLevels):
        levels = levels.as_dict()

    for level_key, value in (levels or {}).items():
        normalized_key = _to_level_key(level_key)
        if normalized_key is not None:
            normalized[normalized_key] = bool(value)

    return normalized


def _build_default_settings() -> LogSettings:
    return LogSettings(
        enabled=True,
        levels=LogLevels(**{key: True for key in _LEVEL_KEYS}),
        components=LogComponents(),
    )


_system_log_settings: LogSettings = _build_default_settings()


def _to_component_dict(components: Optional[Union[LogComponents, Dict[str, bool]]]) -> dict[str, bool]:
    if components is None:
        return {}
    if isinstance(components, dict):
        return {k.lower(): bool(v) for k, v in components.items()}
    if hasattr(components, "as_dict"):
        return {k.lower(): bool(v) for k, v in components.as_dict().items()}
    if hasattr(components, "model_dump"):
        return {k.lower(): bool(v) for k, v in components.model_dump().items()}
    return {}


def components_to_dict(components: Optional[LogComponents]) -> dict[str, bool]:
    if components is None:
        return {}
    return _to_component_dict(components)


def _apply_component_registry(settings: LogSettings) -> None:
    current = _to_component_dict(settings.components)

    component_names = set(get_registered_components())
    component_names.update(k for k in current.keys() if k != "all")

    synced_components: dict[str, bool] = {}
    for component in sorted(component_names):
        synced_components[component] = bool(current.get(component, True))

    settings.components = LogComponents(**synced_components)


def configure_system_log(
    enabled: Optional[bool] = None,
    components: Optional[LogComponents] = None,
    levels: Optional[LogLevels] = None,
) -> None:
    """Configure system logging behavior."""
    global _system_log_settings

    if enabled is not None:
        _system_log_settings.enabled = bool(enabled)
    if components is not None:
        _system_log_settings.components = components
    if levels is not None:
        normalized_levels = _normalize_levels(levels)
        _system_log_settings.levels = LogLevels(**normalized_levels)

    _apply_component_registry(_system_log_settings)


def _levels_to_dict(levels: Optional[Union[LogLevels, Dict[str, Any]]]) -> dict[str, bool]:
    if isinstance(levels, LogLevels):
        return levels.as_dict()
    if isinstance(levels, dict):
        return {k: bool(v) for k, v in levels.items()}
    return {}


def get_configured_logging() -> LogSettings:
    configured = LogSettings(
        enabled=_system_log_settings.enabled,
        levels=LogLevels(**_levels_to_dict(_system_log_settings.levels)),
        components=LogComponents(**components_to_dict(_system_log_settings.components)),
    )

    _apply_component_registry(configured)
    return configured


def should_log(level_enum: LogLevel) -> bool:
    config = get_configured_logging()
    if not config.enabled:
        return False

    level_name = level_enum.name
    return bool(_levels_to_dict(config.levels).get(level_name, False))
