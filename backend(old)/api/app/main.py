from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
import socketio

from api.app.routes.socketio_routes import register_socket_events
from api.app.socketio_server import get_socketio_server, is_distributed_mode_enabled

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Worker'ları kaldırdığımız için burası temiz kalabilir
    yield

fastapi_app = FastAPI(lifespan=lifespan)

@fastapi_app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "orion-minimal-socketio-api",
        "status": "ok",
    }

@fastapi_app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "distributed_mode": is_distributed_mode_enabled(),
    }

sio = get_socketio_server()
register_socket_events(sio)
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)