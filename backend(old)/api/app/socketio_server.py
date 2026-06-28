from __future__ import annotations

import os

import socketio

_sio: socketio.AsyncServer | None = None


def _is_true(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_distributed_mode_enabled() -> bool:
    return _is_true(os.getenv("SOCKETIO_DISTRIBUTED_MODE", "false"))


def is_websocket_only_enabled() -> bool:
    return _is_true(os.getenv("WS_TRANSPORT_WEBSOCKET_ONLY", "true"))


def _build_socketio_server() -> socketio.AsyncServer:
    redis_url = os.getenv("REDIS_URL", "").strip()

    options: dict[str, object] = {
        "async_mode": "asgi",
        "async_handlers": True,
        "cors_allowed_origins": "*",
        "ping_interval": int(os.getenv("SOCKETIO_PING_INTERVAL_SECONDS", "25")),
        "ping_timeout": int(os.getenv("SOCKETIO_PING_TIMEOUT_SECONDS", "60")),
        "max_http_buffer_size": int(os.getenv("SOCKETIO_MAX_HTTP_BUFFER_BYTES", str(1_000_000))),
        "logger": False,
        "engineio_logger": False,
    }

    if is_websocket_only_enabled():
        options["transports"] = ["websocket"]

    if redis_url and is_distributed_mode_enabled():
        options["client_manager"] = socketio.AsyncRedisManager(redis_url)

    return socketio.AsyncServer(**options)


def get_socketio_server() -> socketio.AsyncServer:
    global _sio
    if _sio is None:
        _sio = _build_socketio_server()
    return _sio
