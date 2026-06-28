"""
Admin Panel specific settings.
Only environment variables that the admin panel needs.
"""
from common.env_helper import get_env

# --- Admin Panel Settings ---
ADMIN_HOST = get_env("ADMIN_HOST")
ADMIN_PORT = int(get_env("ADMIN_PORT"))
ALLOWED_ORIGINS = get_env("ALLOWED_ORIGINS").split(",")

# Dışarıdan erişilecek gerçek backend adresi (admin panel bu URL'e istek atar)
API_BACKEND_URL = get_env("API_BACKEND_URL")
