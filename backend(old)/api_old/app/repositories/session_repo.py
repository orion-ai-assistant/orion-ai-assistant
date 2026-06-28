"""
Session history repository.

Her WebSocket oturumuna ait LangChain mesaj listesini saklar.

Mevcut backend : in-memory dict (process yeniden başlayınca sıfırlanır).
TODO: Redis'e taşı (redis-py / aioredis) — kalıcı ve çok-process desteği için.
      Sadece bu dosyayı değiştirmen yeterli; session_service ve üstündeki katmanlar değişmez.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from api.app.store.redis_client import get_redis_client
from log import Logger

_store: Dict[str, List[Any]] = {}
_SESSION_KEY_PREFIX = "session:history:"
_SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours

logger = Logger(__file__)


def _key(session_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{session_id}"


async def get(session_id: str) -> List[Any] | None:
    """Oturumun mesaj geçmişini döner; kayıt yoksa None."""
    r = get_redis_client()
    try:
        raw = await r.get(_key(session_id))
        if raw is None:
            return None
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, list) else None
    except Exception as exc:
        logger.warning(lambda: f"session_repo.get redis read failed for {session_id}: {exc}")
    return _store.get(session_id)


async def save(session_id: str, messages: List[Any]) -> None:
    """Oturumun mesaj geçmişini kaydeder/günceller."""
    r = get_redis_client()
    try:
        await r.setex(_key(session_id), _SESSION_TTL_SECONDS, json.dumps(messages, ensure_ascii=False))
    except Exception as exc:
        logger.warning(lambda: f"session_repo.save redis write failed for {session_id}: {exc}")
    _store[session_id] = messages


async def delete(session_id: str) -> None:
    """Oturumun kaydını siler (örn. bağlantı kesilince)."""
    r = get_redis_client()
    try:
        await r.delete(_key(session_id))
    except Exception as exc:
        logger.warning(lambda: f"session_repo.delete redis delete failed for {session_id}: {exc}")
    _store.pop(session_id, None)
