from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Dict

from log import Logger
import socketio


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, str] = {}
        self._user_sids: DefaultDict[str, set[str]] = defaultdict(set)
        self._sio: socketio.AsyncServer | None = None

    def set_socketio_server(self, sio_server: socketio.AsyncServer) -> None:
        self._sio = sio_server

    def connect(self, sid: str, user_id: str) -> None:
        self.active_connections[sid] = user_id
        self._user_sids[user_id].add(sid)

    def disconnect(self, sid: str) -> str | None:
        user_id = self.active_connections.pop(sid, None)
        if not user_id:
            return None

        user_sid_set = self._user_sids.get(user_id)
        if user_sid_set is not None:
            user_sid_set.discard(sid)
            if not user_sid_set:
                self._user_sids.pop(user_id, None)
        return user_id

    def get_user_id(self, sid: str) -> str | None:
        return self.active_connections.get(sid)

    def has_active_user(self, user_id: str) -> bool:
        return bool(self._user_sids.get(user_id))

    async def send_to(self, sid: str, payload: Dict[str, Any], event: str = "message") -> None:
        if self._sio is None or sid not in self.active_connections:
            return
        await self._sio.emit(event, payload, to=sid)

    async def broadcast_to_user(self, user_id: str, payload: Dict[str, Any], event: str = "message", skip_sid: str | None = None) -> None:
        if self._sio is None:
            return
        await self._sio.emit(event, payload, room=f"user_{user_id}", skip_sid=skip_sid)

    async def broadcast_to_chat(self, chat_id: str, payload: Dict[str, Any], event: str = "message", skip_sid: str | None = None) -> None:
        if self._sio is None:
            return
        logger = Logger(__file__)
        logger.debug(lambda: f"[broadcast_to_chat] room=chat_{chat_id} event={event} payload={payload}")
        await self._sio.emit(event, payload, room=f"chat_{chat_id}", skip_sid=skip_sid)
