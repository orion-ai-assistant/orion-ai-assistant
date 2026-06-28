"""
Orion AI Admin Panel
Modern, modular admin interface for managing AI configuration and monitoring users.

Auth akışı: Admin panel token doğrulamasını doğrudan yapmaz.
Kullanıcının girdiği token, API'ye (/admin/auth/verify) gönderilir ve
API doğrularsa httpOnly cookie set edilir. Sonraki isteklerde cookie'deki
token, API'ye Bearer header olarak iletilir.
"""
from fastapi import FastAPI, Depends, HTTPException, status, Body, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import time
from collections import defaultdict

from pathlib import Path
from common.env_helper import get_env
from config.settings.admin import (
    ADMIN_HOST as HOST,
    ADMIN_PORT as PORT,
    ALLOWED_ORIGINS,
    API_BACKEND_URL,
)
from admin_panel.app.services.backend_client import BackendClient
from admin_panel.app.services.api_paths import ADMIN_AUTH_VERIFY
from log import Logger
from admin_panel.app.routes import config_router, user_router, stats_router

_ENV = (get_env("ENV", required=False) or "dev").strip().lower()
_IS_PROD = _ENV == "prod"
DOCS_URL = None if _IS_PROD else "/docs"
REDOC_URL = None if _IS_PROD else "/redoc"
OPENAPI_URL = None if _IS_PROD else "/openapi.json"
ADMIN_TOKEN = get_env("ADMIN_TOKEN", required=False)

# Local paths
STATIC_DIR = Path(__file__).parent / "static"
logger = Logger(__file__)

# Backend client for auth verification
_auth_client = BackendClient(API_BACKEND_URL)

# Brute force koruması: IP başına başarısız deneme sayısı ve kilit süresi
_login_attempts: dict = defaultdict(lambda: {"count": 0, "locked_until": 0.0})
_MAX_ATTEMPTS = 5
_LOCK_SECONDS = 30
_security = HTTPBearer(auto_error=False)

def _check_rate_limit(ip: str):
    record = _login_attempts[ip]
    if time.time() < record["locked_until"]:
        retry_after = int(record["locked_until"] - time.time()) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Çok fazla başarısız giriş. {retry_after} saniye bekleyin.",
            headers={"Retry-After": str(retry_after)},
        )

def _record_failed(ip: str):
    record = _login_attempts[ip]
    record["count"] += 1
    if record["count"] >= _MAX_ATTEMPTS:
        record["locked_until"] = time.time() + _LOCK_SECONDS
        record["count"] = 0

def _record_success(ip: str):
    _login_attempts.pop(ip, None)


def verify_admin_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_security),
):
    """Cookie'de admin_session olup olmadığını kontrol eder.
    
    Token'ın geçerliliği API tarafında doğrulanır — admin panel sadece
    cookie varlığını kontrol eder. Yanlış token olursa API 401 döner
    ve frontend otomatik logout yapar.
    """
    # Swagger docs can authenticate with Bearer token via Authorize button.
    if credentials is not None:
        if not ADMIN_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ADMIN_TOKEN is not configured on the server",
            )
        if credentials.credentials != ADMIN_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing admin token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return credentials.credentials

    token = request.cookies.get("admin_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


# Initialize FastAPI app
app = FastAPI(
    title="Orion AI Admin Panel",
    description="Admin interface for managing AI configuration and monitoring",
    version="1.0.0",
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include routers (tüm API rotaları admin session gerektirir)
app.include_router(config_router, dependencies=[Depends(verify_admin_session)])
app.include_router(user_router, dependencies=[Depends(verify_admin_session)])
app.include_router(stats_router, dependencies=[Depends(verify_admin_session)])


@app.post("/api/auth/login")
async def login(request: Request, response: Response, token: str = Body(..., embed=True)):
    """Admin token ile giriş doğrulama.
    
    Token'ı API'ye gönderip doğrulatır. Doğruysa httpOnly cookie set eder.
    Admin panel kendi başına token doğrulaması YAPMAZ.
    """
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    try:
        # API'ye token doğrulatma isteği gönder
        verify_resp = await _auth_client.request(
            "POST",
            ADMIN_AUTH_VERIFY,
            json={"token": token},
        )

        if verify_resp.status_code != 200:
            _record_failed(ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(lambda: f"Auth verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backend API unreachable",
        )

    _record_success(ip)
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,       # JS erişemez
        samesite="strict",   # CSRF koruması
        secure=False,        # Prod'da True yapın (HTTPS gerektir)
        max_age=8 * 3600,    # 8 saat
    )
    return {"success": True}


@app.post("/api/auth/logout")
async def logout_endpoint(response: Response):
    """Admin oturumunu sonlandır."""
    response.delete_cookie("admin_session")
    return {"success": True}


@app.get("/")
async def index():
    """Serve the admin dashboard."""
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/api/health")
async def health_check():
    """Quick health check for the admin panel itself."""
    return {
        "status": "healthy",
        "service": "admin_panel",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Orion AI Admin Panel")
    logger.info(lambda: f"Dashboard: http://{HOST}:{PORT}")
    logger.info(lambda: f"API Docs: http://{HOST}:{PORT}/docs")
    uvicorn.run(app, host=HOST, port=PORT)
