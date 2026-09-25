from __future__ import annotations

from collections.abc import Mapping
import logging
import json
from pydantic import ValidationError
from redis.asyncio import Redis

from orion.contracts.constants import SETTINGS_HASH_KEY_PREFIX, SETTINGS_DEFAULT_USER
from orion.contracts.settings import RuntimeSettings
from orion.kernel.registry import fetch_setting_overrides, upsert_setting_overrides, insert_missing_setting_overrides, fetch_all_settings, delete_setting_override

settings = RuntimeSettings()
_allowed_keys = set(settings.model_dump().keys())
logger = logging.getLogger(__name__)


def serialize_setting(value) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def is_protected_global_key(key: str) -> bool:
    """Pydantic şemasında tanımlı olan anahtarlar korunur.
    Legacy/obsolete anahtarlar (şemada olmayan) silinebilir."""
    return key.lower() in _allowed_keys


def _clean_legacy_value(key: str, val: str) -> str:
    if key == "router_api_key" and val == "sk-60f3eaf169d7c485-0icocf-0a3db541":
        return ""
    if key == "thinking_level" and val.lower() == "default":
        return ""
    return val


def build_runtime_settings(overrides: Mapping[str, str] | None = None) -> RuntimeSettings:
    defaults = settings.model_dump()
    data = defaults.copy()
    if overrides:
        for key, value in overrides.items():
            normalized_key = key.lower()
            if normalized_key in data:
                data[normalized_key] = _clean_legacy_value(normalized_key, str(value))
    try:
        return RuntimeSettings.model_validate(data)
    except ValidationError as exc:
        # Older database rows may predate the constraints. Keep the service usable
        # while the administrator corrects those rows in the panel.
        for error in exc.errors():
            key = error["loc"][0]
            if key in defaults:
                logger.warning("Ignoring invalid stored setting: %s", key)
                data[key] = defaults[key]
        return RuntimeSettings.model_validate(data)


def _normalize_overrides(overrides: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized_key = key.lower()
        if normalized_key in _allowed_keys:
            normalized[normalized_key] = _clean_legacy_value(normalized_key, str(value))
    return normalized


def _settings_key_for_user(user_id: str) -> str:
    return f"{SETTINGS_HASH_KEY_PREFIX}{user_id}"


async def _read_overrides_from_redis(redis: Redis, user_id: str) -> dict[str, str]:
    raw = await redis.hgetall(_settings_key_for_user(user_id))
    if not raw:
        return {}
    overrides: dict[str, str] = {}
    for key, value in raw.items():
        overrides[str(key)] = str(value)
    return overrides


async def _replace_overrides_in_redis(
    redis: Redis,
    user_id: str,
    overrides: Mapping[str, str],
) -> None:
    key = _settings_key_for_user(user_id)
    await redis.delete(key)
    if overrides:
        await redis.hset(key, mapping=overrides)


async def _get_effective_overrides(user_id: str) -> dict[str, str]:
    global_overrides = await fetch_setting_overrides(SETTINGS_DEFAULT_USER)
    user_overrides = {} if user_id == SETTINGS_DEFAULT_USER else await fetch_setting_overrides(user_id)
    return _normalize_overrides({**global_overrides, **user_overrides})


async def get_runtime_settings(redis: Redis | None = None, user_id: str | None = None) -> RuntimeSettings:
    # 1. Base case: No user specified, return hardcoded env defaults
    if not user_id:
        return build_runtime_settings()

    # 2. Try Redis Cache first
    if redis is not None:
        cached_overrides = await _read_overrides_from_redis(redis, user_id)
        if cached_overrides:
            return build_runtime_settings(cached_overrides)

    # 3. Cache Miss: Fetch from DB
    # Fetch global defaults and user-specific overrides from DB
    normalized = await _get_effective_overrides(user_id)
    
    runtime_settings = build_runtime_settings(normalized)
    
    # 4. Update Redis with TTL
    if redis is not None:
        key = _settings_key_for_user(user_id)
        await redis.delete(key)
        if normalized:
            await redis.hset(key, mapping=normalized)
            await redis.expire(key, runtime_settings.redis_cache_ttl_seconds)
            
    return runtime_settings

async def seed_database_settings(redis: Redis) -> None:
    """Add missing schema defaults to the global settings on every startup."""
    existing = await fetch_setting_overrides(SETTINGS_DEFAULT_USER)
    defaults = {key: serialize_setting(value) for key, value in settings.model_dump().items()}
    missing = {key: value for key, value in defaults.items() if key not in existing}
    if missing:
        await insert_missing_setting_overrides(SETTINGS_DEFAULT_USER, missing)

    updates = {}
    if existing.get("temperature") == "0.7":
        updates["temperature"] = "0.9"
    if existing.get("router_model_group") == "local-model":
        updates["router_model_group"] = "local-chat"
    if updates:
        await upsert_setting_overrides(SETTINGS_DEFAULT_USER, updates)

    if missing or updates:
        await refresh_runtime_settings(redis, SETTINGS_DEFAULT_USER)


async def refresh_runtime_settings(redis: Redis, user_id: str) -> RuntimeSettings:
    overrides = await _get_effective_overrides(user_id)
    runtime_settings = build_runtime_settings(overrides)
    key = _settings_key_for_user(user_id)
    await redis.delete(key)
    if overrides:
        await redis.hset(key, mapping=overrides)
        await redis.expire(key, runtime_settings.redis_cache_ttl_seconds)
    return runtime_settings


async def update_runtime_settings(
    redis: Redis,
    updates: Mapping[str, str],
    user_id: str,
) -> RuntimeSettings:
    normalized = _normalize_overrides(updates)
    if not normalized:
        return await get_runtime_settings(redis, user_id)

    data = settings.model_dump()
    data.update(normalized)
    runtime_settings = RuntimeSettings.model_validate(data)

    if "tool_selection" in normalized:
        from orion.worker.tools._registry import get_registry
        get_registry().validate_selection(runtime_settings.tool_selection)
        normalized["tool_selection"] = runtime_settings.tool_selection.model_dump_json()
        await upsert_setting_overrides(user_id, normalized, require_db=True)
    else:
        await upsert_setting_overrides(user_id, normalized)

    effective_overrides = await _get_effective_overrides(user_id)
    runtime_settings = build_runtime_settings(effective_overrides)

    key = _settings_key_for_user(user_id)
    await redis.delete(key)
    if effective_overrides:
        await redis.hset(key, mapping=effective_overrides)
    await redis.expire(key, runtime_settings.redis_cache_ttl_seconds)

    # Global defaults must propagate to existing inheriting users immediately.
    if user_id == SETTINGS_DEFAULT_USER and "tool_selection" in normalized:
        async for cached_key in redis.scan_iter(match=f"{SETTINGS_HASH_KEY_PREFIX}*"):
            if cached_key != key:
                await redis.delete(cached_key)

    return runtime_settings

async def get_all_users_settings() -> dict[str, dict[str, str]]:
    return await fetch_all_settings()

async def delete_runtime_setting(redis: Redis, user_id: str, key: str) -> None:
    normalized_key = key.lower()

    # Global kullanıcının aktif şema anahtarları silinemez (fabrika ayarı koruması)
    if user_id == SETTINGS_DEFAULT_USER and is_protected_global_key(normalized_key):
        raise ValueError(
            f"'{normalized_key}' global varsayılan ayardır ve silinemez. "
            f"Değerini değiştirmek için güncelleme (update) kullanın."
        )

    await delete_setting_override(user_id, normalized_key)
    await redis.hdel(_settings_key_for_user(user_id), normalized_key)

