"""Configuration management.

This module holds the singleton ConfigManager used across the system.
It provides an in-memory config cache backed by a JSON file (CONFIG_FILE).
"""

import hashlib
import json
import threading
import atexit
from pathlib import Path
from typing import Optional

from config.schemas import AIConfig
from config.settings.core import CONFIG_FILE

from log import Logger
from common.design_patterns.singleton import SingletonMeta
from .manager.defaults import ConfigDefaultsMixin
from .manager.sync import ConfigSyncMixin
from .manager.operations import ConfigOperationsMixin

logger = Logger(__file__)

class ConfigManager(ConfigDefaultsMixin, ConfigSyncMixin, ConfigOperationsMixin, metaclass=SingletonMeta):
    """Singleton config manager.

    In-memory state + JSON persistence. Uses mixins for operations, sync, and defaults
    to prevent file bloat.
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Only initialize once
        if ConfigManager._initialized:
            return
        ConfigManager._initialized = True

        self._config: Optional[AIConfig] = None
        self._config_file: Optional[Path] = None
        self._config_file_mtime_ns: Optional[int] = None
        self._config_file_hash: Optional[str] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_stop_event = threading.Event()
        self._lock = threading.RLock()

    def _log(self, message: str):
        """Unified config debug logging (production-safe)."""
        logger.info(message)

    def _refresh_agent_graph_cache(self) -> None:
        """Clear and warm graph cache so config changes apply without user wait."""
        try:
            import sys

            if "agent.graph" in sys.modules:
                graph_module = sys.modules["agent.graph"]
                if hasattr(graph_module, "warm_graph_cache"):
                    graph_module.warm_graph_cache()
                    return

            # Deferred import to avoid circular dependency at startup.
            from importlib import import_module

            graph_module = import_module("agent.graph")
            warm_graph_cache = getattr(graph_module, "warm_graph_cache", None)
            if callable(warm_graph_cache):
                warm_graph_cache()
        except Exception as exc:
            logger.warning(lambda: f"Graph cache refresh failed: {exc}")

    def initialize(self, config_file: Optional[str] = None):
        """Initialize the config manager with a file path."""
        if config_file:
            self._config_file = Path(config_file)

        if self._config is None:
            if self._config_file:
                self._load_from_file()
            else:
                self._config = self._get_default_config()

        if self._config_file:
            self._ensure_background_watcher_started()

    def _background_watch_loop(self) -> None:
        """Watch config file in the background to preload runtime changes."""
        while not self._watch_stop_event.wait(0.3):
            try:
                with self._lock:
                    self._reload_if_config_file_changed()
            except Exception:
                # Keep watcher alive even if transient read/parsing issues occur.
                pass

    def _stop_background_watcher(self) -> None:
        self._watch_stop_event.set()

    def _ensure_background_watcher_started(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return

        self._watch_stop_event.clear()
        self._watch_thread = threading.Thread(
            target=self._background_watch_loop,
            name="config-watch",
            daemon=True,
        )
        self._watch_thread.start()
        atexit.register(self._stop_background_watcher)

    def _ensure_config_integrity(self):
        """Ensure config has required fields to avoid data loss on save."""
        if self._config is None:
            return

        if getattr(self._config, "log_settings", None) is None:
            from log.settings_models import LogSettings
            self._config.log_settings = LogSettings() # type: ignore

        if getattr(self._config, "agents", None) is None:
            self._config.agents = {}
            self._ensure_active_agent(self._config)

        if getattr(self._config, "models", None) is None:
            self._config.models = []

        if getattr(self._config, "tools", None) is None:
            self._config.tools = []

    def _flatten_config(self, data, prefix=""):
        changes = {}
        if isinstance(data, dict):
            for k, v in data.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    changes.update(self._flatten_config(v, prefix=full_key))
                else:
                    changes[full_key] = v
        elif isinstance(data, list):
            changes[prefix] = data
        else:
            changes[prefix] = data
        return changes

    def _diff_config(self, old_config: AIConfig, new_config: AIConfig):
        old_dict = old_config.model_dump() if old_config else {}
        new_dict = new_config.model_dump() if new_config else {}
        old_flat = self._flatten_config(old_dict)
        new_flat = self._flatten_config(new_dict)

        keys = set(old_flat.keys()) | set(new_flat.keys())
        diffs = []
        for key in sorted(keys):
            old_val = old_flat.get(key, None)
            new_val = new_flat.get(key, None)
            if old_val != new_val:
                diffs.append(key)
        return diffs

    def _load_from_file(self, sync_persist: bool = True, log_no_effective_changes: bool = True):
        """Load configuration from file if it exists."""
        if self._config_file and self._config_file.exists():
            try:
                old_config = self._config
                with open(self._config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = AIConfig(**data)

                if old_config is not None:
                    try:
                        from agent.model import clear_model_cache

                        clear_model_cache()
                    except Exception:
                        pass

                # Backward-compatible guard: if persisted config has no agents,
                # bootstrap a default profile so runtime and admin panel stay usable.
                if not self._config.agents:
                    default_cfg = self._get_default_config()
                    self._config.agents = default_cfg.agents
                    self._config.active_agent = default_cfg.active_agent
                    if sync_persist:
                        self._save_to_file()
                else:
                    previous_active = self._config.active_agent
                    self._ensure_active_agent(self._config)
                    if sync_persist and self._config.active_agent != previous_active:
                        self._save_to_file()

                self._ensure_config_integrity()
                self._sync_log_components(persist=sync_persist)
                self._refresh_agent_graph_cache()
                self._update_config_file_mtime()
                if old_config is not None:
                    diff_keys = self._diff_config(old_config, self._config)
                    if diff_keys:
                        logger.info(lambda: f"Loaded config from {self._config_file}; changed keys: {', '.join(diff_keys)}")
                    elif log_no_effective_changes:
                        logger.info(lambda: f"Loaded config from {self._config_file}; no effective changes")
                else:
                    logger.info(lambda: f"Loaded config from {self._config_file}")
            except Exception as e:
                logger.error(lambda: f"Error loading config: {e}. Using default.")
                self._config = self._get_default_config()
                self._ensure_config_integrity()
                self._sync_log_components(persist=False)
                if sync_persist:
                    self._save_to_file()
        else:
            self._config = self._get_default_config()
            self._ensure_config_integrity()
            self._sync_log_components(persist=False)
            self._refresh_agent_graph_cache()
            if sync_persist:
                self._save_to_file()

    def _save_to_file(self):
        """Save current config to file."""
        if self._config_file:
            try:
                self._config_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._config_file, "w", encoding="utf-8") as f:
                    json.dump(self._config.model_dump(), f, indent=2, ensure_ascii=False, default=str)
                self._update_config_file_mtime()
                logger.info(lambda: f"Saved config to {self._config_file}")
            except Exception as e:
                logger.error(lambda: f"Error saving config: {e}")

    def _compute_file_hash(self, path: Path) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                data = f.read()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return None

    def _update_config_file_mtime(self) -> None:
        if not self._config_file:
            return
        try:
            if self._config_file.exists():
                self._config_file_mtime_ns = self._config_file.stat().st_mtime_ns
                self._config_file_hash = self._compute_file_hash(self._config_file)
        except Exception:
            pass

    def _reload_if_config_file_changed(self) -> None:
        if not self._config_file or not self._config_file.exists():
            return

        try:
            current_mtime_ns = self._config_file.stat().st_mtime_ns
        except Exception:
            return

        if self._config_file_mtime_ns is None:
            self._config_file_mtime_ns = current_mtime_ns
            return

        if current_mtime_ns != self._config_file_mtime_ns:
            # mtime değişti ama içerik değişmedi ise reload yapma,
            # Docker bind/timeout/parite-yansımalarında sık görülen fasıladan kaçın.
            new_hash = self._compute_file_hash(self._config_file)
            if self._config_file_hash and new_hash and new_hash == self._config_file_hash:
                self._config_file_mtime_ns = current_mtime_ns
                return

            # Aynı dosya değişimini bir kez işle: önce gözlenen işaretleri güncelle.
            self._config_file_mtime_ns = current_mtime_ns
            self._config_file_hash = new_hash
            logger.info(lambda: f"Detected config file change, reloading: {self._config_file}")
            # Runtime refresh: no implicit writes, yoksa mtime değişerek sonsuz döngü yaratır.
            self._load_from_file(sync_persist=False, log_no_effective_changes=False)

    def get_config(self) -> AIConfig:
        """Get current configuration."""
        with self._lock:
            if self._config is None:
                self._config = self._get_default_config()
            self._ensure_config_integrity()
            self._sync_log_components(persist=False)
            return self._config

    def refresh_system_sync(self, persist: bool = True) -> AIConfig:
        """Explicitly sync runtime-derived settings (tools/log components)."""
        with self._lock:
            if self._config is None:
                self._config = self._get_default_config()

            self._ensure_config_integrity()
            before = self._config.model_dump()

            self._config = self._merge_agent_tools(self._config)
            self._sync_log_components(persist=False)
            self._refresh_agent_graph_cache()

            after = self._config.model_dump()
            if persist and before != after:
                self._save_to_file()

            return self._config

    def get_active_agent_profile(self):
        """Return the currently active agent profile (if any)."""
        cfg = self.get_config()
        agent_name = cfg.active_agent or (next(iter(cfg.agents), None) if cfg.agents else None)
        if not agent_name:
            return None
        return cfg.agents.get(agent_name)

    def get_agent_profile(self, agent_name: str | None = None):
        """Get a specific agent profile (or active agent if None)."""
        cfg = self.get_config()
        if not agent_name:
            return self.get_active_agent_profile()
        return cfg.agents.get(agent_name)


def get_config_manager() -> ConfigManager:
    """Get the singleton config manager."""
    manager = ConfigManager()
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")

    manager.initialize(CONFIG_FILE)
    return manager
