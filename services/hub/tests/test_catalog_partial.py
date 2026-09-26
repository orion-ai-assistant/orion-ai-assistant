import asyncio
import time
import unittest
from unittest.mock import patch

from orion.kernel import router_models as catalog


class Response:
    def __init__(self, data, delay=0):
        self.data, self.delay = data, delay

    async def __aenter__(self):
        await asyncio.sleep(self.delay)
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self, local_delay=0, voices=None):
        self.calls = []
        self.local_delay = local_delay
        self.voices = voices or ['zephyr2']

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kwargs):
        name = url.rsplit('/', 1)[-1]
        self.calls.append(name)
        data = {
            'models': {'models': [{'name': 'gemini', 'provider': 'google', 'capability': 'chat'}]},
            'model-groups': {'groups': []},
            'voices': {'voices': {'google': ['cloud-voice']}},
            'local-tts-info': {'voices': self.voices},
        }
        return Response(data[name], self.local_delay if name == 'local-tts-info' else 0)


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(catalog, '_catalog_sections', {}),
            patch.object(catalog, '_catalog_cache', None),
            patch.object(catalog, '_model_catalog_cache', None),
            patch.object(catalog, '_model_refresh_task', None),
            patch.object(catalog, '_LOCAL_TTS_TIMEOUT_SECONDS', .03),
            patch.object(catalog, 'get_router_base_urls', return_value=['http://router', 'http://fallback']),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def fetch(self, session):
        with patch.object(catalog.aiohttp, 'ClientSession', return_value=session):
            return await catalog.get_router_catalog(force_refresh=True)

    async def test_unreachable_local_tts_does_not_block_other_catalogs(self):
        session = Session(local_delay=10)
        started = time.monotonic()
        result = await self.fetch(session)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(result['models'][0]['name'], 'gemini')
        self.assertEqual(result['voices']['google'], ['cloud-voice'])
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
        with patch.object(catalog.aiohttp, 'ClientSession', return_value=session):
            await catalog.validate_model_updates({'router_model_group': 'gemini'})
        self.assertCountEqual(session.calls, ['models', 'model-groups'])

    async def test_poll_and_first_save_share_model_request(self):
        session = Session()
        with patch.object(catalog.aiohttp, 'ClientSession', return_value=session):
            result, _ = await asyncio.gather(
                catalog.get_router_catalog(force_refresh=True, include_voices=False),
                catalog.validate_model_updates({'router_model_group': 'gemini'}),
            )
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
            catalog.aiohttp, 'ClientSession', return_value=Session()
        ):
            full = asyncio.create_task(catalog.get_router_catalog(force_refresh=True))
            try:
                await voice_started.wait()
                await asyncio.wait_for(catalog.validate_model_updates({'router_model_group': 'gemini'}), .5)
                self.assertFalse(full.done())
            finally:
                release_voice.set()
                await full
