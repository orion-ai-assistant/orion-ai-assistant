import asyncio
import os
import time

import aiohttp
from services.shared.environment import get_router_base_urls


class ModelNotFoundError(ValueError):
    pass


_catalog_cache: dict | None = None
_catalog_cached_at = 0.0
_CATALOG_TTL_SECONDS = 15.0
_catalog_sections: dict[str, dict] = {}
_SECTION_TIMEOUT_SECONDS = 1.0
_LOCAL_TTS_TIMEOUT_SECONDS = 0.6


async def _fetch_section(session, bases: list[str], name: str, key: str) -> dict | None:
    async def fetch():
        for base in bases:
            url = base.rstrip('/').removesuffix('/v1') + '/dashboard/api/' + name
            try:
                async with session.get(url, headers={'x-admin-key': key}) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if not isinstance(data, dict) or data.get('error'):
                        return None
                    field = {'models': 'models', 'model-groups': 'groups', 'voices': 'voices', 'local-tts-info': 'voices'}[name]
                    expected = dict if name == 'voices' else list
                    if name == 'local-tts-info' and not data.get('voices'):
                        return None
                    return data if isinstance(data.get(field), expected) else None
            except (aiohttp.ClientError, ValueError):
                # Optional local TTS is checked once per refresh, without retries.
                if name == 'local-tts-info':
                    return None
        return None

    timeout = _LOCAL_TTS_TIMEOUT_SECONDS if name == 'local-tts-info' else _SECTION_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(fetch(), timeout=timeout)
    except TimeoutError:
        return None


async def get_router_catalog(force_refresh: bool = False) -> dict:
    """Read the Router catalog using server credentials; expose names only."""
    global _catalog_cache, _catalog_cached_at
    if not force_refresh and _catalog_cache is not None:
        if time.monotonic() - _catalog_cached_at < _CATALOG_TTL_SECONDS:
            return _catalog_cache

    key = os.getenv("ROUTER_ADMIN_KEY") or os.getenv("ROUTER_API_KEY") or "orion"
    names = ['models', 'model-groups', 'voices', 'local-tts-info']
    bases = get_router_base_urls()
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(_fetch_section(session, bases, name, key) for name in names))
    unavailable = []
    for name, result in zip(names, results):
        if result is None:
            unavailable.append(name)
        else:
            _catalog_sections[name] = result
    if not _catalog_sections:
        raise RuntimeError('Model listelerine şu an erişilemiyor. Sonraki kontrolde tekrar denenecek.')
    data = _catalog_sections.get('models', {})
    groups = _catalog_sections.get('model-groups', {})
    models = [{"name": m["name"], "provider": m.get("provider"), "capability": m.get("capability")}
              for m in data.get('models', []) if m.get('is_active', True)]
    models.extend({"name": g["name"], "provider": None, "capability": g.get("capability", "chat")}
                  for g in groups.get('groups', []) if g.get('capability', 'chat') == 'chat' and g.get('is_active', True))
    voices = dict(_catalog_sections.get('voices', {}).get('voices', {}))
    local_voices = _catalog_sections.get('local-tts-info', {}).get('voices', [])
    if local_voices:
        voices['local'] = local_voices
    _catalog_cache = {'models': models, 'voices': voices, 'unavailable': unavailable}
    _catalog_cached_at = time.monotonic()
    return _catalog_cache


async def get_model_provider(name: str, capability: str) -> str | None:
    catalog = await get_router_catalog()
    for model in catalog["models"]:
        if model["name"] == name and model["capability"] == capability:
            return model.get("provider")
    raise ModelNotFoundError(
        f"Model bulunamadı: {name}. Modeli Orion Router’a ekleyin ve etkinleştirin (Modeller sayfası)."
    )
async def validate_model_updates(updates: dict[str, str]) -> None:
    """Validate model settings when they change, outside the message hot path."""
    model_capabilities = {
        "router_model_group": "chat",
        "chat_title_model": "chat",
        "tts_model": "tts",
        "stt_model": "stt",
    }
    for key, value in updates.items():
        capability = model_capabilities.get(key.lower())
        model_name = str(value).strip()
        if capability and model_name:
            await get_model_provider(model_name, capability)
