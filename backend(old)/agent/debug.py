"""
Debug yardımcıları.
Config debug_mode aktifken terminale renkli JSON basar.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config.config_manager import get_config_manager
from log import Logger

logger = Logger(__file__)

# CLI oturumu için geçici override (None = config değerini kullan)
_cli_debug_override: Optional[bool] = None

# CLI oturumu için düşünce (thoughts) gösterimi override (None = config değeri kullanılsın)
_cli_show_thoughts_override: Optional[bool] = None


def set_cli_debug_mode(value: Optional[bool]) -> None:
    """CLI oturumunda debug modunu geçici olarak ayarlar (config dosyasını değiştirmez)."""
    global _cli_debug_override
    _cli_debug_override = value


def set_runtime_debug_mode(value: Optional[bool]) -> None:
    """Set runtime debug mode override for non-CLI entry points as well."""
    set_cli_debug_mode(value)


def is_debug_mode() -> bool:
    """Aktif debug modunu döner: CLI override varsa onu, yoksa config değerini kullanır."""
    if _cli_debug_override is not None:
        return _cli_debug_override
    try:
        cfg = get_config_manager().get_config()
        if hasattr(cfg, "log_settings") and hasattr(cfg.log_settings, "levels"):
            levels = cfg.log_settings.levels
            if hasattr(levels, "as_dict"):
                return bool(levels.as_dict().get("DEBUG", False))
            if isinstance(levels, dict):
                return bool(levels.get("DEBUG", False))
    except Exception:
        pass
    return False


def set_cli_show_thoughts_mode(value: Optional[bool]) -> None:
    """CLI oturumunda modelden düşünceler (chain-of-thought) istemeyi açıklar/kapatır."""
    global _cli_show_thoughts_override
    _cli_show_thoughts_override = value


def is_show_thoughts_mode() -> bool:
    """Aktif thoughts modunu döner: CLI override varsa onu, yoksa config değeri kullanır."""
    if _cli_show_thoughts_override is not None:
        return _cli_show_thoughts_override
    try:
        cfg = get_config_manager().get_config()
        return bool(getattr(cfg, "thinking_enabled", False))
    except Exception:
        return False


def debug_json(label: str, obj) -> None:
    """Debug modu açıksa objeyi renkli JSON olarak terminale basar."""
    if not is_debug_mode():
        return
    try:
        if isinstance(obj, list):
            data = [item.model_dump() if hasattr(item, "model_dump") else repr(item) for item in obj]
        else:
            data = obj.model_dump() if hasattr(obj, "model_dump") else repr(obj)
    except Exception:
        data = repr(obj)
    logger.debug(lambda: f"\033[90m{'-' * 60}\n"
        f"  [DEBUG] {label}\n"
        f"{json.dumps(data, indent=2, ensure_ascii=False)}\n"
        f"{'-' * 60}\033[0m"
    )


def _to_debug_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _to_debug_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _to_debug_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_debug_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def debug_block(label: str, obj: Any, color_code: str = "90") -> None:
    """Debug modu açıksa tek tip renkli blok formatında payload/response basar."""
    if not is_debug_mode():
        return

    lines = [f"\033[{color_code}m" + ("-" * 60), f"  {label}"]
    try:
        lines.append(json.dumps(_to_debug_jsonable(obj), ensure_ascii=False, indent=2))
    except Exception as exc:
        lines.append(f"[JSON dump error] {exc}")
    lines.append(("-" * 60) + "\033[0m")
    logger.debug("\n".join(lines))


def debug_provider_payload(payload: Any) -> None:
    debug_block("[PAYLOAD] LLM'e gonderilen gercek model backend payload", payload, color_code="95")


def debug_provider_response(response: Any) -> None:
    debug_block("[RESPONSE] LLM'den gelen AIMessage", response, color_code="92")


def format_agent_log(think: str, reply: str) -> str:
    """Build a debug-friendly think/reply block layout."""
    if think:
        return f"think:\n{think}\n\nOrion:\n{reply}"
    return f"Orion:\n{reply}"


