from typing import Optional

from config.schemas import AIConfig, AgentProfile, DEFAULTS, ConfigKeys, ModelConfig
from log import Logger

# Daha kısa kullanım için `component=` belirtmeye gerek yok; Logger() zaten ilk argümanı component olarak alıyor.
logger = Logger(__file__)

class ConfigDefaultsMixin:
    """Provides methods for resolving default settings for ConfigManager."""

    def _get_default_config(self) -> AIConfig:
        """Get default configuration using central schema and definitions."""
        try:
            from config.schemas import MODELS
            cfg = AIConfig(models=[ModelConfig(**m) for m in MODELS])
        except Exception as e:
            logger.error(lambda: f"Başlangıç ayarları yükleme hatası: {e}")
            cfg = AIConfig()

        # Default agent (single-profile fallback)
        if not cfg.agents:
            cfg.agents = {
                "chat": AgentProfile(
                    display_name="Chat Agent",
                    description="Genel sohbet için.",
                    system_prompt=DEFAULTS[ConfigKeys.SYSTEM_PROMPT],
                    model=next((m.name for m in cfg.models if getattr(m, "is_default", False)), None), # type: ignore
                    tools=None,
                    graph="default",
                    default=True,
                )
            }
            cfg.active_agent = "chat"

        self._ensure_active_agent(cfg)  # type: ignore
        return cfg

    def _ensure_active_agent(self, cfg: AIConfig) -> None:
        """Ensure there's always a valid active_agent set."""
        if cfg.active_agent and cfg.active_agent in cfg.agents:
            return
        # fallback to explicit default
        for k, v in cfg.agents.items():
            if getattr(v, "default", False):
                cfg.active_agent = k
                return
        # fallback to first agent
        cfg.active_agent = next(iter(cfg.agents), None)

    def reset_to_default(self, admin_name: Optional[str] = None) -> AIConfig:
        """Reset to default configuration."""
        self._config = self._get_default_config()  # type: ignore
        self._config.updated_by = admin_name or "system"
        self._save_to_file()  # type: ignore
        logger.info("Config reset to default")
        return self._config
