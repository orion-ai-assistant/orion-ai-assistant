import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app: FastAPI) -> None:
    """Allow local Flutter web clients and explicitly configured deployments."""
    origins = [origin.strip() for origin in os.getenv("ORION_CORS_ORIGINS", "").split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError("ORION_CORS_ORIGINS must list explicit origins, not '*'")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?",
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
        allow_credentials=False,
    )
