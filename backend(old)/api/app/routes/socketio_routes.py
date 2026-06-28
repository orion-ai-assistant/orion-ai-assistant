from __future__ import annotations

import logging
import os
import time
from typing import Any

import socketio

from api.app.services.mock_reply_service import build_mock_reply
from api.app.socketio_server import get_socketio_server

logger = logging.getLogger(__name__)

_EVENT_CONNECTED = "chat:connected"
_EVENT_JOINED = "chat:joined"
_EVENT_ACK = "chat:ack"
_EVENT_MESSAGE_RECEIVED = "chat:message:received"
_EVENT_MOCK_REPLY = "chat:mock_reply"
_EMIT_ACK = os.getenv("WS_EMIT_ACK", "false").strip().lower() in {"1", "true", "yes", "on"}
_EMIT_LEGACY_MOCK_REPLY = os.getenv("WS_EMIT_LEGACY_MOCK_REPLY", "false").strip().lower() in {"1", "true", "yes", "on"}
_LIGHTWEIGHT_PAYLOAD = os.getenv("WS_LIGHTWEIGHT_PAYLOAD", "true").strip().lower() in {"1", "true", "yes", "on"}

_sessions: dict[str, str] = {}


def _extract_chat_id(auth: dict[str, Any] | None, sid: str) -> str:
    auth_data = auth if isinstance(auth, dict) else {}
    chat_id = auth_data.get("chat_id") or auth_data.get("chatId")
    chat_text = str(chat_id or "").strip()
    return chat_text or sid


def _extract_text(data: str | dict[str, Any]) -> str:
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    if isinstance(payload.get("message"), str):
        return payload["message"].strip()
    if isinstance(payload.get("text"), str):
        return payload["text"].strip()
    if isinstance(payload.get("message"), dict):
        return str(payload["message"].get("text") or "").strip()
    return ""


async def _emit_ack(sid: str) -> None:
    sio = get_socketio_server()
    await sio.emit(_EVENT_ACK, {"ok": True}, to=sid)


async def _send_mock_reply(sid: str, chat_id: str, text: str, start_time: float) -> None:
    sio = get_socketio_server()
    processing_ms = (time.time() - start_time) * 1000
    
    payload: dict[str, Any] = {
        "ok": True,
        "chat_id": chat_id,
        "text": build_mock_reply(text),
        "server_sent_ns": time.time_ns(),
    }
    
    if not _LIGHTWEIGHT_PAYLOAD:
        payload["metadata"] = {
            "dominant_wait": "model",
            "processing_ms": processing_ms,
            "model_ms": processing_ms * 0.8,
            "queue_backlog_ms": 0.0, # Kuyruk kaldırıldığı için sıfır
        }
    else:
        payload["model_ms"] = round(processing_ms * 0.8, 3)
        payload["queue_backlog"] = 0 # Kuyruk kaldırıldığı için sıfır
        
    await sio.emit(_EVENT_MESSAGE_RECEIVED, payload, to=sid)
    if _EMIT_LEGACY_MOCK_REPLY:
        await sio.emit(_EVENT_MOCK_REPLY, payload, to=sid)


async def connect(sid: str, _environ: dict[str, Any], auth: dict[str, Any] | None = None) -> bool:
    chat_id = _extract_chat_id(auth, sid)
    _sessions[sid] = chat_id

    sio = get_socketio_server()
    await sio.emit(
        _EVENT_CONNECTED,
        {"ok": True, "sid": sid, "chat_id": chat_id},
        to=sid,
    )
    return True


async def disconnect(sid: str) -> None:
    _sessions.pop(sid, None)


async def handle_chat_join(sid: str, data: str | dict[str, Any] | None = None) -> None:
    old_chat_id = _sessions.get(sid)
    if old_chat_id is None:
        return

    requested_chat_id = None
    if isinstance(data, dict):
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        requested_chat_id = payload.get("chat_id") or payload.get("chatId")

    new_chat_id = str(requested_chat_id or "").strip() or old_chat_id
    _sessions[sid] = new_chat_id

    sio = get_socketio_server()
    await sio.emit(
        _EVENT_JOINED,
        {"ok": True, "chat_id": new_chat_id, "previous_chat_id": old_chat_id},
        to=sid,
    )


async def handle_chat_message(sid: str, data: str | dict[str, Any]) -> None:
    start_time = time.time()
    chat_id = _sessions.get(sid)
    if chat_id is None:
        return

    text = _extract_text(data)
    if not text:
        return

    if _EMIT_ACK:
        await _emit_ack(sid)

    # Mesajı kuyruğa atmak yerine DİREKT işliyoruz.
    await _send_mock_reply(sid, chat_id, text, start_time)


def register_socket_events(sio_instance: socketio.AsyncServer) -> None:
    sio_instance.on("connect")(connect)
    sio_instance.on("disconnect")(disconnect)
    sio_instance.on("chat:join")(handle_chat_join)
    sio_instance.on("chat:message")(handle_chat_message)