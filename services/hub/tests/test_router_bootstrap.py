"""Fresh native installer bootstrap, with real TLS and no network TOFU."""
import json
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from orion.kernel.local_security import SecurityStore, tls_identity
from orion.kernel.router_bootstrap import bootstrap_local_router, register_local_router_installation


class RouterBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'ORION_SECURITY_DIR': str(self.root / 'hub-security'), 'LOCALAPPDATA': str(self.root / 'user-data'), 'ORION_ROUTER_CONNECTION_FILE': str(self.root / 'selected.json'), 'ROUTER_HOST': 'router'})
        self.environment.start()
        self.router = self.root / 'router'
        self.router.mkdir()
        (self.router / 'pyproject.toml').write_text('[project]\nname="orion-router"\n')
        self.tls = tls_identity(self.router / 'persistent' / 'tls')
        self.public_cert = self.router / 'persistent' / 'tls' / 'certificate.pem'
        shutil.copyfile(self.tls['cert'], self.public_cert)
        (self.router / 'persistent' / 'mdns-id').write_text(self.tls['id'])
        self.identity = {key: self.tls[key] for key in ('v', 'id', 'name', 'fp')}
        self.requests = 0
        async def identity(request):
            self.requests += 1
            return web.json_response(self.identity)
        app = web.Application()
        app.router.add_get('/api/v1/tls/identity', identity)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.tls['cert'], self.tls['key'])
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0, ssl_context=context)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]
        (self.router / '.env').write_text('ORION_ROUTER_TLS_PORT=' + str(self.port))
        register_local_router_installation(self.router)
        self.store = SecurityStore()

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.environment.stop()
        self.temporary.cleanup()

    async def test_fresh_install_trusts_only_verified_filesystem_identity(self):
        self.assertEqual(self.store.routers(), [])
        result = await bootstrap_local_router()
        self.assertEqual(result['status'], 'trusted')
        self.assertEqual(self.store.routers()[0]['fp'], self.tls['fp'])
        self.assertEqual(self.store.routers()[0]['url'], f'https://127.0.0.1:{self.port}')
        self.assertEqual(self.requests, 1)

    async def test_tampered_public_key_never_sends_http_request(self):
        replacement = tls_identity(self.root / 'replacement')
        shutil.copyfile(replacement['cert'], self.public_cert)
        self.assertEqual((await bootstrap_local_router())['status'], 'untrusted')
        self.assertEqual(self.requests, 0)
        self.assertEqual(self.store.routers(), [])

    async def test_wrong_uuid_endpoint_does_not_create_trust(self):
        self.identity = {**self.identity, 'id': '11111111-1111-4111-8111-111111111111'}
        self.assertEqual((await bootstrap_local_router())['status'], 'untrusted')
        self.assertEqual(self.store.routers(), [])

    async def test_missing_optional_router_does_not_block_startup(self):
        (self.root / 'hub-security' / 'local-router-installation.json').unlink()
        with patch('orion.kernel.router_bootstrap.read_local_router_candidate', return_value=None):
            self.assertEqual((await bootstrap_local_router())['status'], 'missing')
        self.assertEqual(self.store.routers(), [])

    async def test_explicit_remote_host_is_not_replaced_by_local_installation(self):
        with patch.dict(os.environ, {'ROUTER_HOST': '192.0.2.12'}):
            self.assertEqual((await bootstrap_local_router())['status'], 'owner-configured')
        self.assertEqual(self.store.routers(), [])
        self.assertEqual(self.requests, 0)

    async def test_selected_other_uuid_is_not_replaced(self):
        from services.shared.router_connection import write_connection
        write_connection({'mode': 'discovery', 'id': '11111111-1111-4111-8111-111111111111'})
        self.assertEqual((await bootstrap_local_router())['status'], 'owner-configured')
        self.assertEqual(self.requests, 0)

    async def test_explicit_revoke_survives_restart_and_automatic_retry(self):
        self.assertEqual((await bootstrap_local_router())['status'], 'trusted')
        self.store.remove_router(self.tls['id'])
        self.assertEqual((await bootstrap_local_router())['status'], 'untrusted')
        self.assertEqual(SecurityStore().routers(), [])
        self.assertEqual(self.requests, 1)

    async def test_changed_key_cannot_overwrite_prior_bootstrap(self):
        self.assertEqual((await bootstrap_local_router())['status'], 'trusted')
        replacement = tls_identity(self.root / 'replacement')
        shutil.copyfile(replacement['cert'], self.public_cert)
        self.assertEqual((await bootstrap_local_router())['status'], 'untrusted')
        self.assertEqual(self.store.routers()[0]['fp'], self.tls['fp'])
        self.assertEqual(self.requests, 1)

    async def test_registered_identity_paths_must_stay_inside_installation(self):
        record_path = self.root / 'hub-security' / 'local-router-installation.json'
        record = json.loads(record_path.read_text())
        record['certificate_path'] = str(self.root / 'outside.pem')
        record_path.write_text(json.dumps(record))
        self.assertEqual((await bootstrap_local_router())['status'], 'untrusted')
        self.assertEqual(self.requests, 0)
