from config.schemas import AIConfig, ToolConfig
from log.settings_models import LogComponents, LogLevels, LogSettings
# Fix circular dependency: partial import from log.logger only.
# Runtime imports for registry/settings are handled inside methods.
from log.logger import Logger

logger = Logger(__file__)

class ConfigSyncMixin:
    """Provides mapping and dynamic synchronization logical components for ConfigManager."""

    def _merge_agent_tools(self, cfg: AIConfig) -> AIConfig:
        """Ensure config.tools reflects the current set of available tools."""
        try:
            from agent.tools import tools as agent_tools
        except Exception as e:
            # Agent package may not be importable in API-only container images.
            # In that case, keep persisted tool config as-is.
            logger.warning(lambda: f"Agent tools import failed: {e}")
            return cfg

        # Existing config tool mapping (name -> enabled)
        existing: dict[str, bool] = {
            getattr(t, "name", "unknown"): getattr(t, "enabled", True)
            for t in cfg.tools
        }

        merged_tools: list[ToolConfig] = []
        for t in agent_tools:
            t_name = getattr(t, "name", "unknown")
            t_desc = getattr(t, "description", "").split("\n")[0].strip() or f"{t_name} tool"

            # Preserve enabled flag from existing config when possible
            enabled = existing.get(t_name, True)

            merged_tools.append(ToolConfig(name=t_name, description=t_desc, enabled=enabled))

        cfg.tools = merged_tools
        return cfg

    def _sync_log_components(self, persist: bool = False) -> None:
        """Dynamically fetch instantiated loggers and update state."""
        # Local imports to avoid circular dependency during initialization
        from log.registry import get_registered_components
        from log.settings import get_log_levels

        if self._config is None:  # type: ignore
            return

        if self._config.log_settings is None: # type: ignore
            self._config.log_settings = LogSettings() # type: ignore

        components_source = self._config.log_settings.components
        if hasattr(components_source, "as_dict"):
            current_components = {k.lower(): bool(v) for k, v in components_source.as_dict().items()}
        elif isinstance(components_source, dict):
            current_components = {k.lower(): bool(v) for k, v in components_source.items()}
        else:
            current_components = {}

        normalized_current_components: dict[str, bool] = {}
        for k, v in current_components.items():
            if k == "all":
                continue
            # Geçersiz/yanlış yakalanmış component adlarını (örn: "...") temizle.
            # Slashes and hyphens are valid for component names like routes/http_routes
            safe_k = k.replace("_", "").replace("/", "").replace("-", "")
            if not safe_k or not safe_k.isalnum():
                continue
            normalized_current_components[k] = v

        registered = get_registered_components()
        known_component_names = set(registered).union(normalized_current_components.keys())
        synced_components: dict[str, bool] = {}
        for component in sorted(known_component_names):
            synced_components[component] = bool(current_components.get(component, True))

        # Ensure each LogLevel exists in config settings (backwards compatibility)
        levels_source = self._config.log_settings.levels
        if hasattr(levels_source, "as_dict"):
            levels = levels_source.as_dict()
        elif isinstance(levels_source, dict):
            levels = dict(levels_source)
        else:
            levels = {}

        for level in get_log_levels():
            if level not in levels:
                levels[level] = True

        self._config.log_settings.levels = LogLevels(**levels) # type: ignore

        if synced_components != normalized_current_components:
            self._config.log_settings.components = LogComponents(**synced_components) # type: ignore
            if persist:
                self._save_to_file() # type: ignore

        # Push to the decoupled log module
        from log.settings import configure_system_log
        configure_system_log(
            enabled=self._config.log_settings.enabled, # type: ignore
            levels=self._config.log_settings.levels, # type: ignore
            components=self._config.log_settings.components # type: ignore
        )
