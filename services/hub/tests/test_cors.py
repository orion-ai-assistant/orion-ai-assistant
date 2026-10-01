import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from orion.api.cors import configure_cors


class CorsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        with patch.dict(os.environ, {"ORION_CORS_ORIGINS": "https://orion.example"}):
            configure_cors(self.app)

        @self.app.post("/api/v1/auth/login")
        async def login():
            raise HTTPException(401, "Incorrect username or password")

        @self.app.post("/api/v1/auth/register", status_code=201)
        async def register():
            return {"message": "User created successfully"}

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://hub")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_flutter_preflight_accepts_login_and_registration(self):
        for origin in ("http://localhost:51842", "http://127.0.0.1:7357", "http://[::1]:7357"):
            for endpoint in ("login", "register"):
                response = await self.client.options(f"/api/v1/auth/{endpoint}", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,authorization",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
                self.assertNotIn("access-control-allow-credentials", response.headers)

    async def test_auth_errors_and_success_responses_retain_cors_headers(self):
        for endpoint, status in (("login", 401), ("register", 201)):
            response = await self.client.post(f"/api/v1/auth/{endpoint}", headers={"Origin": "http://localhost:51842"}, json={})
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:51842")

    async def test_nonlocal_origins_require_explicit_configuration(self):
        for origin, status in (("https://orion.example", 200), ("https://untrusted.example", 400),
                               ("http://localhost.untrusted.example:7357", 400), ("null", 400)):
            response = await self.client.options("/api/v1/auth/login", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
            })
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers.get("access-control-allow-origin"), origin if status == 200 else None)
