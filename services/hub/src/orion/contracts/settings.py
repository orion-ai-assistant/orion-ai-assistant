from pydantic import BaseModel, Field, field_validator
from orion.contracts.tools import ToolSelection, default_tool_selection

class SystemSettings(BaseModel):
    result_ttl_seconds: int = Field(default=86400, ge=1, le=2592000)
    sse_heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    worker_max_concurrency: int = Field(default=250, ge=1, le=10000)
    stop_key_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    redis_cache_ttl_seconds: int = Field(default=3600, ge=1, le=86400)


class AISettings(BaseModel):
    tool_selection: ToolSelection = Field(default_factory=default_tool_selection)

    @field_validator("tool_selection", mode="before")
    @classmethod
    def parse_tool_selection(cls, value):
        if isinstance(value, str):
            return ToolSelection.model_validate_json(value)
        return value

    ai_chat_titles_enabled: bool = False
    chat_title_model: str = ""
    first_token_delay_ms: int = Field(default=0, ge=0, le=60000)
    token_delay_ms: int = Field(default=50, ge=0, le=10000)
    chat_history_max_messages: int = Field(default=20, ge=1, le=1000)
    llm_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    system_prompt: str = "You are Orion. Reply friendly and concisely. You MUST answer directly. Do NOT output your thinking process or explain your step-by-step reasoning. Just give the final answer."
    embed_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    
    thinking_level: str = ""
    temperature: float = Field(default=0.9, ge=0, le=2)
    
    # Router Configuration
    router_api_key: str = ""
    router_model_group: str = "local-chat"

    stt_enabled: bool = True
    stt_model: str = "local-stt"

    # TTS Configuration
    tts_enabled: bool = True
    tts_voice: str = ""
    tts_model: str = "local-tts"
    tts_timeout_seconds: int = Field(default=15, ge=1, le=300)


class RuntimeSettings(SystemSettings, AISettings):
    """Sistem ve Yapay Zeka ayarlarını tek bir düz yapıda birleştiren
    nihai çalışma zamanı (runtime) konfigürasyon modeli.
    """
    pass
