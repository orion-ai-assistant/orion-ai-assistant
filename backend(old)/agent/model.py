from __future__ import annotations

"""
LLM model konfigürasyonu.
Model adı, temperature ve tool listesi ai_config.json'dan her çağrıda okunur —
admin paneli değişiklikleri restart gerekmeden yansır.
"""

from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

from config.settings.ai import (
    GOOGLE_API_KEY,
    ORION_LLM_NO_BIND_TOOLS,
    VERTEXAI_ENABLED,
    VERTEX_LOCATION,
    VERTEX_PROJECT,
)
from config.config_manager import get_config_manager
from .tools import tools

def _build_gemini(
    m_conf: Any,
    thinking: bool,
    *,
    api_key: str | None = None,
    use_vertexai: bool = False,
    project: str | None = None,
    location: str | None = None,
    streaming: bool = True,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": m_conf.name,
        "thinking_budget": -1 if thinking else 0,
        "include_thoughts": thinking,
        "temperature": m_conf.temperature,
        "top_p": m_conf.top_p,
        "top_k": m_conf.top_k,
        "max_tokens": m_conf.max_tokens,
        "streaming": streaming,
    }

    if use_vertexai:
        kwargs.update(
            {
                "vertexai": True,
                "project": project,
                "location": location,
            }
        )
        return ChatGoogleGenerativeAI(**kwargs)

    kwargs["api_key"] = api_key
    return ChatGoogleGenerativeAI(**kwargs)


# Aktif model adı: admin panelde default model değiştiğinde güncellenir.
ACTIVE_MODEL: str | None = None

# get_model() önbelleği: her ainvoke'da yeni ChatGoogleGenerativeAI yaratmak Vertex'te
# ek TLS/istemci maliyeti ekleyebilir; config değişince anahtar invalid olur.
_cached_model: Any | None = None
_cached_model_key: tuple[Any, ...] | None = None


def clear_model_cache() -> None:
    """Test veya config hot-reload sonrası zorunlu yenileme için."""
    global _cached_model, _cached_model_key
    _cached_model = None
    _cached_model_key = None


def _resolve_agent_tool_allowlist(config: Any, active_agent_name: str | None) -> set[str] | None:
    """Return allowed tool names for the selected agent.

    None => no per-agent restriction (use global enabled tools).
    Empty set => agent explicitly disables all tools.
    """
    agent_name = active_agent_name or getattr(config, "active_agent", None)
    if not agent_name:
        return None

    agents = getattr(config, "agents", None)
    if agents is None:
        return None

    get_fn = getattr(agents, "get", None)
    if not callable(get_fn):
        return None

    agent_profile = get_fn(agent_name)
    if agent_profile is None:
        return None

    declared = getattr(agent_profile, "tools", None)
    # Empty list means the agent cannot use any tool.
    if isinstance(declared, list) and len(declared) == 0:
        return set()
    # None keeps backward compatibility for older configs that never set tools.
    if declared is None:
        return None

    allowed: set[str] = set()
    for item in declared:
        if isinstance(item, str):
            if item:
                allowed.add(item)
            continue
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                allowed.add(name)
            continue
        name = getattr(item, "name", None)
        if isinstance(name, str) and name:
            allowed.add(name)

    return allowed


def _get_model_cache_key(config: Any, m_conf: Any, active_agent_name: str | None) -> tuple[Any, ...]:
    provider = getattr(config, "provider", None)
    use_vertexai = (provider == "vertexai") or (provider is None and bool(VERTEXAI_ENABLED))
    enabled_tool_names = tuple(sorted(t.name for t in get_active_tools(config, active_agent_name)))
    bind = bool(getattr(config, "enable_tool_binding", True)) and not ORION_LLM_NO_BIND_TOOLS
    return (
        getattr(config, "last_updated", None),
        active_agent_name or getattr(config, "active_agent", None),
        ACTIVE_MODEL,
        m_conf.name,
        m_conf.temperature,
        m_conf.top_p,
        m_conf.top_k,
        m_conf.max_tokens,
        bool(getattr(config, "thinking_enabled", False)),
        provider,
        use_vertexai,
        getattr(config, "vertex_project", None) or VERTEX_PROJECT,
        getattr(config, "vertex_location", None) or VERTEX_LOCATION,
        bool(getattr(config, "google_api_key", None) or GOOGLE_API_KEY),
        bind,
        enabled_tool_names if bind else (),
        bool(ORION_LLM_NO_BIND_TOOLS),
    )


