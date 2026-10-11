import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Header, HTTPException
from orion.api.access_boundary import AccessBoundary
from orion.api.cors import configure_cors
from orion.api.https_boundary import LocalTransportBoundary
from orion.api.pc_audio_routes import router, get_current_user


class PcAudioAccessTests(unittest.IsolatedAsyncioTestCase):
    def app(self, local_entry=True):
        app = FastAPI()
        app.state.public = True
        app.state.local_entry = local_entry
        configure_cors(app)
        app.include_router(router)
        async def user(authorization: str = Header(default='')):
            if authorization != 'Bearer test-user':
                raise HTTPException(401, 'Login required')
            return 'alice'
        app.dependency_overrides[get_current_user] = user
        app.add_middleware(AccessBoundary, public=True, local_entry=local_entry)
        app.add_middleware(LocalTransportBoundary)
        return app

    async def request(self, method, path='/api/v1/pc-audio', *, origin='http://localhost:64142',
                      client=('127.0.0.1', 1234), local_entry=True, **headers):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app(local_entry), client=client),
                                     base_url='http://127.0.0.1:8910') as api:
            with patch('orion.api.pc_audio_routes.os', SimpleNamespace(name='nt')):
                return await api.request(method, path, headers={'Origin': origin, **headers})

    async def test_flutter_preflight_passes_boundary_for_all_pc_audio_routes(self):
        for path, method in [('/api/v1/pc-audio', 'GET'),
                             *[(f'/api/v1/pc-audio/{action}', 'POST') for action in ('acquire', 'renew', 'release')]]:
            result = await self.request('OPTIONS', path, **{
                'Access-Control-Request-Method': method,
                'Access-Control-Request-Headers': 'authorization,content-type'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers['access-control-allow-origin'], 'http://localhost:64142')

    async def test_local_user_capability_available_and_login_still_required(self):
        result = await self.request('GET', Authorization='Bearer test-user')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['supported'])
        result = await self.request('GET')
        self.assertEqual(result.status_code, 401)
        self.assertIn('access-control-allow-origin', result.headers)

    async def test_remote_peers_origins_and_forwarded_requests_rejected(self):
        for args in [dict(client=('192.168.1.20', 1234)), dict(origin='https://evil.example'),
                     dict(origin='http://localhost.evil.example:1234'), dict(origin='null'),
                     {'X-Forwarded-For': '127.0.0.1'}]:
            result = await self.request('GET', Authorization='Bearer test-user', **args)
            self.assertEqual(result.status_code, 403)

    async def test_nonlocal_listener_does_not_expose_pc_audio(self):
        result = await self.request('GET', local_entry=False, Authorization='Bearer test-user')
        self.assertNotEqual(result.status_code, 200)

    async def test_browser_owner_routes_keep_same_origin_requirement(self):
        result = await self.request('OPTIONS', '/api/v1/browser', **{'Access-Control-Request-Method': 'POST'})
        self.assertEqual(result.status_code, 403)
