"""Shared schema definitions for AI configuration.

These models are used across the API, admin panel, and agent.
Keeping them here avoids cross-package imports and keeps the config schema
normalized in one place.
"""

from datetime import datetime
from typing import List, Optional, Literal

from log.settings_models import LogSettings
from pydantic import BaseModel, Field


class ConfigKeys:
    """Config keys to avoid magic strings throughout the app."""
    THINKING_ENABLED = "thinking_enabled"
    SYSTEM_PROMPT = "system_prompt"
    MAX_CONVERSATION_HISTORY = "max_conversation_history"
    RATE_LIMIT_PER_USER = "rate_limit_per_user"
    UPDATED_BY = "updated_by"

    # Model specific keys
    MODEL_NAME = "name"
    MODEL_THINKING_CAPABLE = "thinking_capable"
    MODEL_ENABLED = "enabled"
    MODEL_MAX_TOKENS = "max_tokens"
    MODEL_TEMPERATURE = "temperature"
    MODEL_TOP_P = "top_p"
    MODEL_TOP_K = "top_k"
    MODEL_IS_DEFAULT = "is_default"

    # Tool specific keys
    TOOL_NAME = "name"
    TOOL_DESCRIPTION = "description"
    TOOL_ENABLED = "enabled"

MODELS = [
    {
        ConfigKeys.MODEL_NAME: "gemini-2.5-flash",
        ConfigKeys.MODEL_ENABLED: True,
        ConfigKeys.MODEL_THINKING_CAPABLE: True,
        ConfigKeys.MODEL_IS_DEFAULT: True,
    },
]


# Genel uygulama varsayılanları — ai_config.json içinde bu alanlar yoksa
# code-side defaults olarak buradan doldurulur.
DEFAULTS = {
    ConfigKeys.THINKING_ENABLED: False,
    ConfigKeys.SYSTEM_PROMPT: "Kullanıcı ne söylerse yardımcı olmalısın.",
    ConfigKeys.MAX_CONVERSATION_HISTORY: 20,
    ConfigKeys.RATE_LIMIT_PER_USER: 100,
    ConfigKeys.UPDATED_BY: "system",
}

MODEL_DEFAULTS = {
    ConfigKeys.MODEL_TEMPERATURE: 1,
    ConfigKeys.MODEL_MAX_TOKENS: 4096,
    ConfigKeys.MODEL_TOP_P: 0.95,
    ConfigKeys.MODEL_TOP_K: 40,
    ConfigKeys.MODEL_THINKING_CAPABLE: False,
}


class ModelConfig(BaseModel):
    """Bireysel model konfigürasyonu."""
    name: str = Field(..., description="Model markası/adı (örn: gemini-2.5-flash)")
    enabled: bool = Field(default=True, description="Modelin aktif olup olmadığı")
    max_tokens: Optional[int] = Field(
        default=MODEL_DEFAULTS[ConfigKeys.MODEL_MAX_TOKENS],
        description="Maksimum token sayısı",
    )
    temperature: float = Field(
        default=MODEL_DEFAULTS[ConfigKeys.MODEL_TEMPERATURE],
        ge=0.0,
        le=2.0,
        description="Sıcaklık ayarı",
    )
    top_p: float = Field(
        default=MODEL_DEFAULTS[ConfigKeys.MODEL_TOP_P],
        ge=0.0,
        le=1.0,
        description="Top P ayarı",
    )
    top_k: int = Field(
        default=MODEL_DEFAULTS[ConfigKeys.MODEL_TOP_K],
        ge=0,
        description="Top K ayarı",
    )
    is_default: bool = Field(default=False, description="Varsayılan model olup olmadığı")
    thinking_capable: bool = Field(
        default=MODEL_DEFAULTS[ConfigKeys.MODEL_THINKING_CAPABLE],
        description="Thinking yeteneği var mı?",
    )


class ToolConfig(BaseModel):
    """Bireysel araç konfigürasyonu."""
    name: str = Field(..., description="Araç adı")
    description: str = Field(default="", description="Araç açıklaması")
    enabled: bool = Field(default=True, description="Araç aktif mi?")


