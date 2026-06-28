from __future__ import annotations

import os
from pathlib import Path

_registered_components: set[str] = set()


def normalize_component(component: str) -> str:
    raw = str(component or "").strip()
    if not raw:
        return "general"

    path_like = any(sep in raw for sep in (os.sep, "\\", "/")) or raw.lower().endswith(".py")

    if path_like:
        try:
            path_value = Path(raw)
            if path_value.suffix.lower() == ".py":
                path_value = path_value.with_suffix("")

            workspace_dir = Path.cwd().resolve()
            resolved = path_value.resolve() if path_value.exists() else path_value

            try:
                rel = resolved.relative_to(workspace_dir)
            except Exception:
                rel = resolved

            parts = [str(part).lower() for part in rel.parts if part not in (".", "..", "")]
            workspace_name = workspace_dir.name.lower()
            if workspace_name in parts:
                workspace_idx = parts.index(workspace_name)
                parts = parts[workspace_idx + 1 :]

            if parts and (parts[0].endswith(":\\") or parts[0].endswith(":")):
                parts = parts[1:]

            normalized = "/".join(parts) if parts else path_value.name.lower()
        except Exception:
            normalized = raw
    else:
        normalized = raw.lower()

    if path_like:
        normalized = normalized.replace("\\", "/").strip(" /").lower()
    else:
        normalized = normalized.strip(" _").lower()

    return normalized or "general"


def _register_component(component: str) -> None:
    normalized = normalize_component(component)
    if normalized == "all":
        return

    if normalized in _registered_components:
        return

    _registered_components.add(normalized)

    # Sync to persisted config when a new component is first registered.
    try:
        from config.config_manager import get_config_manager

        cm = get_config_manager()
        cm._sync_log_components(persist=True)
    except Exception:
        pass


def register_component(component: str) -> None:
    """Public API: register a component name in the runtime registry."""
    _register_component(component)


def get_registered_components() -> set[str]:
    return set(_registered_components)


def get_known_components() -> set[str]:
    """Backward-compatible alias: runtime-registered component names."""
    return get_registered_components()
