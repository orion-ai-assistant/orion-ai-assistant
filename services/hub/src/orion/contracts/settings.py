from pydantic import BaseModel

class SystemSettings(BaseModel):
    result_ttl_seconds: int = 86400
    sse_heartbeat_seconds: int = 15
    worker_max_concurrency: int = 250
    stop_key_ttl_seconds: int = 3600
    redis_cache_ttl_seconds: int = 3600


class AISettings(BaseModel):
    first_token_delay_ms: int = 0
    token_delay_ms: int = 50
    chat_history_max_messages: int = 20
    llm_timeout_seconds: int = 120
    system_prompt: str = "You are Orion. Reply friendly and concisely. You MUST answer directly. Do NOT output your thinking process or explain your step-by-step reasoning. Just give the final answer."
    embed_timeout_seconds: int = 60
    
    thinking_level: str = "default"
    temperature: float = 0.7
    
    # Router Configuration
    router_api_key: str = "sk-60f3eaf169d7c485-0icocf-0a3db541"
    router_model_group: str = "local-model"

class RuntimeSettings(SystemSettings, AISettings):
    """Sistem ve Yapay Zeka ayarlarını tek bir düz yapıda birleştiren
    nihai çalışma zamanı (runtime) konfigürasyon modeli.
    """
    pass
