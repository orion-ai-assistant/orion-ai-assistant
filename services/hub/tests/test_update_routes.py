"""Normal phone sessions see status but cannot control the PC updater."""
from unittest.mock import patch
import unittest
import unittest.mock

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
        # Error paths log to <registry>/updates: never to the developer's real installation.
        self.log = self.enterContext(patch('services.shared.update_log.log_exception'))
        self.enterContext(patch.object(api, 'Registry'))

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
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['error'], 'ValueError')  # Not "busy": that would spin forever.
            self.assertNotIn('busy', response.json())
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


class CheckNowTests(unittest.TestCase):
    def test_check_now_spawns_only_the_fixed_retained_command(self):
        with patch.object(api, 'Registry'), \
                patch.object(api, 'runtime_command', return_value=['py', 'update_automatic.py', '--registry', 'r']), \
                patch.object(api.subprocess, 'Popen') as popen, \
                patch.object(api, 'read_status', return_value={'ok': True}):
            self.assertEqual(CHANGE_PREFERENCES(api.Preferences(check_now=True)), {'ok': True})
            self.assertEqual(popen.call_args.args[0][-2:], ['check', '--now'])
            for bad in ({'check_now': False}, {'check_now': True, 'paused': False}):
                with self.assertRaises(ValueError):
                    CHANGE_PREFERENCES(api.Preferences(**bad))
            popen.assert_called_once()

    def test_failed_check_process_is_recorded_not_swallowed(self):
        process = unittest.mock.Mock()
        process.wait.return_value = 2
        with patch.object(api, 'record_check_failure') as record:
            api._report_failure(process, 'registry')
            record.assert_called_once_with('registry', 2)
            record.reset_mock()
            process.wait.return_value = 0
            api._report_failure(process, 'registry')
            record.assert_not_called()

    def test_check_process_that_cannot_start_is_recorded_for_the_card(self):
        with patch.object(api, 'Registry'),                 patch.object(api, 'runtime_command', return_value=['py']),                 patch.object(api.subprocess, 'Popen', side_effect=OSError('no such file')),                 patch.object(api, 'record_check_failure') as record,                 patch.object(api, 'read_status', return_value={'ok': True}):
            self.assertEqual(CHANGE_PREFERENCES(api.Preferences(check_now=True)), {'ok': True})
            record.assert_called_once()
            self.assertEqual(record.call_args.args[-1], 'CheckProcessNotStarted')
            self.assertEqual(api._requested['at'], 0.0)

    def test_status_reports_checking_during_the_start_grace_period_and_the_last_result(self):
        import time as clock
        registry = unittest.mock.Mock()
        registry.read.return_value = {'preferences': {'start_with_windows': False, 'show_client': True}}
        automatic = unittest.mock.Mock()
        attempt = [100.0]
        automatic.status.side_effect = lambda: {'checking': False, 'last_attempt': attempt[0]}
        automatic.policy.return_value = {'enabled': False}
        with patch.object(api, 'Registry', return_value=registry),                 patch.object(api, 'AutomaticUpdates', return_value=automatic),                 patch.object(api, 'runtime_command', return_value=['x']),                 patch.object(api, 'router_managed', return_value=False),                 patch.object(api.update_now, 'view', return_value=None),                 patch.object(api.update_ui, 'read_result', return_value={'status': 'completed', 'message': 'Güncellendi: hub 1.', 'at': 1, 'seen': False}):
            api._requested['at'] = clock.time()
            status = api.read_status()
            self.assertTrue(status['checking'])  # Pressed, process not yet running.
            self.assertEqual(status['last_result']['message'], 'Güncellendi: hub 1.')
            api._requested['at'] = clock.time() - api._REQUEST_GRACE - 1
            self.assertFalse(api.read_status()['checking'])
            api._requested['at'] = clock.time()
            attempt[0] = clock.time() + 1
            self.assertFalse(api.read_status()['checking'])  # The check already ran and finished.
            api._requested['at'] = 0.0

    def test_activity_follows_journal_and_state_whichever_process_checks(self):
        pick = api.activity
        self.assertEqual(pick({'checking': True, 'pending': None}), 'checking')
        self.assertEqual(pick({'checking': True, 'check_outcome': 'update_found', 'pending': None}), 'found')
        # A manual check that failed does not hide the watcher's download.
        self.assertEqual(pick({'checking': False, 'error': 'CheckProcessFailed',
                               'pending': {'phase': 'downloading'}}), 'downloading')
        self.assertEqual(pick({'checking': True, 'pending': {'phase': 'staged'}}), 'ready')
        self.assertEqual(pick({'checking': False, 'pending': None}), 'idle')

    def test_status_never_fails_while_an_operation_holds_the_state(self):
        from services.shared.process_lifecycle import LifecycleError
        with patch.object(api, 'read_status', side_effect=[{'available': True, 'pending': None}, LifecycleError('busy')]),                 patch('services.shared.update_log.log_exception') as log, patch.object(api, 'Registry'):
            self.assertNotIn('busy', api.read_status_or_last())
            result = api.read_status_or_last()
        self.assertTrue(result['busy'] and result['available'])
        self.assertEqual(log.call_args.args[1], 'status.read')

    def test_check_now_without_retained_runtime_is_refused(self):
        with patch.object(api, 'Registry'), patch.object(api, 'runtime_command', return_value=None), \
                patch.object(api.subprocess, 'Popen') as popen:
            with self.assertRaises(ValueError):
                CHANGE_PREFERENCES(api.Preferences(check_now=True))
            popen.assert_not_called()


class SingleSwitchTests(unittest.TestCase):
    def test_automatic_updates_switch_also_covers_the_local_router(self):
        with patch.object(api, 'Registry'), patch.object(api, 'runtime_command', return_value=['x']),                 patch.object(api, 'router_managed', return_value=True),                 patch.object(api, 'AutomaticUpdates') as automatic, patch.object(api, 'read_status', return_value={}):
            CHANGE_PREFERENCES(api.Preferences(automatic_updates=True))
        scopes = [call.kwargs.get('scope', 'hub') for call in automatic.call_args_list]
        self.assertEqual(scopes, ['hub', 'router'])
        for instance in automatic.return_value.preferences.call_args_list:
            self.assertEqual(instance.kwargs['enabled'], True)

    def test_without_a_managed_router_only_the_hub_scope_changes(self):
        with patch.object(api, 'Registry'), patch.object(api, 'runtime_command', return_value=['x']),                 patch.object(api, 'router_managed', return_value=False),                 patch.object(api, 'AutomaticUpdates') as automatic, patch.object(api, 'read_status', return_value={}):
            CHANGE_PREFERENCES(api.Preferences(automatic_updates=False))
        self.assertEqual(automatic.call_count, 1)
