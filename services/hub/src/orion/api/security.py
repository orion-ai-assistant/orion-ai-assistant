from services.shared.environment import get_env
from datetime import datetime, timedelta, timezone

import hmac
import ipaddress
import logging
import os
import sys

import bcrypt
import jwt
from fastapi import HTTPException, status

DEFAULT_JWT_SECRET = "super-secret-default-key-for-dev"
JWT_SECRET_KEY = get_env("JWT_SECRET_KEY", DEFAULT_JWT_SECRET, required=False)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 24 * 60  # 24 hours

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def admin_key_matches(provided: str | None) -> bool:
    configured = get_env("ADMIN_API_KEY", required=False)
    return bool(configured and provided and hmac.compare_digest(provided.encode(), configured.encode()))


def require_admin(request) -> None:
    provided = request.headers.get("X-Admin-Key") or request.query_params.get("admin_key")
    if not admin_key_matches(provided):
        raise HTTPException(status_code=403, detail="Admin access denied")


def validate_jwt_configuration() -> None:
    """Reject development secrets before any startup side effects in exposed modes."""
    if not JWT_SECRET_KEY or not JWT_SECRET_KEY.strip():
        raise RuntimeError("JWT_SECRET_KEY must not be empty")
    if JWT_SECRET_KEY != DEFAULT_JWT_SECRET:
        return
    mode = os.getenv("ORION_HUB_MODE", os.getenv("ORION_INSTALL_MODE", "local")).lower()
    environment = os.getenv("ORION_ENVIRONMENT", os.getenv("ENVIRONMENT", "development")).lower()
    host = os.getenv("UVICORN_HOST", "127.0.0.1")
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg == "--host" and index + 1 < len(sys.argv):
            host = sys.argv[index + 1]
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if mode not in {"local", "development", "dev"} or environment in {"production", "prod", "remote"} or not loopback:
        raise RuntimeError("Configure a non-default JWT_SECRET_KEY before starting Hub in remote/production mode")
    logging.getLogger(__name__).warning("SECURITY WARNING: Hub uses the development JWT secret; local development only. Configure JWT_SECRET_KEY before remote access.")
