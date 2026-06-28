"""
Session service.

Sorumluluğu:
  - Agent'a gönderilecek mesaj listesini oluşturur
    (sistem promptu + oturum geçmişi + yeni kullanıcı mesajı)
  - Agent'ın yanıtından gelen son mesaj listesini kalıcı hale getirir
  - Oturumu temizler (WebSocket bağlantısı kesilince)

WebSocket / HTTP / agent'ın iç yapısını bilmez.
Veri katmanı için session_repo kullanır; depo değişirse bu servis değişmez.
"""
from __future__ import annotations

import time

from common.env_helper import get_env
from log import Logger

logger = Logger(__file__)

from api.app.repositories import session_repo

PROMPT_CACHE_TTL_SECONDS = float(get_env("SESSION_PROMPT_CACHE_TTL_SECONDS", required=False) or "2.0")
_PROMPT_CACHE: dict[str, object] = {"expires_at": 0.0, "prompt": None}


def _system_message(content: str) -> dict[str, str]:
    return {"role": "system", "content": content}


def _human_message(content: str) -> dict[str, str]:
    return {"role": "user", "content": content}


def _get_system_prompt() -> dict[str, str]:
    """Config'deki system_prompt'u döner; agent-specific varsa onu, sonra global."""
    now = time.monotonic()
    cached_prompt = _PROMPT_CACHE.get("prompt")
    expires_at = float(_PROMPT_CACHE.get("expires_at") or 0.0)
    if isinstance(cached_prompt, dict) and now < expires_at:
        return cached_prompt

    from config.config_manager import get_config_manager
    config = get_config_manager().get_config()

    active_agent_name = getattr(config, "active_agent", None)
    if active_agent_name and getattr(config, "agents", None):
        agent_profile = config.agents.get(active_agent_name)
        if agent_profile and getattr(agent_profile, "system_prompt", None):
            prompt = _system_message(str(agent_profile.system_prompt))
            _PROMPT_CACHE["prompt"] = prompt
            _PROMPT_CACHE["expires_at"] = now + PROMPT_CACHE_TTL_SECONDS
            return prompt

    prompt = _system_message(str(config.system_prompt))
    _PROMPT_CACHE["prompt"] = prompt
    _PROMPT_CACHE["expires_at"] = now + PROMPT_CACHE_TTL_SECONDS
    return prompt


def _is_system_message_entry(message: object) -> bool:
    if isinstance(message, dict):
        return str(message.get("role", "")).lower() == "system"
    return False



async def get_messages_for_agent(chat_id: str, user_text: str) -> list:
    """
    Agent'a gönderilecek tam mesaj listesini döner:
        [SYSTEM_PROMPT, ...önceki geçmiş..., HumanMessage(user_text)]
    Her çağrıda güncel system_prompt kullanılır (config değişirse otomatik güncellenir).
    Model değiştirildiğinde geçmişteki Gemini thinking bloklarını temizler.
    """
    history = await session_repo.get(chat_id)
    current_prompt = _get_system_prompt()
    if history is None:
        return [current_prompt, _human_message(user_text)]
    msgs = list(history)
    # Kayıtlı system_prompt'u her seferinde güncel config ile güncelle
    if msgs and _is_system_message_entry(msgs[0]):
        msgs[0] = current_prompt
    else:
        msgs = [current_prompt] + msgs
    # Mesajlar doğrudan kullanılacak.
    return msgs + [_human_message(user_text)]


async def save_session(chat_id: str, final_messages: list) -> None:
    """
    Agent'ın döndürdüğü son mesaj listesini oturuma kaydeder.
    SYSTEM_PROMPT listenin başında yoksa otomatik eklenir.
    """
    messages = list(final_messages)
    if not messages or not _is_system_message_entry(messages[0]):
        messages = [_get_system_prompt()] + messages
    await session_repo.save(chat_id, messages)


async def clear_session(chat_id: str) -> None:
    """Oturum geçmişini siler (bağlantı kesilince çağrılmalı)."""
    await session_repo.delete(chat_id)
