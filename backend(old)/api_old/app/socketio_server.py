from __future__ import annotations

import os

import socketio
from common.design_patterns.singleton import SingletonMeta
from config.settings.api_server import REDIS_URL


def _is_distributed_mode_enabled() -> bool:
    raw = os.getenv("SOCKETIO_DISTRIBUTED_MODE", "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SocketIOServer(metaclass=SingletonMeta):
    """Process-local Socket.IO server singleton."""

    def __init__(self):
        options = {
            "async_mode": "asgi",
            "cors_allowed_origins": "*",
        }
        if REDIS_URL and _is_distributed_mode_enabled():
            options["client_manager"] = socketio.AsyncRedisManager(REDIS_URL)

        self.sio = socketio.AsyncServer(**options)


def get_socketio_server() -> socketio.AsyncServer:
    """Return the shared Socket.IO server instance for this process."""
    return SocketIOServer().sio
