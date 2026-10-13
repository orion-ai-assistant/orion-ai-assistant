from pydantic import BaseModel, Field, field_validator
import json
from pathlib import Path
from orion.contracts.tools import ToolSelection, default_tool_selection

_stt_catalog = json.loads((Path(__file__).resolve().parents[1] / "api/ui/js/stt_languages.json").read_text(encoding="utf-8"))

class SystemSettings(BaseModel):
    result_ttl_seconds: int = Field(default=86400, ge=1, le=2592000)
    sse_heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    worker_max_concurrency: int = Field(default=250, ge=1, le=10000)
    stop_key_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    redis_cache_ttl_seconds: int = Field(default=3600, ge=1, le=86400)


class AISettings(BaseModel):
    tool_selection: ToolSelection = Field(default_factory=default_tool_selection)
    google_ai_direct_stream: bool = True

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
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tool_rounds: int = Field(default=32, ge=1, le=128)
    max_tool_calls: int = Field(default=64, ge=1, le=256)

    @field_validator("temperature", mode="before")
    @classmethod
    def parse_temperature(cls, value):
        if isinstance(value, str) and value.strip().lower() in ("", "default", "none", "null"):
            return None
        return value
    
    # Router Configuration
    router_api_key: str = ""
    router_model_group: str = "local-chat"

    stt_enabled: bool = True
    stt_model: str = "local-stt"
    stt_language: str | None = None

    @field_validator("stt_language", mode="before")
    @classmethod
    def parse_stt_language(cls, value):
        if value is None or str(value).strip() == "":
            return None  # Unset: the browser chooses its supported primary language.
        code = str(value).strip().lower().replace("_", "-").split("-")[0]
        if code in ("auto", "none"):
            return "auto"
        code = _stt_catalog["aliases"].get(code, code)
        if code not in _stt_catalog["languages"]:
            raise ValueError("Unsupported STT language")
        return code

    # TTS Configuration
    tts_enabled: bool = True
    tts_voice: str = ""
    tts_model: str = "local-tts"
    tts_model_voices: dict[str, str] = Field(default_factory=dict)
    tts_timeout_seconds: int = Field(default=15, ge=1, le=300)

    @field_validator("tts_model_voices", mode="before")
    @classmethod
    def parse_tts_model_voices(cls, value):
        if isinstance(value, str):
            if not value.strip():
                return {}
            import json
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except Exception:
                return {}
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return {}


class RuntimeSettings(SystemSettings, AISettings):
    """Sistem ve Yapay Zeka ayarlarını tek bir düz yapıda birleştiren
    nihai çalışma zamanı (runtime) konfigürasyon modeli.
    """
    pass