def set_active_model(model_name: str | None) -> None:
    """Set active model name for subsequent model resolution."""
    global ACTIVE_MODEL
    ACTIVE_MODEL = model_name
    clear_model_cache()


def get_active_tools(config: Any | None = None, active_agent_name: str | None = None) -> list:
    """Config'e göre etkin tool nesnelerini döner."""
    cfg = config or get_config_manager().get_config()
    enabled_names = {t.name for t in getattr(cfg, "tools", []) if getattr(t, "enabled", False)}
    allowlist = _resolve_agent_tool_allowlist(cfg, active_agent_name)
    if allowlist is not None:
        enabled_names = enabled_names & allowlist
    return [t for t in tools if t.name in enabled_names]


def _resolve_model_params(config) -> Any:
    """Config'den aktif model ayarlarını çıkarır (ModelConf)."""
    if ACTIVE_MODEL:
        for m in config.models:
            if m.name != ACTIVE_MODEL:
                continue
            if not m.enabled:
                raise ValueError(f"'{m.name}' modeli devre dışı. Admin panelden etkinleştirin.")
            return m

    default_model = next((m for m in config.models if getattr(m, "is_default", False)), None)
    if default_model is not None:
        if not default_model.enabled:
            raise ValueError(f"'{default_model.name}' modeli devre dışı. Admin panelden etkinleştirin.")
        return default_model

    for m in config.models:
        if not m.enabled:
            continue
        return m
    raise ValueError("Etkin model bulunamadı.")


def _build_llm(m_conf: Any, thinking_enabled: bool, api_key: str | None = None) -> Any:
    """Aktif model için LLM istemcisini oluşturur (şu an Gemini)."""
    config = get_config_manager().get_config()
    provider = getattr(config, "provider", None)
    use_vertexai = (provider == "vertexai") or (provider is None and bool(VERTEXAI_ENABLED))

    if use_vertexai:
        project = getattr(config, "vertex_project", None) or VERTEX_PROJECT
        location = getattr(config, "vertex_location", None) or VERTEX_LOCATION

        if not project:
            raise RuntimeError(
                "VERTEXAI_ENABLED=true but no project configured. "
                "Set VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT."
            )
        if not location:
            raise RuntimeError(
                "VERTEXAI_ENABLED=true but no location configured. "
                "Set VERTEX_LOCATION or GOOGLE_CLOUD_LOCATION."
            )
        return _build_gemini(
            m_conf,
            thinking_enabled,
            use_vertexai=True,
            project=project,
            location=location,
            streaming=True,
        )

    return _build_gemini(
        m_conf,
        thinking_enabled,
        api_key=api_key,
        use_vertexai=False,
        streaming=True,
    )


def get_llm_diag_context(active_agent_name: str | None = None) -> dict[str, Any]:
    """Teşhis logları için: provider + tool sayısı (get_model ile aynı config)."""
    config = get_config_manager().get_config()
    provider = getattr(config, "provider", None)
    use_vertexai = (provider == "vertexai") or (provider is None and bool(VERTEXAI_ENABLED))
    label = "vertexai" if use_vertexai else "google_genai"
    n_tools = len(get_active_tools(config, active_agent_name))
    return {
        "provider": label,
        "n_tools": n_tools,
        "thinking_enabled": bool(getattr(config, "thinking_enabled", False)),
        "no_bind_tools": bool(ORION_LLM_NO_BIND_TOOLS),
        "enable_tool_binding": bool(getattr(config, "enable_tool_binding", True)),
    }


def get_model(active_agent_name: str | None = None):
    """
    Config'e göre LLM + isteğe bağlı bind_tools döner.
    Aynı config anahtarında istemci yeniden kullanılır (Vertex TTFT için önemli);
    last_updated / model / tool listesi değişince önbellek sıfırlanır.
    """
    global _cached_model, _cached_model_key

    config = get_config_manager().get_config()
    m_conf = _resolve_model_params(config)
    key = _get_model_cache_key(config, m_conf, active_agent_name)

    if _cached_model is not None and _cached_model_key == key:
        return _cached_model

    selected_api_key = getattr(config, 'google_api_key', None) or GOOGLE_API_KEY
    llm = _build_llm(m_conf, config.thinking_enabled, api_key=selected_api_key)
    active_tools = get_active_tools(config, active_agent_name)
    bind = bool(getattr(config, "enable_tool_binding", True)) and not ORION_LLM_NO_BIND_TOOLS

    if not bind or not active_tools:
        out = llm
    else:
        out = llm.bind_tools(active_tools)

    _cached_model_key = key
    _cached_model = out
    return out
