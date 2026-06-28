from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import socketio
import uvicorn

from config.settings.api_server import (
    API_HOST,
    API_PORT,
    ADMIN_TOKEN,
    DOCS_URL,
    REDOC_URL,
    OPENAPI_URL,
)
from config.config_manager import get_config_manager
from api.app.routes.http_routes import router as http_router
from api.app.routes.admin_routes import router as admin_router
from api.app.routes.user_admin_routes import router as user_admin_router
from api.app.services.agent_event_bridge import start_agent_event_bridge, stop_agent_event_bridge
from api.app.socketio_server import get_socketio_server
from api.app.routes.socketio_routes import register_socket_events, start_incoming_message_workers, stop_incoming_message_workers
from log import Logger

logger = Logger(__file__)

_security = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown lifecycle hooks for API process."""
    try:
        get_config_manager().refresh_system_sync(persist=True)
    except Exception as exc:
        logger.error(lambda: f"Config startup sync failed: {exc}")
    await start_incoming_message_workers()
    await start_agent_event_bridge()
    yield
    await stop_agent_event_bridge()
    await stop_incoming_message_workers()

def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_TOKEN is not configured on the server",
        )
    if credentials is None or credentials.credentials != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


fastapi_app = FastAPI(
    lifespan=lifespan,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
)

# Allow CORS
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fastapi_app.include_router(http_router)
fastapi_app.include_router(admin_router, dependencies=[Depends(verify_admin_token)])  # Admin config endpoints
fastapi_app.include_router(user_admin_router, dependencies=[Depends(verify_admin_token)])  # Admin user management


@fastapi_app.post("/admin/auth/verify")
async def verify_auth_token(token: str = Body(..., embed=True)):
    """Admin panel login doğrulaması. Token doğruysa 200, yanlışsa 401."""
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_TOKEN is not configured on the server",
        )
    if token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return {"valid": True}


sio = get_socketio_server()
# SOCKET.IO EVENT'LERİNİ AÇIKÇA AÇIYORUZ
register_socket_events(sio)

# Combined ASGI entrypoint: Socket.IO + FastAPI
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

if __name__ == "__main__":
    # API_HOST/API_PORT sadece uvicorn bind ayarıdır; dış URL için API_BACKEND_URL kullanılır.
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        access_log=False,
        log_level="warning",
    )
