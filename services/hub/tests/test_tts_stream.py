import asyncio
import base64
import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.worker.services.tts_stream import relay_tts
from orion.worker.services.router import stream_tts, TtsStreamError


PCM = {"format": "pcm", "sample_rate": 24000, "channels": 1, "bits_per_sample": 16}


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(voice=None, emit_audio_stream=AsyncMock(), emit_audio=AsyncMock(return_value={"audio": "saved"}))

    async def test_stream_aligns_samples_and_saves_wav(self):
        async def stream(*args):
            for data in (b'\x01', b'\x02\x03', b'\x04'):
                yield data, PCM
        context = self.context()
        with patch('orion.worker.services.tts_stream.stream_tts', stream):
            result = await relay_tts(context, None, 'hello', AsyncMock(return_value=False), lambda: 100)
        self.assertEqual(result, {"audio": "saved"})
        events = context.emit_audio_stream.call_args_list
        self.assertEqual([c.args[0] for c in events], ['audio_start', 'audio_chunk', 'audio_chunk', 'audio_done'])
        self.assertEqual(b''.join(base64.b64decode(c.args[1]['chunk']) for c in events if c.args[0] == 'audio_chunk'), b'\x01\x02\x03\x04')
        with wave.open(io.BytesIO(context.emit_audio.call_args.args[0])) as wav:
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.readframes(2), b'\x01\x02\x03\x04')
        self.assertTrue(context.emit_audio.call_args.kwargs['streamed'])

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
            task = asyncio.create_task(relay_tts(context, None, 'hello', check, lambda: 1))
            await entered.wait()
            stop = True
            self.assertIsNone(await asyncio.wait_for(task, 1))
        self.assertTrue(closed.is_set())
        context.emit_audio.assert_not_called()

    async def test_midstream_failure_marks_audio_failed_without_saving(self):
        async def stream(*args):
            yield b'\x00\x00', PCM
            raise RuntimeError('connection lost')
        context = self.context()
        with patch('orion.worker.services.tts_stream.stream_tts', stream), self.assertRaises(RuntimeError):
            await relay_tts(context, None, 'hello', AsyncMock(return_value=False), lambda: 1)
        self.assertEqual(context.emit_audio_stream.call_args.args[1]['status'], 'failed')
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
            task = asyncio.create_task(relay_tts(context, None, 'hello', check, lambda: 1))
            await blocked.wait()
            stop = True
            self.assertEqual(await asyncio.wait_for(task, 1), {"audio": "saved"})
        self.assertTrue(closed.is_set())
        self.assertEqual(context.emit_audio_stream.call_args.args[1]['status'], 'stopped')
        self.assertEqual(context.emit_audio.call_args.kwargs['status'], 'stopped')
        with wave.open(io.BytesIO(context.emit_audio.call_args.args[0])) as wav:
            self.assertEqual(wav.readframes(1), b'\x00\x00')

    async def test_container_response_keeps_existing_playback(self):
        async def stream(*args):
            yield b'ID3audio', {"format": "mp3"}
        context = self.context()
        with patch('orion.worker.services.tts_stream.stream_tts', stream):
            await relay_tts(context, None, 'hello', AsyncMock(return_value=False), lambda: 1)
        context.emit_audio_stream.assert_not_called()
        self.assertEqual(context.emit_audio.call_args.args[0], b'ID3audio')
        self.assertFalse(context.emit_audio.call_args.kwargs['streamed'])


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
