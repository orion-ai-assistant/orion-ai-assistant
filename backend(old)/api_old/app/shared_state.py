from datetime import date
from typing import Dict

from .connection_manager import ConnectionManager
from .socketio_server import get_socketio_server


manager = ConnectionManager()
manager.set_socketio_server(get_socketio_server())
# session_chat_map: session_id → chat_id (transport katmanı için)
session_chat_map: Dict[str, str] = {}
# session_verified_chat_map: session_id → verified chat_id
session_verified_chat_map: Dict[str, str] = {}
# Not: Mesaj geçmişi (session_histories) repositories/session_repo.py'de tutulur.

# ── Mesaj sayaçları ──────────────────────────────────────────────────────────
_messages_today: int = 0
_messages_today_date: str = ""          # YYYY-MM-DD formatında, gün degişince sıfırlanır
_messages_per_user: Dict[str, int] = {} # user_id → toplam mesaj sayısı


def increment_message_count(user_id: str | None = None) -> None:
    global _messages_today, _messages_today_date
    today = date.today().isoformat()
    if today != _messages_today_date:
        _messages_today = 0
        _messages_today_date = today
    _messages_today += 1
    if user_id:
        _messages_per_user[user_id] = _messages_per_user.get(user_id, 0) + 1


def get_messages_today() -> int:
    today = date.today().isoformat()
    if today != _messages_today_date:
        return 0
    return _messages_today


def get_user_message_count(user_id: str) -> int:
    return _messages_per_user.get(user_id, 0)
