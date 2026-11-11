import asyncio
import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.worker.services.tts_stream import relay_tts_batches
from orion.worker.services.router import stream_tts, TtsStreamError


PCM = {"format": "pcm", "sample_rate": 24000, "channels": 1, "bits_per_sample": 16}


class RelayTests(unittest.IsolatedAsyncioTestCase):
    """Stopping a reply; the rest of the relay is covered in test_adaptive_tts.py."""

    def context(self):
        return SimpleNamespace(voice=None, emit_audio_stream=AsyncMock(), emit_audio=AsyncMock(return_value={"audio": "saved"}))

    @staticmethod
    async def one_part():
        yield 0, 'hello'

    async def test_stop_before_first_chunk_closes_generator(self):
        closed = asyncio.Event()
        entered = asyncio.Event()
        async def stream(*args):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield b'\x00\x00', PCM
            finally:
                closed.set()
        context = self.context()
        stop = False
        async def check():
            return stop
        with patch('orion.worker.services.tts_stream.stream_tts', stream):
            task = asyncio.create_task(relay_tts_batches(context, None, check, lambda: 1, self.one_part()))
            await entered.wait()
            stop = True
            self.assertIsNone(await asyncio.wait_for(task, 1))
        self.assertTrue(closed.is_set())
        context.emit_audio.assert_not_called()

    async def test_stop_during_stream_retains_partial_recording(self):
        blocked = asyncio.Event()
        closed = asyncio.Event()
        stop = False
        async def stream(*args):
            try:
                yield b'\x00\x00', PCM
                blocked.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def check(): return stop
        context = self.context()
        with patch('orion.worker.services.tts_stream.stream_tts', stream):
            task = asyncio.create_task(relay_tts_batches(context, None, check, lambda: 1, self.one_part()))
            await blocked.wait()
            stop = True
            self.assertEqual(await asyncio.wait_for(task, 1), {"audio": "saved"})
        self.assertTrue(closed.is_set())
        self.assertEqual(context.emit_audio_stream.call_args.args[1]['status'], 'stopped')
        self.assertEqual(context.emit_audio.call_args.kwargs['status'], 'stopped')
        with wave.open(io.BytesIO(context.emit_audio.call_args.args[0])) as wav:
            self.assertEqual(wav.readframes(1), b'\x00\x00')


class RouterStreamTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, status=200, headers=None, chunks=None):
        self.closed = False
        async def content(_):
            for chunk in chunks or [b'\x00\x00']:
                yield chunk
        response = SimpleNamespace(status=status, headers=headers or {
            'Content-Type': 'audio/pcm', 'X-Audio-Sample-Rate': '24000',
            'X-Audio-Channels': '1', 'X-Audio-Bits-Per-Sample': '16'},
            text=AsyncMock(return_value='{"error":{"message":"quota exhausted"}}'),
            content=SimpleNamespace(iter_chunked=content))
        owner = self
        class Request:
            async def __aenter__(self): return response
            async def __aexit__(self, *args): owner.closed = True
        session = SimpleNamespace(post=lambda *args, **kwargs: Request())
        return session, {}, {}, None, ['https://router/v1/audio/speech'], 'gemini'

    async def test_headers_validated_and_generator_close_releases_response(self):
        request = await self.request(chunks=[b'\x01\x02', b'\x03\x04'])
        with patch('orion.worker.services.router._tts_request', AsyncMock(return_value=request)):
            stream = stream_tts('hello', SimpleNamespace(tts_timeout_seconds=15))
            data, info = await anext(stream)
            self.assertEqual(data, b'\x01\x02')
            self.assertEqual(info['encoding'], 'pcm_s16le')
            await stream.aclose()
        self.assertTrue(self.closed)

    async def test_request_asks_the_router_to_keep_the_connection_until_the_audio_is_read(self):
        from orion.worker.services import router
        settings = SimpleNamespace(tts_model='gemini-test', tts_voice='', tts_timeout_seconds=15, tts_model_voices={})
        with patch.object(router, 'get_session', AsyncMock()), \
                patch.object(router, 'get_model_provider', AsyncMock(return_value='gemini')), \
                patch.object(router, 'get_router_base_urls', lambda: ['https://router']), \
                patch.object(router, '_get_router_api_key', lambda settings: 'key'):
            _, headers, _, _, _, _ = await router._tts_request('hello', settings, None, 'pcm')
        self.assertEqual(headers['Connection'], 'keep-alive')

    async def test_request_carries_the_chosen_speech_language_only_when_one_is_set(self):
        from orion.contracts.settings import RuntimeSettings
        from orion.worker.services import router
        with patch.object(router, 'get_session', AsyncMock()),                 patch.object(router, 'get_model_provider', AsyncMock(return_value='local')),                 patch.object(router, 'get_router_base_urls', lambda: ['https://router']),                 patch.object(router, '_get_router_api_key', lambda settings: 'key'):
            for chosen, sent in ((' Turkish ', 'Turkish'), ('Auto', 'Auto')):
                settings = RuntimeSettings(tts_model='local-tts', tts_language=chosen)
                _, _, payload, _, _, _ = await router._tts_request('merhaba', settings, None, 'pcm')
                self.assertEqual(payload['language'], sent)
            unset = RuntimeSettings(tts_model='local-tts', tts_language='')
            _, _, payload, _, _, _ = await router._tts_request('merhaba', unset, None, 'pcm')
            self.assertNotIn('language', payload)

    async def test_the_models_own_voice_wins_over_a_global_one_left_by_another_engine(self):
        from orion.contracts.settings import RuntimeSettings
        from orion.worker.services import router
        with patch.object(router, 'get_session', AsyncMock()),                 patch.object(router, 'get_model_provider', AsyncMock(return_value='local')),                 patch.object(router, 'get_router_base_urls', lambda: ['https://router']),                 patch.object(router, '_get_router_api_key', lambda settings: 'key'):
            # "Varsayılan" saved for local-tts: no voice is sent, the engine speaks with its own.
            default = RuntimeSettings(tts_model='local-tts', tts_voice='zephyr', tts_model_voices={'local-tts': ''})
            _, _, payload, _, _, _ = await router._tts_request('merhaba', default, None, 'pcm')
            self.assertNotIn('voice', payload)
            chosen = RuntimeSettings(tts_model='local-tts', tts_voice='zephyr', tts_model_voices={'local-tts': 'nika'})
            _, _, payload, _, _, _ = await router._tts_request('merhaba', chosen, None, 'pcm')
            self.assertEqual(payload['voice'], 'nika')
            # A model with nothing saved still takes the global voice.
            legacy = RuntimeSettings(tts_model='local-tts', tts_voice='nika', tts_model_voices={})
            _, _, payload, _, _, _ = await router._tts_request('merhaba', legacy, None, 'pcm')
            self.assertEqual(payload['voice'], 'nika')

    def test_the_speech_warning_carries_the_services_own_reason(self):
        import json
        from orion.worker.services.job_processor import _tts_reason
        inner = json.dumps({'detail': "Böyle bir ses klonu bulunamadı: 'zephyr'."}, ensure_ascii=False)
        self.assertEqual(_tts_reason(f'Router TTS (500): Local TTS API Error: 400 - {inner}'),
                         "Böyle bir ses klonu bulunamadı: 'zephyr'.")
        wrapped = json.dumps({'error': f'Local TTS API Error: 400 - {inner}'}, ensure_ascii=False)
        self.assertEqual(_tts_reason(f'Router TTS (500): {wrapped}'), "Böyle bir ses klonu bulunamadı: 'zephyr'.")
        self.assertEqual(_tts_reason(TimeoutError('read timed out')), 'read timed out')

    async def test_quota_error_preserves_http_status(self):
        request = await self.request(status=429)
        with patch('orion.worker.services.router._tts_request', AsyncMock(return_value=request)):
            with self.assertRaises(TtsStreamError) as error:
                await anext(stream_tts('hello', SimpleNamespace(tts_timeout_seconds=15)))
        self.assertEqual(error.exception.status, 429)
        self.assertIn('quota exhausted', str(error.exception))
        self.assertTrue(self.closed)

    async def test_missing_pcm_headers_are_rejected_before_audio(self):
        request = await self.request(headers={'Content-Type': 'audio/pcm'})
        with patch('orion.worker.services.router._tts_request', AsyncMock(return_value=request)):
            with self.assertRaises(TtsStreamError):
                await anext(stream_tts('hello', SimpleNamespace(tts_timeout_seconds=15)))
        self.assertTrue(self.closed)

    async def test_stream_request_accepts_complete_wav_fallback(self):
        for provider, asked in (('local', 'pcm'), ('openai', 'wav')):
            request = list(await self.request(headers={'Content-Type': 'audio/wav'}, chunks=[b'wave']))
            request[-1] = provider
            request[2] = {'response_format': 'pcm'}  # As _tts_request builds it.
            payloads = []
            post = request[0].post
            request[0].post = lambda *args, **kwargs: (payloads.append(dict(kwargs['json'])), post(*args, **kwargs))[1]
            with patch('orion.worker.services.router._tts_request', AsyncMock(return_value=tuple(request))):
                result = [part async for part in stream_tts('hello', SimpleNamespace(tts_timeout_seconds=15))]
            self.assertEqual(result, [(b'wave', {'format': 'wav'})])
            self.assertEqual(payloads[0]['response_format'], asked)  # Local asks PCM: VoxCPM2 streams it.
            self.assertTrue(payloads[0]['stream'])

    async def test_local_pcm_rate_header_streams_normalized_audio(self):
        request = list(await self.request(headers={'Content-Type': 'audio/pcm', 'X-Sample-Rate': '16000'},
                                          chunks=[b'\x20', b'\x00\x20\x00']))
        request[-1] = 'local'
        with patch('orion.worker.services.router._tts_request', AsyncMock(return_value=tuple(request))):
            parts = [part async for part in stream_tts('hello', SimpleNamespace(tts_timeout_seconds=15))]
        self.assertEqual(b''.join(data for data, _ in parts), b'\x20\x00' * 3)
        self.assertTrue(all(info['sample_rate'] == 24000 for _, info in parts))


class PcmNormalizerTests(unittest.TestCase):
    def test_transport_chunk_boundaries_do_not_change_resampling(self):
        from orion.worker.services.pcm_normalizer import PcmNormalizer
        import struct
        for rate in (16000, 22050, 44100, 48000):
            for channels in (1, 2):
                audio = struct.pack('<200h', *range(200))
                whole = PcmNormalizer(rate, channels)
                expected = whole.feed(audio) + whole.feed(b'', final=True)
                split = PcmNormalizer(rate, channels)
                actual = b''.join(split.feed(audio[i:i + 7]) for i in range(0, len(audio), 7))
                actual += split.feed(b'', final=True)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), (200 // channels) * 24000 // rate * 2)

    def test_incomplete_frame_is_rejected(self):
        from orion.worker.services.pcm_normalizer import PcmNormalizer
        converter = PcmNormalizer(16000, 2)
        converter.feed(b'\x01\x02')
        with self.assertRaises(ValueError):
            converter.feed(b'', final=True)
