import os
import time

import aiohttp
from services.shared.environment import get_router_base_urls


class ModelNotFoundError(ValueError):
    pass


_catalog_cache: dict | None = None
_catalog_cached_at = 0.0
_CATALOG_TTL_SECONDS = 15.0


async def get_router_catalog(force_refresh: bool = False) -> dict:
    """Read the Router catalog using server credentials; expose names only."""
    global _catalog_cache, _catalog_cached_at
    if not force_refresh and _catalog_cache is not None:
        if time.monotonic() - _catalog_cached_at < _CATALOG_TTL_SECONDS:
            return _catalog_cache

    key = os.getenv("ROUTER_ADMIN_KEY") or os.getenv("ROUTER_API_KEY") or "orion"
    last_error = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        for base in get_router_base_urls():
            base = base.rstrip("/").removesuffix("/v1")
            try:
                async with session.get(base + "/dashboard/api/models", headers={"x-admin-key": key}) as response:
                    response.raise_for_status()
                    data = await response.json()
                async with session.get(base + "/dashboard/api/model-groups", headers={"x-admin-key": key}) as response:
                    response.raise_for_status()
                    groups = await response.json()
                async with session.get(base + "/dashboard/api/voices", headers={"x-admin-key": key}) as response:
                    response.raise_for_status()
                    voices = await response.json()
                async with session.get(base + "/dashboard/api/local-tts-info", headers={"x-admin-key": key}) as response:
                    response.raise_for_status()
                    local_tts = await response.json()
                models = [{"name": m["name"], "provider": m.get("provider"), "capability": m.get("capability")} for m in data["models"]
                          if m.get("is_active", True)]
                models.extend({"name": g["name"], "provider": None, "capability": g.get("capability", "chat")} for g in groups["groups"]
                              if g.get("capability", "chat") == "chat" and g.get("is_active", True))
                voice_catalog = voices.get("voices", {})
                local_voices = local_tts.get("voices", [])
                if local_voices:
                    voice_catalog["local"] = local_voices
                _catalog_cache = {"models": models, "voices": voice_catalog}
                _catalog_cached_at = time.monotonic()
                return _catalog_cache
            except (aiohttp.ClientError, TimeoutError, KeyError, TypeError) as exc:
                last_error = exc
    raise RuntimeError("Orion Router model listesi alınamadı. Router bağlantısını ve yönetim anahtarını kontrol edin.") from last_error


async def get_chat_models() -> list[dict]:
    catalog = await get_router_catalog()
    return [m for m in catalog["models"] if m["capability"] == "chat"]


async def get_model_provider(name: str, capability: str) -> str | None:
    catalog = await get_router_catalog()
    for model in catalog["models"]:
        if model["name"] == name and model["capability"] == capability:
            return model.get("provider")
    raise ModelNotFoundError(
        f"Model bulunamadı: {name}. Modeli Orion Router’a ekleyin ve etkinleştirin (Modeller sayfası)."
    )


async def require_chat_model(name: str) -> None:
    await get_model_provider(name, "chat")
