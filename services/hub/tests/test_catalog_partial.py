import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from orion.kernel import router_models as catalog


class Response:
    def __init__(self, data, delay=0, started=None, release=None):
        self.data, self.delay = data, delay
        self.started, self.release = started, release

    async def __aenter__(self):
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        await asyncio.sleep(self.delay)
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self, local_delay=0, voices=None, model_started=None, release_model=None):
        self.calls = []
        self.model_started, self.release_model = model_started, release_model
        self.local_delay = local_delay
        self.voices = voices or ['zephyr2']

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kwargs):
        name = url.rsplit('/', 1)[-1]
        assert url.startswith('https://router.test:9443/api/v1/hubs/catalog/')
        assert kwargs['headers'] == {'Authorization': 'Bearer fixture-token'}
        self.calls.append(name)
        data = {
            'models': {'models': [{'name': 'gemini', 'provider': 'google', 'capability': 'chat'}]},
            'model-groups': {'groups': []},
            'voices': {'voices': {'google': ['cloud-voice']}, 'model_voice_groups': {'google': [{'models': ['tts-classic'], 'voices': ['cloud-voice']}]}},
            'local-tts-info': {'voices': self.voices},
        }
        return Response(data[name], self.local_delay if name == 'local-tts-info' else 0,
                        self.model_started if name == 'models' else None,
                        self.release_model if name == 'models' else None)


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(catalog, '_catalog_sections', {}),
            patch.object(catalog, '_catalog_cache', None),
            patch.object(catalog, '_catalog_cached_at', 0.0),
            patch.object(catalog, '_model_cached_at', 0.0),
            patch.object(catalog, '_catalog_generation', 0),
            patch('orion.kernel.router_accounts.catalog_credentials', AsyncMock(return_value=('/api/v1/hubs/catalog/', {'Authorization': 'Bearer fixture-token'}))),
            patch.object(catalog, '_model_catalog_cache', None),
            patch.object(catalog, '_model_refresh_task', None),
            patch.object(catalog, '_LOCAL_TTS_TIMEOUT_SECONDS', .03),
            patch.object(catalog, 'get_router_base_urls', return_value=['https://router.test:9443', 'https://fallback.test:9443']),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def asyncTearDown(self):
        task = catalog._model_refresh_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def fetch(self, session):
        with patch.object(catalog, 'router_session', return_value=session):
            return await catalog.get_router_catalog(force_refresh=True)

    async def test_unreachable_local_tts_does_not_block_other_catalogs(self):
        session = Session(local_delay=10)
        result = await asyncio.wait_for(self.fetch(session), 1)
        self.assertEqual(result['models'][0]['name'], 'gemini')
        self.assertEqual(result['voices']['google'], ['cloud-voice'])
        self.assertEqual(result['model_voice_groups']['google'][0]['models'], ['tts-classic'])
        self.assertIn('local-tts-info', result['unavailable'])
        self.assertEqual(session.calls.count('local-tts-info'), 1)

    async def test_outage_preserves_previous_voices_and_recovers(self):
        await self.fetch(Session())
        offline = await self.fetch(Session(local_delay=10))
        self.assertEqual(offline['voices']['local'], ['zephyr2'])
        recovered = await self.fetch(Session(voices=['zephyr2', 'new-voice']))
        self.assertEqual(recovered['voices']['local'], ['zephyr2', 'new-voice'])
        self.assertNotIn('local-tts-info', recovered['unavailable'])

    async def test_cold_model_save_never_requests_voice_services(self):
        session = Session(local_delay=10)
        with patch.object(catalog, 'router_session', return_value=session):
            await catalog.validate_model_updates({'router_model_group': 'gemini'})
        self.assertCountEqual(session.calls, ['models', 'model-groups'])

    async def test_poll_and_first_save_share_model_request(self):
        started, release = asyncio.Event(), asyncio.Event()
        session = Session(model_started=started, release_model=release)
        with patch.object(catalog, 'router_session', return_value=session):
            poll = asyncio.create_task(catalog.get_router_catalog(force_refresh=True, include_voices=False))
            save = None
            try:
                await asyncio.wait_for(started.wait(), 1)
                save = asyncio.create_task(catalog.validate_model_updates({'router_model_group': 'gemini'}))
                await asyncio.sleep(0)
                self.assertFalse(poll.done())
                self.assertFalse(save.done())
                release.set()
                result, _ = await asyncio.wait_for(asyncio.gather(poll, save), 1)
            finally:
                release.set()
                for task in (poll, save):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(task for task in (poll, save) if task is not None), return_exceptions=True)
        self.assertEqual(result['models'][0]['name'], 'gemini')
        self.assertCountEqual(session.calls, ['models', 'model-groups'])

    async def test_validation_finishes_while_voice_refresh_is_pending(self):
        voice_started, release_voice = asyncio.Event(), asyncio.Event()
        original = catalog._fetch_section

        async def fetch(session, bases, name, key):
            if name == 'local-tts-info':
                voice_started.set()
                await release_voice.wait()
            return await original(session, bases, name, key)

        with patch.object(catalog, '_fetch_section', side_effect=fetch), patch.object(
            catalog, 'router_session', return_value=Session()
        ):
            full = asyncio.create_task(catalog.get_router_catalog(force_refresh=True))
            try:
                await asyncio.wait_for(voice_started.wait(), 1)
                await asyncio.wait_for(catalog.validate_model_updates({'router_model_group': 'gemini'}), .5)
                self.assertFalse(full.done())
            finally:
                release_voice.set()
                await asyncio.wait_for(full, 1)

    async def test_cache_clear_prevents_old_inflight_catalog_from_being_published(self):
        started, release = asyncio.Event(), asyncio.Event()
        session = Session(model_started=started, release_model=release)
        with patch.object(catalog, 'router_session', return_value=session):
            refresh = asyncio.create_task(catalog.get_router_catalog(force_refresh=True, include_voices=False))
            inflight = None
            try:
                await asyncio.wait_for(started.wait(), 1)
                inflight = catalog._model_refresh_task
                generation = catalog._catalog_generation
                catalog.clear_catalog_cache()
                self.assertEqual(catalog._catalog_generation, generation + 1)
                release.set()
                with self.assertRaises(RuntimeError):
                    await asyncio.wait_for(refresh, 1)
                self.assertIsNone(catalog._model_catalog_cache)
                self.assertEqual(catalog._catalog_sections, {})
            finally:
                release.set()
                refresh.cancel()
                if inflight is not None:
                    inflight.cancel()
                    await asyncio.gather(inflight, return_exceptions=True)
                await asyncio.gather(refresh, return_exceptions=True)
