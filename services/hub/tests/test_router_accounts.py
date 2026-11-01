import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from orion.api import router_providers
from orion.api.auth_routes import get_current_user
from orion.contracts.settings import RuntimeSettings
from orion.kernel import router_accounts as accounts

HUB = '00000000-0000-4000-8000-000000000001'
ROUTER = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'
RECORD = {'id': ROUTER, 'name': 'Router', 'url': 'https://127.0.0.1:9443', 'fp': 'a' * 64}


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_catalog_never_reuses_permission_cache_or_admin_credentials(self):
        from orion.kernel.router_models import get_account_catalog
        permitted = [{'name': 'allowed', 'provider': 'openai', 'capability': 'chat'}]
        async def result(record, method, path, **kwargs):
            return {'models': list(permitted)} if path.endswith('/models') else {'groups': []}
        with patch.object(accounts, 'selected_router', AsyncMock(return_value=RECORD)), patch.object(
            accounts, 'ensure_account', AsyncMock(return_value='account-fixture')) as ensure, patch.object(
            accounts, 'request_router', AsyncMock(side_effect=result)) as request:
            first = await get_account_catalog('user', False)
            permitted.clear()
            second = await get_account_catalog('user', False)
        self.assertEqual(first['models'][0]['name'], 'allowed')
        self.assertEqual(second['models'], [])
        self.assertEqual(request.await_count, 4)
        self.assertEqual(ensure.await_count, 2)
        for call in request.await_args_list:
            self.assertEqual(call.kwargs['account_key'], 'account-fixture')
            self.assertNotIn('admin_password', call.kwargs)

    def test_credentials_are_scoped_to_hub_router_and_account(self):
        with patch.object(accounts, '_material', return_value=(b'a' * 32, {'id': HUB})):
            first = accounts._credential(ROUTER, 'account', HUB)
            self.assertEqual(first, accounts._credential(ROUTER, 'account', HUB))
            self.assertNotEqual(first, accounts._credential(OTHER, 'account', HUB))
            self.assertNotEqual(first, accounts._credential(ROUTER, 'account', OTHER))
            self.assertNotEqual(first, accounts._credential(ROUTER, 'hub'))
        with patch.object(accounts, '_material', return_value=(b'a' * 32, {'id': OTHER})):
            self.assertNotEqual(first, accounts._credential(ROUTER, 'account', HUB))

    async def test_recreated_username_gets_a_new_subject(self):
        with patch.object(accounts, 'tls_identity', return_value={'id': HUB}), patch(
            'orion.kernel.registry.get_user_by_username', AsyncMock(side_effect=[
                {'id': 'user', 'created_at': '2026-01-01'},
                {'id': 'user', 'created_at': '2026-02-01'}])):
            self.assertNotEqual(await accounts.account_identity('user'), await accounts.account_identity('user'))

    async def test_provisioning_retries_same_key_after_lost_response_and_stops_after_success(self):
        request = AsyncMock(side_effect=[accounts.RouterAccountError('offline'), {'id': 'key-id'}])
        states = {}
        def state(router, subject=None):
            return states.get(subject)
        def write(path, data):
            states[HUB] = json.loads(data)
        with patch.object(accounts, 'account_identity', AsyncMock(return_value=(HUB, 'user'))), patch.object(
            accounts, '_credential', return_value='sk-orion-fixture'), patch.object(accounts, '_state', side_effect=state), patch.object(
            accounts, 'ensure_connection', AsyncMock()), patch.object(accounts, 'request_router', request), patch.object(
            accounts, 'atomic_write', side_effect=write):
            with self.assertRaises(accounts.RouterAccountError):
                await accounts.ensure_account('user', RECORD)
            self.assertEqual(await accounts.ensure_account('user', RECORD), 'sk-orion-fixture')
            await accounts.ensure_account('user', RECORD)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args_list[0], request.await_args_list[1])
        self.assertNotIn('key', states[HUB])

    async def test_failed_provisioning_does_not_use_shared_admin_key(self):
        with patch.object(accounts, 'selected_router', AsyncMock(return_value=RECORD)), patch.object(
            accounts, 'account_identity', AsyncMock(return_value=(HUB, 'user'))), patch.object(
            accounts, '_credential', return_value='sk-orion-personal'), patch.object(
            accounts, 'provision_if_available', AsyncMock()):
            settings = await accounts.account_settings(RuntimeSettings(router_api_key='admin-secret'), 'user')
        self.assertEqual(settings.router_api_key, 'sk-orion-personal')

    async def test_missing_trust_for_selected_router_does_not_restore_admin_credentials(self):
        with patch.object(accounts, 'selected_router', AsyncMock(side_effect=accounts.RouterAccountError('revoked'))), patch.object(
            accounts, 'read_connection', return_value=RECORD), patch.object(
            accounts, 'account_identity', AsyncMock(return_value=(HUB, 'user'))), patch.object(
            accounts, '_credential', return_value='sk-orion-personal'), patch.object(
            accounts, 'provision_if_available', AsyncMock()):
            settings = await accounts.account_settings(RuntimeSettings(router_api_key='admin-secret'), 'user')
        self.assertEqual(settings.router_api_key, 'sk-orion-personal')

    async def test_same_pc_connection_is_automatic(self):
        with patch.object(accounts, '_state', return_value=None), patch.object(accounts, 'connect_router', AsyncMock()) as connect:
            await accounts.ensure_connection(RECORD)
        connect.assert_awaited_once_with(RECORD)

    def enroll(self, *, local_token=None):
        return (patch.object(accounts, 'tls_identity', return_value={'id': HUB, 'name': 'PC'}),
                patch.object(accounts, '_credential', return_value='hub-orion-fixture'),
                patch.object(accounts, 'local_enrollment_token', return_value=local_token),
                patch.object(accounts, 'request_router', AsyncMock(return_value={'connected': True})),
                patch.object(accounts, 'atomic_write'))

    async def test_connection_code_is_only_used_for_enrollment(self):
        identity, credential, local, request, write = self.enroll()
        with identity, credential, local, request as request, write as write:
            await accounts.connect_router(RECORD, 'ABCD-EFGH-JKMN')
        self.assertEqual(request.await_args.kwargs['enrollment'], {'x-orion-enrollment-code': 'ABCD-EFGH-JKMN'})
        self.assertNotIn('ABCD', str(write.call_args))

    async def test_same_pc_token_admits_without_a_code(self):
        identity, credential, local, request, write = self.enroll(local_token='t' * 64)
        with identity, credential, local, request as request, write:
            await accounts.connect_router(RECORD)
        self.assertEqual(request.await_args.kwargs['enrollment'], {'x-orion-local-enrollment': 't' * 64})

    async def test_router_password_is_never_sent(self):
        identity, credential, local, request, write = self.enroll()
        with identity, credential, local, request as request, write as write:
            with self.assertRaises(accounts.EnrollmentRequired):
                await accounts.connect_router(RECORD)
        request.assert_not_awaited()
        write.assert_not_called()


class ProviderRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_selected_router_does_not_hide_other_connected_routers(self):
        other = {**RECORD, 'id': OTHER, 'name': 'Other Router'}
        with patch.object(router_providers, 'account', AsyncMock(return_value=(RECORD, 'first-account'))), patch.object(
            router_providers, 'SecurityStore', return_value=SimpleNamespace(routers=lambda: [RECORD, other])), patch.object(
            router_providers, '_state', return_value={'connected': True}), patch.object(
            router_providers, 'ensure_account', AsyncMock(return_value='other-account')), patch.object(
            router_providers, 'request_router', AsyncMock(side_effect=[accounts.RouterAccountError('offline'), {'active_keys': 2}])):
            response = await self.client.get('/api/v1/router/providers')
        self.assertTrue(response.json()['connected'])
        self.assertEqual(response.json()['router_id'], OTHER)
        self.assertEqual(response.json()['active_keys'], 2)
        self.assertFalse(response.json()['routers'][0]['connected'])

    async def test_multiple_routers_use_authenticated_account_and_aggregate_counts(self):
        records = [RECORD, {**RECORD, 'id': OTHER, 'name': 'Other Router'}]
        remote = AsyncMock(side_effect=[{'summary': [{'provider': 'openai', 'shared': 2, 'private': 1}], 'active_keys': 3},
                                       {'summary': [{'provider': 'openai', 'shared': 0, 'private': 2}], 'active_keys': 2}])
        with patch.object(router_providers, 'account', AsyncMock(return_value=(RECORD, 'first-account'))), patch.object(
            router_providers, 'SecurityStore', return_value=SimpleNamespace(routers=lambda: records)), patch.object(
            router_providers, '_state', return_value={'connected': True}), patch.object(
            router_providers, 'ensure_account', AsyncMock(return_value='other-account')) as ensure, patch.object(
            router_providers, 'request_router', remote):
            response = await self.client.get('/api/v1/router/providers?user_id=someone-else')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['active_keys'], 5)
        self.assertEqual(response.json()['active_routers'], 2)
        self.assertEqual(len(response.json()['routers']), 2)
        ensure.assert_awaited_once_with('user', records[1])
        self.assertEqual(remote.await_args_list[1].kwargs['account_key'], 'other-account')
        self.assertNotIn('account', response.text)

    async def test_invalid_credential_does_not_echo_secret_in_validation_error(self):
        secret = 'secret-fixture-' * 700
        response = await self.client.put('/api/v1/router/providers/openrouter', json={'api_key': secret, 'router_id': ROUTER})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret-fixture', response.text)

    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(router_providers.router)
        self.app.dependency_overrides[get_current_user] = lambda: 'user'
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='https://hub')
        self.addAsyncCleanup(self.client.aclose)

    async def test_provider_write_uses_account_key_and_never_echoes_provider_secret(self):
        with patch.object(router_providers, 'account', AsyncMock(return_value=(RECORD, 'sk-orion-user'))), patch.object(
            router_providers, 'request_router', AsyncMock(return_value={'saved': True})) as remote, patch(
            'orion.api.login_limits.limit_attempts', AsyncMock()):
            response = await self.client.put('/api/v1/router/providers/openrouter', json={
                'api_key': 'provider-secret', 'router_id': ROUTER, 'user_id': 'other'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('provider-secret', response.text)
        self.assertEqual(remote.await_args.kwargs['account_key'], 'sk-orion-user')
        self.assertEqual(remote.await_args.kwargs['body'], {'api_key': 'provider-secret'})

    async def test_router_change_rejects_stale_save_and_delete(self):
        with patch.object(router_providers, 'account', AsyncMock(return_value=({**RECORD, 'id': OTHER}, 'key'))), patch.object(
            router_providers, 'request_router', AsyncMock()) as remote, patch(
            'orion.api.login_limits.limit_attempts', AsyncMock()):
            save = await self.client.put('/api/v1/router/providers/openrouter', json={'api_key': 'secret', 'router_id': ROUTER})
            remove = await self.client.delete('/api/v1/router/providers/openrouter', params={'router_id': ROUTER})
        self.assertEqual(save.status_code, 409)
        self.assertEqual(remove.status_code, 409)
        remote.assert_not_awaited()

    async def test_provider_paths_cannot_escape_to_admin_api(self):
        with patch('orion.api.login_limits.limit_attempts', AsyncMock()), patch.object(router_providers, 'account', AsyncMock()) as account:
            response = await self.client.put('/api/v1/router/providers/a.b', json={'api_key': 'secret', 'router_id': ROUTER})
        self.assertEqual(response.status_code, 422)
        account.assert_not_awaited()

    async def test_missing_router_returns_actionable_status(self):
        with patch.object(router_providers, 'account', AsyncMock(side_effect=accounts.RouterAccountError('Orion Panel’den Router bağlayın.'))):
            result = await self.client.get('/api/v1/router/providers')
        self.assertFalse(result.json()['connected'])
        self.assertTrue(result.json()['managed'])

    async def test_unfinished_connection_is_directed_to_the_hub_owner(self):
        with patch.object(router_providers, 'account', AsyncMock(side_effect=accounts.EnrollmentRequired())):
            result = await self.client.get('/api/v1/router/providers')
        self.assertIn('Hub sahibi', result.json()['message'])
        self.assertNotIn('şifre', result.json()['message'])

    def test_provider_routes_are_available_to_mobile_but_connection_management_is_private(self):
        from orion.api.access_boundary import public_path
        self.assertTrue(public_path('/api/v1/router/providers'))
        self.assertTrue(public_path('/api/v1/router/providers/openrouter'))
        self.assertFalse(public_path('/api/v1/owner/router/access'))

    async def test_no_authentication_cannot_manage_providers(self):
        self.app.dependency_overrides.clear()
        result = await self.client.get('/api/v1/router/providers')
        self.assertEqual(result.status_code, 401)

    async def test_code_for_another_router_is_never_used_to_enroll(self):
        from fastapi import HTTPException
        from orion.api import router_connection
        manager = SimpleNamespace(lock=asyncio.Lock())
        request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))
        with patch.object(router_connection, 'association', return_value=manager), patch(
            'orion.api.login_limits.limit_attempts', AsyncMock()), patch.object(
            router_connection, '_listed_router', AsyncMock(return_value={**RECORD, 'url': 'https://router.test'})), patch(
            'orion.kernel.pinned_router.prove_router', AsyncMock(return_value={'id': OTHER, 'fp': 'a' * 64})), patch.object(
            accounts, 'connect_router', AsyncMock()) as connect:
            with self.assertRaises(HTTPException) as failure:
                await router_connection.select(router_connection.Selection(id=ROUTER, code='ABCD-EFGH-JKMN'), request)
        self.assertEqual(failure.exception.status_code, 409)
        connect.assert_not_awaited()

    async def test_a_removed_hub_forgets_its_admission(self):
        response = AsyncMock(status=401)
        response.__aenter__.return_value = response
        session = AsyncMock()
        session.request = lambda *args, **kwargs: response
        session.__aenter__.return_value = session
        with patch.object(accounts, '_credential', return_value='hub-orion-fixture'), patch.object(
            accounts, 'router_session', return_value=session), patch.object(accounts, '_state_path') as path:
            with self.assertRaises(accounts.EnrollmentRequired):
                await accounts.request_router(RECORD, 'GET', '/api/v1/hubs/status')
        path.return_value.unlink.assert_called_once_with(missing_ok=True)
