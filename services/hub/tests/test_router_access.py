"""Account isolation, lazy/offline reconciliation and bounded credential fallback."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from fastapi import FastAPI
import httpx

from orion.api import router_access as api
from orion.api.auth_routes import get_current_user
from orion.kernel import router_accounts as accounts
from orion.kernel import router_access as access
from orion.kernel.pinned_router import RouterSession, RouterConnector, _request_target

HUB = '00000000-0000-4000-8000-000000000001'
RID = '00000000-0000-4000-8000-000000000002'
SUBJECT = '00000000-0000-4000-8000-000000000003'
OTHER = '00000000-0000-4000-8000-000000000004'
RECORD = {'id': RID, 'url': 'https://127.0.0.1:9443', 'fp': 'a' * 64, 'name': 'Router'}
MANAGED = 'sk-orion-' + 'a' * 43
MANUAL = 'sk-orion-' + 'b' * 43


class AccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.enterContext(patch.dict(os.environ, ORION_SECURITY_DIR=str(self.directory)))
        self.enterContext(patch.object(accounts, '_material', return_value=(b'c' * 32, {'id': HUB})))
        access.current_route.set(None)
        self.addCleanup(access.current_route.set, None)

    def test_manual_is_encrypted_per_router_and_subject_and_never_replaces_receipt(self):
        receipt = accounts._state_path(RID, SUBJECT)
        receipt.write_text('{"id":"managed-row"}')
        access.save_manual(RID, SUBJECT, key=MANUAL)
        self.assertNotIn(MANUAL.encode(), access.manual_path(RID, SUBJECT).read_bytes())
        self.assertEqual(MANUAL, access.manual_state(RID, SUBJECT)['key'])
        self.assertIsNone(access.manual_state(OTHER, SUBJECT))
        self.assertIsNone(access.manual_state(RID, OTHER))
        access.save_manual(RID, SUBJECT, enabled=False)
        self.assertFalse(access.manual_state(RID, SUBJECT)['enabled'])
        access.save_manual(RID, SUBJECT, remove=True)
        self.assertEqual({'id': 'managed-row'}, json.loads(receipt.read_text()))

    async def test_disabled_and_removed_override_return_to_same_managed_key(self):
        access.save_manual(RID, SUBJECT, key=MANUAL)
        self.assertEqual(MANUAL, await access.account_key('user', RECORD, SUBJECT, MANAGED))
        access.save_manual(RID, SUBJECT, enabled=False)
        self.assertEqual(MANAGED, await access.account_key('user', RECORD, SUBJECT, MANAGED))
        access.save_manual(RID, SUBJECT, remove=True)
        self.assertEqual(MANAGED, await access.account_key('user', RECORD, SUBJECT, MANAGED))

    def test_corrupt_override_can_be_explicitly_replaced_without_changing_managed_receipt(self):
        access.manual_path(RID, SUBJECT).write_bytes(b'invalid cipher text')
        access.save_manual(RID, SUBJECT, key=MANUAL)
        self.assertEqual(MANUAL, access.manual_state(RID, SUBJECT)['key'])

    async def test_offline_user_database_is_reconciled_without_login_after_router_returns(self):
        with patch('orion.kernel.registry.list_active_users', AsyncMock(return_value=[{'id': 'waiting-user'}])), patch.object(
                accounts, 'selected_router', AsyncMock(return_value=RECORD)), patch.object(
                accounts, 'ensure_connection', AsyncMock()), patch.object(
                accounts, 'request_router', AsyncMock(side_effect=[accounts.RouterAccountError('offline'), {'accounts': []}])), patch.object(
                accounts, 'account_identity', AsyncMock(return_value=(SUBJECT, 'waiting-user'))), patch.object(
                accounts, 'ensure_account', AsyncMock()) as ensure:
            with self.assertRaises(accounts.RouterAccountError):
                await accounts.reconcile_accounts()
            await accounts.reconcile_accounts()
        ensure.assert_awaited_once_with('waiting-user', RECORD, force=True)

    async def test_disabled_remote_account_is_not_recreated_or_reactivated(self):
        with patch('orion.kernel.registry.list_active_users', AsyncMock(return_value=[{'id': 'user'}])), patch.object(
                accounts, 'selected_router', AsyncMock(return_value=RECORD)), patch.object(
                accounts, 'ensure_connection', AsyncMock()), patch.object(accounts, '_state', return_value={'id': 'existing'}), patch.object(
                accounts, 'request_router', AsyncMock(return_value={'accounts': [{'subject_id': SUBJECT, 'is_active': False}]})), patch.object(
                accounts, 'account_identity', AsyncMock(return_value=(SUBJECT, 'user'))), patch.object(accounts, 'ensure_account', AsyncMock()) as ensure:
            await accounts.reconcile_accounts()
        ensure.assert_not_awaited()

    async def test_manual_api_never_returns_secret_and_rejects_old_router_or_global(self):
        app = FastAPI(); app.include_router(api.router)
        app.dependency_overrides[get_current_user] = lambda: 'user'
        with patch.object(accounts, 'selected_router', AsyncMock(return_value=RECORD)), patch.object(
                accounts, 'account_identity', AsyncMock(return_value=(SUBJECT, 'user'))), patch.object(
                accounts, 'provision_if_available', AsyncMock()), patch.object(accounts, '_state', return_value={'id': 'receipt'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://hub') as client:
                self.assertEqual(200, (await client.put('/api/v1/router/access', json={'router_id': RID, 'key': MANUAL})).status_code)
                response = await client.get('/api/v1/router/access')
                self.assertNotIn(MANUAL, response.text)
                self.assertTrue(response.json()['manual_saved'])
                self.assertEqual(409, (await client.delete('/api/v1/router/access?router_id=' + OTHER)).status_code)
                self.assertTrue(access.manual_path(RID, SUBJECT).exists())
                self.assertEqual(422, (await client.put('/api/v1/router/access', json={'router_id': RID, 'key': 'secret-invalid'})).status_code)
                app.dependency_overrides[get_current_user] = lambda: 'global'
                self.assertEqual(403, (await client.put('/api/v1/router/access', json={'router_id': RID, 'key': MANUAL})).status_code)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.route = access.AccountRoute(RECORD, 'user', SUBJECT, MANAGED, MANUAL)
        self.enterContext(patch('orion.kernel.local_security.SecurityStore', return_value=SimpleNamespace(routers=lambda: [RECORD])))
        access.current_route.set(None)
        self.addCleanup(access.current_route.set, None)

    async def test_invalid_manual_falls_back_once_but_budget_revocation_and_5xx_do_not(self):
        for statuses in ([401, 200, 200], [402], [403], [500]):
            replies = [SimpleNamespace(status=value, release=lambda: None) for value in statuses]
            with self.subTest(statuses=statuses), patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=replies)) as send:
                async with RouterSession(account_route=self.route) as session:
                    response = await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={'messages': []})
                self.assertEqual(statuses[-1], response.status)
                self.assertEqual(len(statuses), send.await_count)
                self.assertEqual('Bearer ' + MANUAL, send.await_args_list[0].kwargs['headers']['Authorization'])
                if len(statuses) == 3:
                    self.assertEqual('Bearer ' + MANAGED, send.await_args_list[1].kwargs['headers']['Authorization'])

    async def test_deleted_managed_key_repairs_only_after_hub_delegation_check(self):
        route = access.AccountRoute(RECORD, 'user', SUBJECT, MANAGED)
        responses = [SimpleNamespace(status=value, release=lambda: None) for value in (401, 200, 200)]
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=responses)) as send, patch.object(
                accounts, 'request_router', AsyncMock(return_value={'connected': True})) as check, patch.object(
                accounts, 'ensure_account', AsyncMock()) as ensure:
            async with RouterSession(account_route=route) as session:
                await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={})
            check.assert_awaited_once_with(RECORD, 'GET', '/api/v1/hubs/status')
            ensure.assert_awaited_once_with('user', RECORD, force=True)
            self.assertEqual(3, send.await_count)

    async def test_router_switch_refuses_to_send_old_credential(self):
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock()) as send:
            async with RouterSession(account_route=self.route) as session:
                with self.assertRaises(accounts.RouterAccountError):
                    await session._request('POST', 'https://other.test:9443/v1/chat/completions', json={})
            send.assert_not_awaited()

    async def test_same_address_with_two_trusted_identities_uses_account_pin(self):
        from yarl import URL
        other = {**RECORD, 'id': OTHER, 'fp': 'b' * 64}
        with patch('orion.kernel.pinned_router.SecurityStore', return_value=SimpleNamespace(routers=lambda: [other, RECORD])):
            connector = RouterConnector()
            token = _request_target.set(RECORD)
            try:
                self.assertEqual(RID, connector._router_for(SimpleNamespace(url=URL(RECORD['url'])))['id'])
            finally:
                _request_target.reset(token)
                await connector.close()

    async def test_shared_session_keeps_concurrent_user_router_snapshots_separate(self):
        other = {**RECORD, 'id': OTHER, 'url': 'https://other.test:9443', 'fp': 'b' * 64}
        routes = [self.route, access.AccountRoute(other, 'other-user', OTHER, 'sk-orion-other-managed')]
        calls = []
        async def network(method, url, **kwargs):
            before = _request_target.get()['id']
            await asyncio.sleep(.01)
            self.assertEqual(before, _request_target.get()['id'])
            calls.append((before, kwargs['headers']['Authorization']))
            return SimpleNamespace(status=200, release=lambda: None)
        async def request(session, route):
            token = access.current_route.set(route)
            try:
                await session._request('POST', route.record['url'] + '/v1/chat/completions', json={})
            finally:
                access.current_route.reset(token)
        with patch('orion.kernel.local_security.SecurityStore', return_value=SimpleNamespace(routers=lambda: [RECORD, other])), patch.object(
                aiohttp.ClientSession, '_request', AsyncMock(side_effect=network)):
            async with RouterSession() as session:
                await asyncio.gather(*(request(session, route) for route in routes))
        self.assertEqual(2, calls.count((RID, 'Bearer ' + MANUAL)))
        self.assertEqual(2, calls.count((OTHER, 'Bearer sk-orion-other-managed')))

    async def test_revoked_hub_delegation_cannot_recreate_managed_account(self):
        route = access.AccountRoute(RECORD, 'user', SUBJECT, MANAGED)
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(return_value=SimpleNamespace(status=401, release=lambda: None))), patch.object(
                accounts, 'request_router', AsyncMock(side_effect=accounts.RouterAccountError('revoked'))), patch.object(
                accounts, 'ensure_account', AsyncMock()) as ensure:
            async with RouterSession(account_route=route) as session:
                with self.assertRaises(accounts.RouterAccountError):
                    await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={})
            ensure.assert_not_awaited()

    async def test_partial_network_failure_is_not_replayed(self):
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=[SimpleNamespace(status=200, release=lambda: None), TimeoutError])) as send:
            async with RouterSession(account_route=self.route) as session:
                with self.assertRaises(TimeoutError):
                    await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={})
            self.assertEqual(2, send.await_count)

    async def test_auth_probe_timeout_can_try_managed_before_any_model_work(self):
        reply = lambda: SimpleNamespace(status=200, release=lambda: None)
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=[TimeoutError, reply(), reply()])) as send:
            async with RouterSession(account_route=self.route) as session:
                await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={})
            self.assertEqual(['GET', 'GET', 'POST'], [call.args[0] for call in send.await_args_list])
            self.assertEqual('Bearer ' + MANAGED, send.await_args_list[-1].kwargs['headers']['Authorization'])

    async def test_provider_401_after_successful_auth_is_not_replayed(self):
        replies = [SimpleNamespace(status=value, release=lambda: None) for value in (200, 401)]
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=replies)) as send:
            async with RouterSession(account_route=self.route) as session:
                response = await session._request('POST', RECORD['url'] + '/v1/chat/completions', json={})
            self.assertEqual(401, response.status)
            self.assertEqual(2, send.await_count)

    async def test_multipart_auth_fallback_happens_before_body_consumption(self):
        replies = [SimpleNamespace(status=value, release=lambda: None) for value in (401, 200, 200)]
        body = aiohttp.FormData(); body.add_field('file', b'audio', filename='test.wav')
        with patch.object(aiohttp.ClientSession, '_request', AsyncMock(side_effect=replies)) as send:
            async with RouterSession(account_route=self.route) as session:
                await session._request('POST', RECORD['url'] + '/v1/audio/transcriptions', data=body)
            self.assertEqual(['GET', 'GET', 'POST'], [call.args[0] for call in send.await_args_list])
            self.assertNotIn('data', send.await_args_list[0].kwargs)
            self.assertIs(body, send.await_args_list[-1].kwargs['data'])
