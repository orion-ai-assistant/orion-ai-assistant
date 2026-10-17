"""Normal phone sessions see status but cannot control the PC updater."""
from unittest.mock import patch
import unittest

from fastapi import FastAPI
import httpx

from orion.api import update_routes as api
from orion.api.auth_routes import get_current_user
from orion.api.access_boundary import AccessBoundary

CHANGE_PREFERENCES = api.change_preferences


class UpdateRoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.state.mobile = False
        self.app.include_router(api.router)
        self.app.dependency_overrides[get_current_user] = lambda: 'alice'
        self.app.add_middleware(AccessBoundary, public=True)
        self.enterContext(patch('orion.api.discovery.lan_addresses', return_value=['192.168.1.10']))
        self.read = self.enterContext(patch.object(api, 'read_status', return_value={'available': True, 'pending': None}))
        self.change = self.enterContext(patch.object(api, 'change_preferences', return_value={'saved': True}))

    def client(self, peer='127.0.0.1', host='127.0.0.1', **kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=(peer, 1234)),
                                base_url='https://' + host, **kwargs)

    async def test_phone_status_allowed_but_normal_account_cannot_change_preferences(self):
        async with self.client('192.168.1.44', '192.168.1.10') as client:
            response = await client.get('/api/v1/updates')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['can_manage'])
            for body in ({'automatic_updates': True}, {'defer_hours': 24}, {'paused': False},
                         {'start_with_windows': True}, {'show_client': True}, {'router_automatic_updates': True}):
                self.assertEqual((await client.put('/api/v1/updates', json=body)).status_code, 403)
        self.change.assert_not_called()

    async def test_local_browser_can_change_fixed_preferences_only(self):
        async with self.client(headers={'Origin': 'http://127.0.0.1:8090'}) as client:
            self.assertEqual((await client.get('/api/v1/updates')).status_code, 200)
            self.assertEqual((await client.put('/api/v1/updates', json={'automatic_updates': False})).status_code, 200)
            self.assertEqual((await client.put('/api/v1/updates', json={'metadata_url': 'https://evil.example/'})).status_code, 422)
            self.assertEqual((await client.put('/api/v1/updates', json={'automatic_updates': 'true'})).status_code, 422)
            self.assertEqual((await client.put('/api/v1/updates', json={'defer_hours': 1})).status_code, 422)
        self.assertEqual(self.change.call_count, 1)

    async def test_forwarded_origin_and_mobile_listener_cannot_grant_authority(self):
        for headers in ({'X-Forwarded-For': '127.0.0.1'}, {'Origin': 'https://evil.example'},
                        {'Origin': 'http://127.0.0.1:6666'}, {'Sec-Fetch-Site': 'cross-site'}):
            async with self.client(headers=headers) as client:
                self.assertEqual((await client.put('/api/v1/updates', json={'automatic_updates': True})).status_code, 403)
        self.app.state.mobile = True
        async with self.client() as client:
            self.assertEqual((await client.put('/api/v1/updates', json={'paused': True})).status_code, 403)
        self.change.assert_not_called()

    async def test_disconnected_or_broken_status_returns_no_guessed_success(self):
        self.read.side_effect = ValueError('private-path-secret')
        async with self.client() as client:
            response = await client.get('/api/v1/updates')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private-path-secret', response.text)

    async def test_authentication_is_still_required(self):
        self.app.dependency_overrides.clear()
        async with self.client() as client:
            self.assertEqual((await client.get('/api/v1/updates')).status_code, 401)
        self.read.assert_not_called()

    def test_windows_preferences_use_explicit_local_adapter_without_changing_update_preference(self):
        from unittest.mock import Mock
        registry = Mock()
        registry.read.return_value = {'preferences': {'start_with_windows': False, 'show_client': True, 'automatic_updates': False}}
        with patch.object(api, 'Registry', return_value=registry), \
             patch('services.shared.launcher.preferences') as preferences:
            CHANGE_PREFERENCES(api.Preferences(start_with_windows=True))
            preferences.assert_called_once_with(registry, start_with_windows=True, show_client=True)
            self.assertFalse(registry.read()['preferences']['automatic_updates'])

    def test_mixed_independent_preference_groups_are_rejected_before_mutation(self):
        with patch.object(api, 'AutomaticUpdates') as updates:
            with self.assertRaises(ValueError):
                CHANGE_PREFERENCES(api.Preferences(start_with_windows=True, automatic_updates=True))
            updates.assert_not_called()