class AgentProfile(BaseModel):
    """Ajan profili: hangi prompt, model, tool ve graph kullanılacak."""
    display_name: Optional[str] = Field(default=None, description="Agent gösterim adı")
    description: Optional[str] = Field(default=None, description="Agent açıklaması")
    system_prompt: Optional[str] = Field(default=None, description="Agent system prompt")
    model: Optional[str] = Field(default=None, description="Kullanılacak model adı")
    tools: Optional[List[str]] = Field(
        default=None,
        description="Bu ajan için kullanılacak tool isimleri",
    )
    graph: Optional[str] = Field(default=None, description="Kullanılacak graph/flow")
    default: Optional[bool] = Field(default=False, description="Varsayılan ajan mı?")


# LogSettings is defined in log.settings_models to avoid circular dependency.
# It is imported above as LogSettings.


class AIConfig(BaseModel):
    """Tüm AI sistem konfigürasyonu."""
    provider: Optional[Literal["google_genai", "vertexai"]] = Field(
        default=None,
        description="LLM sağlayıcı seçimi. None ise env/varsayılan davranış kullanılır.",
    )
    google_api_key: Optional[str] = Field(
        default=None,
        description="Google API key for Google GenAI provider (vent ile statik alternatif).",
    )
    vertex_project: Optional[str] = Field(
        default=None,
        description="Vertex AI project id (provider=vertexai için).",
    )
    vertex_location: Optional[str] = Field(
        default=None,
        description="Vertex AI location/region (provider=vertexai için). Örn: europe-west1",
    )
    enable_tool_binding: bool = Field(
        default=True,
        description=(
            "False ise LLM'e tool şeması bağlanmaz (TTFT düşer; arama/araçlar devre dışı). "
            "Çok sayıda tool açıkken gecikme artar."
        ),
    )
    thinking_enabled: bool = Field(
        default=DEFAULTS[ConfigKeys.THINKING_ENABLED],
        description="AI düşünme çıktısı aktif mi?",
    )
    log_settings: LogSettings = Field(
        default_factory=LogSettings,
        description="Logging ayarları",
    )

    agents: dict[str, AgentProfile] = Field(
        default_factory=dict,
        description="Agent profilleri ve ayarları",
    )
    active_agent: Optional[str] = Field(
        default=None,
        description="Varsayılan/aktif agent adı",
    )

    models: List[ModelConfig] = Field(default_factory=list, description="Mevcut modeller")
    tools: List[ToolConfig] = Field(default_factory=list, description="Mevcut araçlar")

    max_conversation_history: int = Field(
        default=DEFAULTS[ConfigKeys.MAX_CONVERSATION_HISTORY],
        ge=1,
        le=100,
        description="Geçmişte tutulacak mesaj sayısı",
    )
    rate_limit_per_user: int = Field(
        default=DEFAULTS[ConfigKeys.RATE_LIMIT_PER_USER],
        ge=1,
        description="Kullanıcı başına saatlik istek limiti",
    )
    ws_enable_real_agent: bool = Field(
        default=False,
        description="WebSocket üzerinden gelen mesajlar için gerçek agent modunu etkinleştirir.",
    )

    last_updated: Optional[datetime] = Field(default_factory=datetime.now)
    updated_by: Optional[str] = Field(
        default=DEFAULTS[ConfigKeys.UPDATED_BY],
        description="Son güncelleyen kişi",
    )


class ConfigUpdateRequest(BaseModel):
    """Konfigürasyon güncelleme isteği."""
    google_api_key: Optional[str] = None
    provider: Optional[Literal["google_genai", "vertexai"]] = None
    vertex_project: Optional[str] = None
    vertex_location: Optional[str] = None
    enable_tool_binding: Optional[bool] = None
    thinking_enabled: Optional[bool] = None
    agents: Optional[dict[str, AgentProfile]] = None
    active_agent: Optional[str] = None
    models: Optional[List[ModelConfig]] = None
    tools: Optional[List[ToolConfig]] = None
    system_prompt: Optional[str] = None
    max_conversation_history: Optional[int] = None
    rate_limit_per_user: Optional[int] = None
    ws_enable_real_agent: Optional[bool] = None
    log_settings: Optional[LogSettings] = None
    updated_by: Optional[str] = None
