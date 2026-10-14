import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import aiohttp
from fastapi import FastAPI, Depends, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orion.kernel import config, registry
from orion.contracts.settings import RuntimeSettings
from orion.api.services import stt_catalog
from orion.api.cors import configure_cors


class InitialLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_manual_or_auto_preference_never_changes(self):
        for language in ('en', 'tr', 'auto'):
            with (patch.object(config, '_get_effective_overrides', AsyncMock(return_value={'stt_language': language})),
                  patch.object(registry, 'initialize_stt_language_override', AsyncMock()) as write,
                  patch.object(config, 'refresh_runtime_settings', AsyncMock(return_value=RuntimeSettings(stt_language=language)))):
                saved = await config.initialize_stt_language(object(), 'alice', 'tr')
                self.assertEqual(saved.stt_language, language)
                write.assert_not_awaited()

    async def test_concurrent_winner_is_returned_instead_of_candidate(self):
        with (patch.object(config, '_get_effective_overrides', AsyncMock(return_value={})),
              patch.object(registry, 'initialize_stt_language_override', AsyncMock()) as write,
              patch.object(config, 'refresh_runtime_settings', AsyncMock(return_value=RuntimeSettings(stt_language='en')))):
            saved = await config.initialize_stt_language(object(), 'alice', 'tr')
        write.assert_awaited_once_with('alice', 'tr')
        self.assertEqual(saved.stt_language, 'en')

    async def test_database_failure_does_not_claim_a_persisted_choice(self):
        with (patch.object(config, '_get_effective_overrides', AsyncMock(return_value={})),
              patch.object(registry, 'initialize_stt_language_override', AsyncMock(side_effect=RuntimeError('offline'))),
              patch.object(config, 'refresh_runtime_settings', AsyncMock()) as refresh):
            with self.assertRaises(RuntimeError):
                await config.initialize_stt_language(object(), 'alice', 'tr')
        refresh.assert_not_awaited()


class FirstDeviceRouteTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'src/orion/api/routes.py'
        node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_settings')
        self.app = FastAPI()
        self.app.state.redis = object()
        self.saved = None
        async def settings(*args):
            return RuntimeSettings(stt_language=self.saved)
        async def initialize(redis, user, candidate):
            self.saved = self.saved or candidate
            return RuntimeSettings(stt_language=self.saved)
        self.initialize = AsyncMock(side_effect=initialize)
        self.fetch = AsyncMock(return_value={'languages': {'tr': {}, 'en': {}, 'no': {}}, 'aliases': {'nb': 'no'}})
        namespace = dict(router=self.app, RuntimeSettings=RuntimeSettings, Request=Request, Depends=Depends,
                         get_current_user=lambda: 'alice', Redis=object, get_runtime_settings=settings,
                         _mask_settings_for_client=lambda settings: settings, aiohttp=aiohttp)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        for target, value in ((stt_catalog, self.fetch), (config, self.initialize)):
            name = 'fetch_stt_catalog' if target is stt_catalog else 'initialize_stt_language'
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def read(self, client, language):
        return client.get('/api/v1/admin/settings', headers={'X-Orion-STT-Language': language})

    def test_first_device_language_is_used_by_second_device(self):
        for first, second, expected in (('tr-TR', 'en-US', 'tr'), ('en-US', 'tr-TR', 'en'), ('xx-ZZ', 'tr-TR', 'auto'), ('nb-NO', 'en-US', 'no')):
            self.saved = None
            self.initialize.reset_mock()
            with TestClient(self.app) as client:
                self.assertEqual(self.read(client, first).json()['stt_language'], expected)
                self.assertEqual(self.read(client, second).json()['stt_language'], expected)
            self.initialize.assert_awaited_once()

    def test_explicit_auto_does_not_load_catalog_or_reinitialize(self):
        self.saved = 'auto'
        with TestClient(self.app) as client:
            self.assertEqual(self.read(client, 'tr-TR').json()['stt_language'], 'auto')
        self.fetch.assert_not_awaited()
        self.initialize.assert_not_awaited()

    def test_unavailable_catalog_keeps_unset_and_retries_later(self):
        self.fetch.side_effect = aiohttp.ClientConnectionError('offline')
        with TestClient(self.app) as client:
            result = self.read(client, 'tr-TR')
            self.assertEqual(result.status_code, 200)
            self.assertIsNone(result.json()['stt_language'])
            self.initialize.assert_not_awaited()
            self.fetch.side_effect = None
            self.assertEqual(self.read(client, 'tr-TR').json()['stt_language'], 'tr')

    def test_database_failure_leaves_preference_unset(self):
        self.initialize.side_effect = RuntimeError('offline')
        with TestClient(self.app) as client:
            result = self.read(client, 'tr-TR')
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()['stt_language'])

    def test_flutter_web_preflight_allows_device_language(self):
        configure_cors(self.app)
        with TestClient(self.app) as client:
            result = client.options('/api/v1/admin/settings', headers={
                'Origin': 'http://localhost:8080', 'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'Authorization,X-Orion-STT-Language'})
        self.assertEqual(result.status_code, 200)
