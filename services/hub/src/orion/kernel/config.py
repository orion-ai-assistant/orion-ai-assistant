from __future__ import annotations

from collections.abc import Mapping
from redis.asyncio import Redis

from orion.contracts.constants import SETTINGS_HASH_KEY_PREFIX, SETTINGS_DEFAULT_USER
from orion.contracts.settings import RuntimeSettings
from orion.kernel.registry import fetch_setting_overrides, upsert_setting_overrides, fetch_all_settings, delete_setting_override

settings = RuntimeSettings()
_allowed_keys = set(settings.model_dump().keys())


def is_protected_global_key(key: str) -> bool:
    """Pydantic şemasında tanımlı olan anahtarlar korunur.
    Legacy/obsolete anahtarlar (şemada olmayan) silinebilir."""
    return key.lower() in _allowed_keys


def build_runtime_settings(overrides: Mapping[str, str] | None = None) -> RuntimeSettings:
    data = settings.model_dump()
    if overrides:
        for key, value in overrides.items():
            normalized_key = key.lower()
            if normalized_key in data:
                data[normalized_key] = value
    return RuntimeSettings.model_validate(data)


def _normalize_overrides(overrides: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in overrides.items():
        normalized_key = key.lower()
        if normalized_key in _allowed_keys:
            normalized[normalized_key] = str(value)
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
    global_overrides = await fetch_setting_overrides(SETTINGS_DEFAULT_USER)
    user_overrides = {}
    if user_id != SETTINGS_DEFAULT_USER:
        user_overrides = await fetch_setting_overrides(user_id)
    
    # Merge: Global -> User
    merged_overrides = {**global_overrides, **user_overrides}
    normalized = _normalize_overrides(merged_overrides)
    
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
    """Ensure database has global defaults from .env if not present."""
    # Check if global settings exist in DB
    existing = await fetch_setting_overrides(SETTINGS_DEFAULT_USER)
    if not existing:
        # Seed from .env
        defaults = settings.model_dump()
        # Convert all to string for storage
        overrides = {k: str(v) for k, v in defaults.items()}
        await upsert_setting_overrides(SETTINGS_DEFAULT_USER, overrides)
        # Also refresh Redis for global user
        await refresh_runtime_settings(redis, SETTINGS_DEFAULT_USER)


async def refresh_runtime_settings(redis: Redis, user_id: str) -> RuntimeSettings:
    overrides = _normalize_overrides(
        await fetch_setting_overrides(user_id)
    )
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

    await upsert_setting_overrides(user_id, normalized)
    
    key = _settings_key_for_user(user_id)
    await redis.hset(key, mapping=normalized)
    await redis.expire(key, runtime_settings.redis_cache_ttl_seconds)
    
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

