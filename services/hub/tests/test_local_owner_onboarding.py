import os
import asyncio
import httpx
from fastapi import FastAPI
from orion.api.cors import configure_cors
from orion.api.access_boundary import AccessBoundary
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orion.api.access_boundary import local_owner_web_origin
from orion.api.security import valid_admin_key, migrate_legacy_owner_default, admin_credential_fingerprint


class LocalOwnerOnboardingTests(unittest.TestCase):
    def test_only_same_loopback_hostname_and_selected_owner_routes_allow_local_web(self):
        scope = {'scheme': 'http', 'client': ('127.0.0.1', 12),
                 'headers': [(b'host', b'localhost:8910'), (b'origin', b'http://localhost:50376'), (b'sec-fetch-site', b'same-site')]}
        self.assertEqual(local_owner_web_origin(scope, '/api/v1/owner/secure/pairing'), 'http://localhost:50376')
        self.assertIsNone(local_owner_web_origin(scope, '/api/v1/owner/secure/routers'))
        for origin in [b'https://evil.example', b'http://localhost.evil.example', b'http://127.0.0.1:50376', b'null']:
            scope['headers'][1] = (b'origin', origin)
            self.assertIsNone(local_owner_web_origin(scope, '/api/v1/owner/login'))
        scope['headers'][1] = (b'origin', b'http://localhost:50376')
        scope['client'] = ('192.168.1.2', 12)
        self.assertIsNone(local_owner_web_origin(scope, '/api/v1/owner/login'))

    def test_credentialed_headers_replace_generic_cors_headers(self):
        async def check():
            app = FastAPI()
            configure_cors(app)
            @app.post('/api/v1/owner/login')
            async def fake_login():
                return {'ok':True}
            app.add_middleware(AccessBoundary)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1',12)), base_url='http://localhost:8910') as client:
                headers = {'Origin':'http://localhost:50376', 'Sec-Fetch-Site':'same-site'}
                response = await client.post('/api/v1/owner/login', headers=headers, json={})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers.get_list('access-control-allow-origin'), ['http://localhost:50376'])
                self.assertEqual(response.headers.get_list('access-control-allow-credentials'), ['true'])
                response = await client.options('/api/v1/owner/login', headers={**headers,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
                self.assertEqual(response.headers.get_list('access-control-allow-credentials'), ['true'])
                response = await client.post('/api/v1/owner/login', headers={'Origin':'https://evil.example'}, json={})
                self.assertEqual(response.status_code,403)
        asyncio.run(check())

    def test_explicit_legacy_default_migration_invalidates_old_key_and_sessions(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.dict(os.environ, {'ORION_SECURITY_DIR':directory, 'ADMIN_API_KEY':'orionadmin'}):
            old = admin_credential_fingerprint()
            self.assertTrue(migrate_legacy_owner_default())
            self.assertTrue(valid_admin_key('orion'))
            self.assertFalse(valid_admin_key('orionadmin'))
            self.assertNotEqual(old, admin_credential_fingerprint())
            self.assertFalse(migrate_legacy_owner_default())

    def test_migration_preserves_custom_password(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.dict(os.environ, {'ORION_SECURITY_DIR':directory, 'ADMIN_API_KEY':'custom-owner-key'}):
            self.assertFalse(migrate_legacy_owner_default())
            self.assertTrue(valid_admin_key('custom-owner-key'))
            self.assertFalse(valid_admin_key('orion'))
