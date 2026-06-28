from datetime import datetime
from typing import Optional

from config.schemas import AIConfig, ConfigUpdateRequest, AgentProfile, ModelConfig, ToolConfig
from log.settings_models import LogSettings
from log import Logger

logger = Logger(__file__)

class ConfigOperationsMixin:
    """Provides mutators and updates handling for the AIConfig schema over ConfigManager."""

    def update_config(self, update: ConfigUpdateRequest) -> AIConfig:
        """Update configuration."""
        with self._lock:
            update_data = update.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                if value is None:
                    continue

                if key == "agents":
                    parsed = {}
                    for agent_name, agent_value in (value or {}).items():
                        if isinstance(agent_value, dict):
                            parsed[agent_name] = AgentProfile(**agent_value)
                        else:
                            parsed[agent_name] = agent_value
                    self._config.agents = parsed # type: ignore

                elif key == "models":
                    parsed = []
                    for model_value in (value or []):
                        if isinstance(model_value, dict):
                            parsed.append(ModelConfig(**model_value))
                        else:
                            parsed.append(model_value)
                    self._config.models = parsed # type: ignore

                elif key == "tools":
                    parsed = []
                    for tool_value in (value or []):
                        if isinstance(tool_value, dict):
                            parsed.append(ToolConfig(**tool_value))
                        else:
                            parsed.append(tool_value)
                    self._config.tools = parsed # type: ignore

                elif key == "log_settings":
                    if isinstance(value, dict):
                        self._config.log_settings = LogSettings(**value) # type: ignore
                    else:
                        self._config.log_settings = value # type: ignore

                else:
                    setattr(self._config, key, value) # type: ignore

            # Ensure active_agent always points to a valid profile.
            self._ensure_active_agent(self._config) # type: ignore
            self._sync_log_components(persist=False) # type: ignore

            self._config.last_updated = datetime.now() # type: ignore
            self._save_to_file() # type: ignore
            try:
                self._refresh_agent_graph_cache() # type: ignore
            except Exception:
                pass

            self._log(f"Config updated by {self._config.updated_by}") # type: ignore
            return self._config # type: ignore

    def toggle_thinking(self, enabled: bool, admin_name: Optional[str] = None) -> AIConfig:
        """Toggle thinking mode."""
        with self._lock:
            if self._config.thinking_enabled == enabled: # type: ignore
                self._log(f"Thinking mode already {enabled} (no change) by {admin_name}") # type: ignore
                return self._config # type: ignore

            self._config.thinking_enabled = enabled # type: ignore
            self._config.updated_by = admin_name # type: ignore
            self._config.last_updated = datetime.now() # type: ignore
            self._save_to_file() # type: ignore

            self._log(f"Thinking mode set to {enabled} by {admin_name}") # type: ignore
            return self._config # type: ignore

    def set_default_model(self, model_name: str, admin_name: Optional[str] = None) -> AIConfig:
        """Set default model and update ACTIVE_MODEL in agent."""
        with self._lock:
            for model in self._config.models: # type: ignore
                model.is_default = False

            found = False
            matching_model = None
            for model in self._config.models: # type: ignore
                if model.name == model_name:
                    model.is_default = True
                    found = True
                    matching_model = model
                    break

            if not found:
                raise ValueError(f"Model '{model_name}' not found")

            if self._config.active_agent is not None: # type: ignore
                # if the currently set default model is already this model, no-op
                current_default = next((m for m in self._config.models if m.is_default), None) # type: ignore
                if current_default and current_default.name == model_name:
                    self._log(f"Default model already '{model_name}' (no change) by {admin_name}") # type: ignore
                    return self._config # type: ignore

            self._config.updated_by = admin_name # type: ignore
            self._config.last_updated = datetime.now() # type: ignore
            self._save_to_file() # type: ignore

            # Update active model in agent runtime so next requests use the new default model
            try:
                from agent.model import set_active_model

                if matching_model is not None:
                    set_active_model(model_name)
                    logger.info(lambda: f"ACTIVE_MODEL -> {model_name}")
            except Exception as e:
                logger.error(lambda: f"Could not update ACTIVE_MODEL: {e}")

            logger.info(lambda: f"Default model: {model_name}")
            return self._config # type: ignore

    def toggle_model(self, model_name: str, enabled: bool, admin_name: Optional[str] = None) -> AIConfig:
        """Toggle model enabled/disabled."""
        with self._lock:
            found = False
            for model in self._config.models: # type: ignore
                current_model_name = getattr(model, "name", None)
                current_model_enabled = getattr(model, "enabled", None)

                if current_model_name == model_name:
                    if current_model_enabled == enabled:
                        self._log(f"Model '{model_name}' unchanged (already {enabled}) by {admin_name}") # type: ignore
                        return self._config # type: ignore

                    setattr(model, "enabled", enabled)
                    found = True
                    break

            if not found:
                raise ValueError(f"Model '{model_name}' not found")

            self._config.updated_by = admin_name # type: ignore
            self._config.last_updated = datetime.now() # type: ignore
            self._save_to_file() # type: ignore
            self._log(f"Model '{model_name}' {'enabled' if enabled else 'disabled'} by {admin_name}") # type: ignore
            return self._config # type: ignore

    def toggle_tool(self, tool_name: str, enabled: bool, admin_name: Optional[str] = None) -> AIConfig:
        """Toggle tool enabled/disabled."""
        with self._lock:
            found = False
            for tool in self._config.tools: # type: ignore
                current_tool_name = getattr(tool, "name", None)
                current_tool_enabled = getattr(tool, "enabled", None)

                if current_tool_name == tool_name:
                    if current_tool_enabled == enabled:
                        self._log(f"Tool '{tool_name}' unchanged (already {enabled}) by {admin_name}") # type: ignore
                        return self._config # type: ignore

                    setattr(tool, "enabled", enabled)
                    found = True
                    break

            if not found:
                raise ValueError(f"Tool '{tool_name}' not found")

            self._config.updated_by = admin_name # type: ignore
            self._config.last_updated = datetime.now() # type: ignore
            self._save_to_file() # type: ignore
            try:
                self._refresh_agent_graph_cache() # type: ignore
            except Exception:
                pass
            self._log(f"Tool '{tool_name}' {'enabled' if enabled else 'disabled'} by {admin_name}") # type: ignore
            return self._config # type: ignore
