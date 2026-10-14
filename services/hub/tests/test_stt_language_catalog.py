import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from orion.api.access_boundary import public_path


def require_user():
    raise HTTPException(401, 'Not authenticated')


class SttLanguageCatalogTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'src/orion/api/routes.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'stt_language_catalog')
        self.app = FastAPI()
        self.app.state.redis = object()
        self.settings = AsyncMock(return_value=SimpleNamespace(router_api_key='account-key'))
        namespace = {'router': self.app, 'Depends': Depends, 'get_current_user': require_user,
                     'Request': Request, 'get_runtime_settings': self.settings,
                     'aiohttp': aiohttp, 'HTTPException': HTTPException}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        self.fetch = AsyncMock(return_value={'aliases': {}, 'languages': {'tr': {'name': 'Turkish'}}})
        self.module = patch.dict(sys.modules, {'orion.api.services.stt_catalog': SimpleNamespace(fetch_stt_catalog=self.fetch)})
        self.module.start()
        self.addCleanup(self.module.stop)

    def test_catalog_requires_the_user_dependency(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/api/v1/audio/languages').status_code, 401)
        self.fetch.assert_not_called()

    def test_mobile_catalog_uses_authenticated_router_api(self):
        self.app.dependency_overrides[require_user] = lambda: 'alice'
        with TestClient(self.app) as client:
            response = client.get('/api/v1/audio/languages')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['languages'], {'tr': {'name': 'Turkish'}})
        self.fetch.assert_awaited_once_with('account-key')
        self.settings.assert_awaited_once_with(self.app.state.redis, 'alice')
        self.assertTrue(public_path('/api/v1/audio/languages'))

    def test_unavailable_catalog_does_not_modify_settings(self):
        self.app.dependency_overrides[require_user] = lambda: 'alice'
        self.fetch.side_effect = aiohttp.ClientConnectionError('offline')
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/api/v1/audio/languages').status_code, 503)

    def test_stt_only_exposes_metadata(self):
        root = Path(__file__).resolve().parents[2] / 'stt/whisper'
        path = root / 'api.py'
        node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_audio_language_catalog')
        app = FastAPI()
        catalog = json.loads((root / 'stt_languages.json').read_text(encoding='utf-8'))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'),
             {'app': app, 'LANGUAGE_CATALOG': catalog})
        with TestClient(app) as client:
            data = client.get('/v1/audio/language-catalog').json()
        self.assertEqual(len(data['languages']), 100)
        self.assertEqual(data['aliases'], catalog['aliases'])
        self.assertTrue(all(set(entry) == {'name'} for entry in data['languages'].values()))


class SttCatalogTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_uses_pinned_session_and_forwards_only_metadata(self):
        from unittest.mock import MagicMock
        path = Path(__file__).resolve().parents[1] / 'src/orion/api/services/stt_catalog.py'
        node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'fetch_stt_catalog')
        response = MagicMock()
        response.json = AsyncMock(return_value={
            'aliases': {'nb': 'no'}, 'languages': {'tr': {'name': 'Turkish', 'prompt': 'private'}}})
        response_context = MagicMock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = response_context
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=False)
        pinned_factory = MagicMock(return_value=session_context)
        namespace = dict(aiohttp=aiohttp, get_router_base_urls=lambda: ['https://router:9443/v1'],
                         router_session=pinned_factory)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        result = await namespace['fetch_stt_catalog']('account-key')
        session.get.assert_called_once_with('https://router:9443/v1/audio/languages',
                                            headers={'Authorization': 'Bearer account-key'})
        self.assertEqual(pinned_factory.call_args.kwargs['timeout'].total, 5)
        self.assertEqual(result, {'aliases': {'nb': 'no'}, 'languages': {'tr': {'name': 'Turkish'}}})
