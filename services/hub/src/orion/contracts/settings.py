from pydantic import BaseModel, Field, field_validator
import re
from orion.contracts.tools import ToolSelection, default_tool_selection

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
    # Memory: the new structure (collections, docs/hafiza-tasarimi.md §16). Internal, ON for everyone, no UI (docs §17c); the field
    # stays only so tests and an emergency fallback can turn it off. And how long a removed memory waits in the trash.
    memory_v2: bool = True
    memory_trash_days: int = Field(default=30, ge=1, le=3650)
    # The model that does it, a Router model group; empty = the chat model.
    memory_background_model: str = ""
    # With memory_v2: a short profile of the user (their pinned lines and their own facts, memory_profile) is added to the
    # system message of every "Asistanla konuş" request (never of a normal chat). Turn off to talk without it.
    memory_profile: bool = True
    # "Hafıza değişikliklerini otomatik onayla": a card (remember / replace / relation change / delete) is applied at once, without a
    # press; it is still listed in "Son eklenenler" with undo, and a delete still goes to the trash.
    memory_auto_approve: bool = True
    # The profile ("Profil", about the user) in (estimated) tokens: pinned lines always go in, the rest newest first. Past the
    # soft limit nothing happens; past the hard limit the background model compacts the non-pinned part to `compact_to`.
    memory_profile_soft_tokens: int = Field(default=600, ge=100, le=8000)
    memory_profile_tokens: int = Field(default=800, ge=200, le=8000)  # The hard limit.
    memory_profile_compact_tokens: int = Field(default=400, ge=50, le=4000)
    # "Yakında / güncel": a second small block (automatic): events and yearly dates coming within `soon_days`, important
    # events of the last `recent_days`, in at most `soon_tokens`.
    # Gündem limits like the profile's: soft (past it the list is trimmed to compact_tokens, furthest dates first), hard (never past).
    memory_soon_soft_tokens: int = Field(default=400, ge=50, le=4000)
    memory_soon_tokens: int = Field(default=600, ge=0, le=4000)  # The hard limit.
    memory_soon_compact_tokens: int = Field(default=300, ge=50, le=4000)
    memory_soon_days: int = Field(default=14, ge=1, le=366)
    memory_recent_days: int = Field(default=7, ge=1, le=366)
    # Chat compaction: one background-model call turns the conversation into a summary (its new first message) and memory
    # items. Automatic (a) when the chat has been idle for `idle_minutes` and its size is at `idle_percent` of the chat model's
    # context window, (b) before the next request is sent when it is at `force_percent`. The window comes from the Router's
    # catalog, else `chat_context_tokens` (0 = not known: only the manual "Sohbeti sıkıştır").
    chat_compact_idle_percent: int = Field(default=40, ge=10, le=95)
    chat_compact_idle_minutes: int = Field(default=30, ge=1, le=1440)
    chat_compact_force_percent: int = Field(default=90, ge=50, le=99)
    chat_context_tokens: int = Field(default=0, ge=0, le=10000000)

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
        # Hub stores a preference without requiring a particular STT engine.
        # Supported languages and aliases belong to that engine's catalog.
        if not re.fullmatch(r"[a-z]{2,3}", code):
            raise ValueError("Invalid STT language code")
        return code

    # TTS Configuration
    tts_enabled: bool = True
    tts_voice: str = ""
    tts_model: str = "local-tts"
    tts_model_voices: dict[str, str] = Field(default_factory=dict)
    # A name from the provider's own language catalog ("Auto" included), sent as chosen.
    # Empty: none is sent and the Router uses its own default. Providers that take no language ignore it.
    tts_language: str = Field(default="", max_length=100)
    tts_timeout_seconds: int = Field(default=15, ge=1, le=300)

    @field_validator("tts_language", mode="before")
    @classmethod
    def parse_tts_language(cls, value):
        return str(value or "").strip()

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
