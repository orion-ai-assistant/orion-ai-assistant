from services.shared.environment import get_env
import os
import sys
import logging
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

JWT_SECRET_KEY = get_env("JWT_SECRET_KEY", "super-secret-default-key-for-dev")
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


DEFAULT_JWT_SECRET = "super-secret-default-key-for-dev"


def validate_security_configuration() -> None:
    """Refuse development secrets in network-facing or production deployments."""
    mode = os.getenv("ORION_DEPLOYMENT_MODE", "local").strip().lower()
    if mode not in {"local", "remote", "prod", "production"}:
        raise RuntimeError("ORION_DEPLOYMENT_MODE must be local, remote or production")
    host = os.getenv("ORION_BIND_HOST", os.getenv("UVICORN_HOST", "127.0.0.1"))
    for index, argument in enumerate(sys.argv):
        if argument == "--host" and index + 1 < len(sys.argv):
            host = sys.argv[index + 1]
        elif argument.startswith("--host="):
            host = argument.split("=", 1)[1]
    insecure = not JWT_SECRET_KEY or JWT_SECRET_KEY == DEFAULT_JWT_SECRET
    loopback = {"127.0.0.1", "localhost", "::1"}
    binding_signals = (host, os.getenv("ORION_BIND_HOST"), os.getenv("UVICORN_HOST"))
    remote = mode != "local" or any(value and value not in loopback for value in binding_signals)
    if insecure and remote:
        raise RuntimeError("Configure a non-default JWT_SECRET_KEY before remote/production startup")
    if insecure:
        logging.getLogger(__name__).warning("Development JWT secret in use: loopback-only local development; configure JWT_SECRET_KEY before remote access")


def valid_admin_key(provided: str | None) -> bool:
    configured = get_env("ADMIN_API_KEY", required=False)
    return bool(configured and provided and hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8")))
