import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Header, HTTPException
from orion.api.access_boundary import AccessBoundary
from orion.api.cors import configure_cors
from orion.api.routes import router, get_current_user


class AssistantCapabilityAccessTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method, *, public=True, origin='http://localhost:62523', **headers):
        app = FastAPI()
        configure_cors(app)
        app.include_router(router)

        async def user(authorization: str = Header(default='')):
            if authorization != 'Bearer test-user':
                raise HTTPException(401, 'Login required')
            return 'alice'

        app.dependency_overrides[get_current_user] = user
        app.add_middleware(AccessBoundary, public=public, local_entry=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://127.0.0.1:8910') as client:
            return await client.request(method, '/api/v1/assistant/capabilities',
                                        headers={'Origin': origin, **headers})

    async def test_flutter_preflight_reaches_cors_through_public_boundary(self):
        response = await self.request('OPTIONS', **{
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization,content-type'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:62523')

    async def test_authenticated_capability_and_unauthenticated_error_have_cors(self):
        for headers, status in [({'Authorization': 'Bearer test-user'}, 200), ({}, 401)]:
            response = await self.request('GET', **headers)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:62523')
            if status == 200:
                self.assertEqual(response.json(), {'speech_modes': ['whole', 'adaptive'], 'speech_protocol': 1})

    async def test_untrusted_origin_is_still_rejected(self):
        response = await self.request('OPTIONS', origin='https://evil.example', **{
            'Access-Control-Request-Method': 'GET'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('access-control-allow-origin', response.headers)

    async def test_management_listener_still_refuses_user_capability(self):
        response = await self.request('GET', public=False, Authorization='Bearer test-user')
        self.assertEqual(response.status_code, 403)
