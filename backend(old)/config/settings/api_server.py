"""
API'ye özel ayarlar.
Bu dosya yalnızca API process'inde import edilmelidir.
"""
from common.env_helper import get_env
from config.settings.core import BACKEND_DIR, IS_PROD

# FastAPI docs endpoints: enabled in dev, disabled in prod.
DOCS_URL = None if IS_PROD else "/docs"
REDOC_URL = None if IS_PROD else "/redoc"
OPENAPI_URL = None if IS_PROD else "/openapi.json"

# --- Yalnızca API'ye özel ---
# API spesifik değerler için or kullanmıyoruz ve strict checks (required=True) yapıyoruz.
ADMIN_TOKEN = get_env("ADMIN_TOKEN", required=True)
API_HOST = get_env("API_HOST", required=True)
API_PORT = int(get_env("API_PORT", required=True))
WS_ENABLE_REAL_AGENT = get_env("WS_ENABLE_REAL_AGENT", required=False) == "true"

# Socket.IO Redis manager for multi-worker deployment
REDIS_URL = get_env("REDIS_URL", required=False)

# Paths
ADMIN_STATIC_DIR = BACKEND_DIR / "admin_panel" / "app" / "static"
