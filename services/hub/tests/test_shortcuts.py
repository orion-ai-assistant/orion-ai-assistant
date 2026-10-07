import unittest
from unittest.mock import patch, AsyncMock

import httpx
from orion.api.main import local_app, public_app


class LocalEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_browser_uses_account_authority_not_owner_login(self):
        from orion.api.auth_routes import get_current_user
        async def user():
            return 'alice'
        local_app.dependency_overrides[get_current_user] = user
        manager = AsyncMock()
        manager.status.return_value = {'connected': True}
        try:
            with patch('orion.api.browser_routes.BrowserManager', return_value=manager), patch('orion.api.browser_routes.require_local'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
                    response = await client.get('/api/v1/browser', headers={'Authorization':'Bearer account', 'Origin':'http://localhost', 'Cookie':'orion_owner_local=old'})
                    self.assertEqual(response.status_code, 200)
                    manager.status.assert_awaited_once()
                    rejected = await client.get('/api/v1/browser', headers={'Authorization':'Bearer account', 'Origin':'http://attacker.example'})
                    self.assertEqual(rejected.status_code, 403)
        finally:
            local_app.dependency_overrides.pop(get_current_user)

    async def test_manage_trailing_punctuation_redirects_only_on_loopback(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost', follow_redirects=True) as client:
            self.assertEqual((await client.get('/manage.')).status_code, 200)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('192.168.1.20', 1234)), base_url='http://localhost') as client:
            self.assertEqual((await client.get('/manage.')).status_code, 403)

    async def test_unknown_local_addresses_are_not_private_access_errors(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            for path in ['/deneme', '/8910']:
                response = await client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('yalnızca bu PC', response.text)
            self.assertIn('http://localhost:8910/manage', (await client.get('/8910')).json()['detail'])

    async def test_account_settings_schema_uses_public_auth_and_cors(self):
        from orion.api.auth_routes import get_current_user
        async def user():
            return 'alice'
        local_app.dependency_overrides[get_current_user] = user
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8910') as client:
                response = await client.get('/api/v1/admin/settings/schema', headers={'Authorization': 'Bearer account', 'Origin': 'http://localhost:8021'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:8021')
                self.assertIsInstance(response.json(), list)
                mixed = await client.get('/api/v1/admin/settings/schema', headers={'Authorization': 'Bearer account', 'Cookie': 'orion_owner_local=expired-owner-session', 'Origin': 'http://localhost:8021'})
                self.assertEqual(mixed.status_code, 200)
                owner = await client.get('/panel/api/v1/admin/settings/schema', headers={'Origin': 'http://localhost:8021'})
                self.assertEqual(owner.status_code, 403)
        finally:
            local_app.dependency_overrides.pop(get_current_user)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8910') as client:
            response = await client.get('/api/v1/admin/settings/schema', headers={'Origin': 'http://localhost:8021'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:8021')

    async def test_http_manage_is_real_html_and_directory_keeps_lan_https(self):
        with patch('orion.api.hub_directory.local_identity', return_value={'id': 'self', 'name': 'PC'}), patch.object(local_app.state.hub_directory, 'list', AsyncMock(return_value=[{'id': 'remote', 'name': 'Remote', 'url': 'https://192.168.1.20:8911/'}])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8910') as client:
                page = await client.get('/manage')
                self.assertEqual(page.status_code, 200)
                self.assertNotIn('location', page.headers)
                self.assertIn('Kişiler ve davetler', page.text)
                for path in ['/owner-assets/gate.js', '/router/', '/admin/']:
                    self.assertEqual((await client.get(path)).status_code, 200, path)
                hubs = (await client.get('/api/v1/discovery/hubs')).json()['hubs']
                self.assertEqual(hubs[0]['url'], 'http://127.0.0.1:8910/hub')
                self.assertEqual(hubs[1]['url'], 'https://192.168.1.20:8911/')

    async def test_http_local_exception_never_accepts_native_credentials(self):
        from orion.api.security import create_access_token
        jwt = create_access_token({'sub': 'alice', 'device_id': 'device'})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8910') as client:
            for headers in [{'X-Device-Token': 'orion_device_example'}, {'Authorization': 'Bearer ' + jwt}]:
                for path in ['/', '/manage', '/hub', '/panel', '/api/v1/discovery/hubs']:
                    self.assertEqual((await client.get(path, headers=headers)).status_code, 401)

    async def test_http_rejects_docker_private_peer_override(self):
        with patch.dict('os.environ', {'ORION_PRIVATE_DOCKER_LISTENER': 'true'}), patch('orion.api.access_boundary.os.path.exists', return_value=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('172.18.0.2', 1234)), base_url='http://localhost') as client:
                self.assertEqual((await client.get('/manage')).status_code, 403)

    async def test_http_owner_cookie_is_local_only_and_csrf_protected(self):
        with patch.object(local_app.state.panel_app.state, 'redis', AsyncMock(), create=True), patch('orion.api.owner_routes.limit_attempts', AsyncMock()), patch('orion.api.owner_routes.valid_admin_key', return_value=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8910') as client:
                payload = {'password': 'test', 'scope': 'admin'}
                denied = await client.post('/api/v1/owner/login', json=payload, headers={'Origin': 'https://evil.example'})
                self.assertEqual(denied.status_code, 403)
                response = await client.post('/api/v1/owner/login', json=payload)
                self.assertEqual(response.status_code, 200)
                cookie = response.headers['set-cookie']
                self.assertIn('orion_owner_local=', cookie)
                self.assertIn('HttpOnly', cookie)
                self.assertIn('SameSite=strict', cookie)
                self.assertNotIn('Secure', cookie)

    async def test_local_landing_chat_panel_and_assets(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            home = await client.get('/')
            self.assertEqual(home.status_code, 200)
            self.assertIn('href="/hub"', home.text)
            self.assertIn('href="/panel"', home.text)
            self.assertNotIn('auth-modal', home.text)
            for path in ['/hub', '/hub/']:
                page = await client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn('auth-modal show', page.text)
                self.assertRegex(page.text, r'src="/js/hub_picker\.js\?v=\d+"')
                self.assertIn('href="/css/styles.css', page.text)
            for path in ['/panel', '/panel/', '/panel/manage/', '/panel/admin/']:
                page = await client.get(path)
                self.assertEqual(page.status_code, 200, path)
                self.assertNotIn('location', page.headers)
                self.assertIn('href="/panel/"', page.text)
                self.assertNotIn('orion.local', page.text)
                self.assertNotIn(':8909', page.text)
            for path in ['/js/main.js', '/css/styles.css', '/favicon.ico']:
                self.assertEqual((await client.get(path)).status_code, 200, path)
            self.assertEqual((await client.get('/api/v1/owner/people')).status_code, 401)
            self.assertEqual((await client.get('/panel/api/v1/owner/people')).status_code, 401)
        self.assertFalse(local_app.state.discovery_enabled)

    async def test_entire_launcher_requires_real_loopback_and_local_host(self):
        for peer, host, headers in [
            ('192.168.1.100', 'localhost', {}),
            ('127.0.0.1', 'attacker.example', {}),
            ('127.0.0.1', 'localhost', {'X-Forwarded-For': '127.0.0.1'}),
        ]:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=(peer, 1234)), base_url='http://' + host) as client:
                for path in ['/', '/hub', '/panel', '/js/main.js', '/api/v1/discovery/hubs']:
                    self.assertEqual((await client.get(path, headers=headers)).status_code, 403, path)

    async def test_removed_aliases_never_redirect_or_serve_login(self):
        for host in ['orion.local', 'orionpanel', 'orionpanel.local', 'orionadmin.local', 'orionrouter.local', 'orionpanel.localhost']:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://' + host) as client:
                response = await client.get('/')
                self.assertEqual(response.status_code, 403, host)
                self.assertNotIn('location', response.headers)
                self.assertNotIn('auth-password', response.text)

    async def test_bookmark_redirect_keeps_query_on_local_chat(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost', follow_redirects=True) as client:
            self.assertEqual(str((await client.get('/dashboard/?hub=old')).url), 'http://localhost/hub?hub=old')
            self.assertEqual(str((await client.get('/dashboard/js/main.js?v=1')).url), 'http://localhost/js/main.js?v=1')

    async def test_lan_ip_dashboard_remains_available_but_not_owner_panel(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app, client=('192.168.1.100', 1234)), base_url='https://192.168.1.107:8911') as client:
            self.assertEqual((await client.get('/')).status_code, 200)
            for path in ['/panel', '/manage/', '/admin/', '/api/v1/owner/people', '/api/v1/peers/test/auth/login']:
                self.assertEqual((await client.get(path)).status_code, 403, path)
            self.assertEqual((await client.get('/api/v1/discovery/connection')).status_code, 403)

    async def test_connection_shows_current_lan_urls_only(self):
        with patch('orion.api.hub_directory.lan_addresses', return_value=['192.168.1.107']), \
             patch('orion.api.hub_directory.local_identity', return_value={'id': 'self', 'name': 'Hiro'}), \
             patch.dict('os.environ', {'ORION_PUBLIC_PORT': '8910'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
                data = (await client.get('/api/v1/discovery/connection')).json()
                self.assertEqual(data['urls'], ['https://192.168.1.107:8911/'])
                self.assertTrue(data['encrypted'])

    async def test_router_shortcut_targets_explicit_local_loopback_http(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            for path in ['/orionrouter', '/orionrouter/']:
                response = await client.get(path)
                self.assertEqual(response.status_code, 307)
                self.assertEqual(response.headers['location'], 'http://127.0.0.1:20128/dashboard')
            panel = await client.get('/panel/manage/security')
            self.assertEqual(panel.status_code, 200)
            self.assertIn('/panel/owner-assets/base.js', panel.text)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=('127.0.0.1', 1234)), base_url='http://localhost') as client:
            for path in ['/', '/orionrouter', '/panel', '/hub']:
                self.assertIn((await client.get(path)).status_code, {200, 307})
