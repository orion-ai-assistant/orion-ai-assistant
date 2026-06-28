from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

messages_by_chat: Dict[str, List[Dict[str, Any]]] = {}
files_by_chat: Dict[str, List[Dict[str, Any]]] = {}
chats_by_id: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_chat(chat_id: str) -> None:
    if chat_id not in chats_by_id:
        chats_by_id[chat_id] = {
            "chat_id": chat_id,
            "created_by": "system",
            "members": set(),
            "created_at": _now_iso(),
        }
    if chat_id not in messages_by_chat:
        messages_by_chat[chat_id] = []
    if chat_id not in files_by_chat:
        files_by_chat[chat_id] = []


def create_chat(chat_id: str, created_by: str) -> Dict[str, Any]:
    if chat_id in chats_by_id:
        raise ValueError(f"Chat already exists: {chat_id}")

    chats_by_id[chat_id] = {
        "chat_id": chat_id,
        "created_by": created_by,
        "members": {created_by},
        "created_at": _now_iso(),
    }
    messages_by_chat[chat_id] = []
    files_by_chat[chat_id] = []
    return get_chat(chat_id)


def get_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    chat = chats_by_id.get(chat_id)
    if not chat:
        return None

    return {
        "chat_id": str(chat.get("chat_id", chat_id)),
        "created_by": str(chat.get("created_by", "system")),
        "members": sorted(str(x) for x in chat.get("members", set())),
        "created_at": str(chat.get("created_at", "")),
    }


def chat_exists(chat_id: str) -> bool:
    return chat_id in chats_by_id


def add_chat_member(chat_id: str, user_id: str) -> None:
    chat = chats_by_id.get(chat_id)
    if not chat:
        raise ValueError(f"Chat not found: {chat_id}")

    members = chat.get("members")
    if not isinstance(members, set):
        members = set(members or [])
        chat["members"] = members
    members.add(user_id)


def user_can_access_chat(chat_id: str, user_id: str) -> bool:
    chat = chats_by_id.get(chat_id)
    if not chat:
        return False

    members = chat.get("members", set())
    return user_id in members


def add_message(
    chat_id: str,
    sender: str,
    content: str,
    message_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _ensure_chat(chat_id)
    message = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "sender": sender,
        "message_type": message_type,
        "content": content,
        "metadata": metadata or {},
        "created_at": _now_iso(),
    }
    messages_by_chat[chat_id].append(message)
    return message


def get_messages(chat_id: str, limit: int = 50, before_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_chat(chat_id)
    items = messages_by_chat[chat_id]

    if before_id:
        before_index = next((index for index, m in enumerate(items) if m["id"] == before_id), None)
        if before_index is not None:
            items = items[:before_index]

    if limit <= 0:
        return []

    return items[-limit:]


def get_all_messages(chat_id: str) -> List[Dict[str, Any]]:
    _ensure_chat(chat_id)
    return list(messages_by_chat[chat_id])


def chat_has_activity(chat_id: str) -> bool:
    _ensure_chat(chat_id)
    return len(messages_by_chat.get(chat_id, [])) > 0


def add_file(
    chat_id: str,
    filename: Optional[str],
    content_type: Optional[str],
    size_bytes: int,
    description: str = "",
) -> Dict[str, Any]:
    _ensure_chat(chat_id)
    file_item = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "filename": filename or "unknown",
        "content_type": content_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "description": description,
        "created_at": _now_iso(),
    }
    files_by_chat[chat_id].append(file_item)
    return file_item


def get_files(chat_id: str) -> List[Dict[str, Any]]:
    _ensure_chat(chat_id)
    return list(files_by_chat[chat_id])
