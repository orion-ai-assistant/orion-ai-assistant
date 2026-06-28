"""Shared HTTP client utilities for admin panel backend communication."""
from pathlib import Path

import httpx


def resolve_backend_url(configured_url: str) -> str:
    """Resolve a single backend URL based on runtime environment."""
    base = (configured_url or "").rstrip("/")
    in_docker = Path("/.dockerenv").exists()

    if not base:
        return "http://api:8000" if in_docker else "http://127.0.0.1:8000"

    # In Docker, localhost points to the current container, not the API container.
    if in_docker and ("localhost" in base or "127.0.0.1" in base):
        return "http://api:8000"

    return base


class BackendClient:
    """Thin wrapper around httpx for authenticated backend requests."""

    def __init__(self, api_url: str, timeout: float = 5.0):
        self.api_url = resolve_backend_url(api_url)
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        **kwargs,
    ) -> httpx.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # Allow caller to add/override headers
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))
        async with httpx.AsyncClient() as client:
            return await client.request(
                method,
                f"{self.api_url}{normalized_path}",
                headers=headers or None,
                timeout=self.timeout,
                **kwargs,
            )
