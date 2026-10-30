import asyncio
import base64
import io
import wave
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.worker.services.adaptive_tts import AdaptiveTts
from orion.worker.services.sentence_segmenter import SentenceSegmenter
from orion.worker.services.tts_stream import relay_tts_batches

PCM = {"format": "pcm", "sample_rate": 24000, "channels": 1,
       "bits_per_sample": 16, "encoding": "pcm_s16le"}


class SegmentTests(unittest.TestCase):
    def test_token_boundaries_abbreviations_decimal_url_and_final_tail(self):
        source = "Dr. Ayşe 3.14 dedi. https://orion.test/a?x=1 Sonra geldim! Noktasız son"
        for width in (1, 2, 7, 1000):
            segmenter = SentenceSegmenter()
            out = []
            for offset in range(0, len(source), width):
                out.extend(segmenter.feed(source[offset:offset + width]))
            out.extend(segmenter.feed('', final=True))
            self.assertEqual(out, ['Dr. Ayşe 3.14 dedi.', 'Sonra geldim!', 'Noktasız son'])

    def test_code_and_markdown_are_not_read(self):
        segmenter = SentenceSegmenter()
        result = segmenter.feed('**Merhaba.** ```python\nprint("konuşma.")\n``` [Devam](https://x.test). ', final=True)
        self.assertEqual(' '.join(result), 'Merhaba. Devam.')
        segmenter = SentenceSegmenter()
        result = segmenter.feed('Bir `foo. bar')
        result += segmenter.feed('` örnek. ', final=True)
        self.assertNotIn('foo', ' '.join(result))
        self.assertNotIn('bar', ' '.join(result))

    def test_bold_first_sentence_is_available_before_final(self):
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed('**Merhaba.** Devam'), ['Merhaba.'])

    def test_long_unpunctuated_text_has_bounded_parts(self):
        segmenter = SentenceSegmenter()
        parts = segmenter.feed('kelime ' * 300, final=True)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 600 for part in parts))


class AdaptiveTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(voice=None, emit_audio_stream=AsyncMock(), emit_audio=AsyncMock(return_value={'saved': True}))

    async def test_first_audio_before_final_then_three_ready_sentences_one_request(self):
        context = self.context()
        first = asyncio.Event()
        release = asyncio.Event()
        requests = []
        async def provider(text, *_):
            requests.append(text)
            yield b'\x01\x02', PCM
            if len(requests) == 1:
                first.set()
                await release.wait()
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(context, None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('İlk cümle burada. ')
            await asyncio.wait_for(first.wait(), 1)
            self.assertFalse(speech.closed)
            self.assertEqual(context.emit_audio_stream.call_args_list[0].args[0], 'audio_start')
            await speech.feed('İkinci cümle geldi. Üçüncü cümle geldi. Dördüncü cümle geldi. Son parça')
            release.set()
            result = await asyncio.wait_for(speech.finish(), 1)
        self.assertEqual(result, {'saved': True})
        self.assertEqual(requests, ['İlk cümle burada.', 'İkinci cümle geldi. Üçüncü cümle geldi. Dördüncü cümle geldi.', 'Son parça'])
        events = context.emit_audio_stream.call_args_list
        self.assertEqual(sum(c.args[0] == 'audio_start' for c in events), 1)
        self.assertEqual(sum(c.args[0] == 'audio_done' for c in events), 1)
        chunks = [c.args[1] for c in events if c.args[0] == 'audio_chunk']
        self.assertEqual([c['sequence'] for c in chunks], [0, 1, 2])
        self.assertEqual([c['sentence_index'] for c in chunks], [0, 1, 4])
        with wave.open(io.BytesIO(context.emit_audio.call_args.args[0])) as wav:
            self.assertEqual(wav.readframes(3), b'\x01\x02' * 3)

    async def test_short_sentence_waits_for_the_next_one(self):
        requests = []
        async def provider(text, *_):
            requests.append(text)
            yield b'', PCM
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('Selam. ')
            await asyncio.sleep(0.05)
            self.assertEqual(requests, [])
            await speech.feed('Bugün sana bir fıkra anlatayım. Temel')
            await asyncio.sleep(0.05)
            self.assertEqual(requests, ['Selam. Bugün sana bir fıkra anlatayım.'])
            await speech.feed(' gülmüş.')
            await speech.finish()
        self.assertEqual(requests, ['Selam. Bugün sana bir fıkra anlatayım.', 'Temel gülmüş.'])

    async def test_short_reply_is_spoken_at_the_end(self):
        requests = []
        async def provider(text, *_):
            requests.append(text)
            yield b'', PCM
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('Selam, naber?')
            await speech.finish()
        self.assertEqual(requests, ['Selam, naber?'])

    async def test_snapshot_does_not_duplicate_committed_sentences(self):
        requests = []
        async def provider(text, *_):
            requests.append(text)
            yield b'\x01\x02', PCM
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('Merhaba. Dev')
            await speech.feed('Merhaba. Devam.', snapshot=True)
            await speech.finish()
        self.assertEqual(' '.join(requests), 'Merhaba. Devam.')

    async def test_provider_failure_preserves_text_path_without_retry(self):
        requests = []
        async def provider(text, *_):
            requests.append(text)
            raise RuntimeError('provider failed')
            yield
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('İlk cümle burada. ')
            await speech.task
            await speech.feed('Metin devam edebilir. ')
            self.assertIsNone(await speech.finish())
        self.assertEqual(requests, ['İlk cümle burada.'])
        self.assertIsInstance(speech.error, RuntimeError)

    async def test_stop_while_waiting_for_more_text_cancels_every_task(self):
        stopped = False
        async def stop():
            return stopped
        speech = AdaptiveTts(self.context(), None, stop, lambda: 10)
        stopped = True
        await asyncio.wait_for(speech.task, 1)
        self.assertTrue(speech.closed)

    async def test_size_and_snapshot_retraction_stop_only_audio(self):
        async def provider(text, *_):
            await asyncio.Event().wait()
            yield
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('Merhaba. ')
            await speech.feed('Başka metin.', snapshot=True)
            self.assertIsInstance(speech.error, ValueError)
            self.assertTrue(speech.task.done())
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('x' * 13_000)
            self.assertIsInstance(speech.error, ValueError)
            self.assertTrue(speech.task.done())

    async def test_partial_failure_retains_heard_audio_once(self):
        context = self.context()
        async def provider(text, *_):
            yield b'\x01\x02', PCM
            raise RuntimeError('transport failed')
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(context, None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('İlk cümle burada. ')
            await speech.task
        context.emit_audio.assert_awaited_once()
        self.assertEqual(context.emit_audio.call_args.kwargs['status'], 'failed')
        self.assertEqual(context.emit_audio_stream.call_args_list[-1].args[0], 'audio_done')

    async def test_cancel_releases_blocked_producer_and_synthesis(self):
        entered = asyncio.Event()
        released = asyncio.Event()
        async def provider(text, *_):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield
            finally:
                released.set()
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(self.context(), None, AsyncMock(return_value=False), lambda: 10)
            speech.MAX_QUEUED_CHARS = 30
            await speech.feed('İlk cümle burada. ')
            await asyncio.wait_for(entered.wait(), 1)
            producer = asyncio.create_task(speech.feed('Sonraki cümle. ' * 20))
            await asyncio.sleep(0)
            self.assertFalse(producer.done())
            await asyncio.wait_for(speech.cancel(), 1)
            await asyncio.wait_for(producer, 1)
            self.assertTrue(released.is_set())
            self.assertTrue(speech.task.done())

    async def test_non_pcm_provider_fails_once_without_resynthesis(self):
        context = self.context()
        requests = []
        async def provider(text, *_):
            requests.append(text)
            yield b'compressed', {"format": "mp3"}
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            speech = AdaptiveTts(context, None, AsyncMock(return_value=False), lambda: 10)
            await speech.feed('İlk cümle burada. ')
            await speech.task
            await speech.feed('Devam. ')
            await speech.finish()
        self.assertEqual(requests, ['İlk cümle burada.'])
        self.assertIsNotNone(speech.error)
        context.emit_audio_stream.assert_not_awaited()


    async def test_completed_wav_batches_use_same_pcm_queue_and_recording(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x10\x00\x30\x00" * 160)
        audio = output.getvalue()
        context = self.context()
        first_playing = asyncio.Event()
        continue_text = asyncio.Event()
        requests = []
        async def provider(text, *_):
            requests.append(text)
            for start in range(0, len(audio), 17):
                yield audio[start:start + 17], {"format": "wav"}
        async def batches():
            yield 0, 'İlk cümle.'
            first_playing.set()
            await continue_text.wait()
            yield 1, 'İkinci cümle.'
        with patch('orion.worker.services.tts_stream.stream_tts', provider):
            task = asyncio.create_task(relay_tts_batches(
                context, None, AsyncMock(return_value=False), lambda: 10, batches()))
            await asyncio.wait_for(first_playing.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual([c.args[0] for c in context.emit_audio_stream.call_args_list],
                             ['audio_start', 'audio_chunk', 'audio_batch_done'])
            continue_text.set()
            await asyncio.wait_for(task, 1)
        self.assertEqual(requests, ['İlk cümle.', 'İkinci cümle.'])
        events = context.emit_audio_stream.call_args_list
        chunks = [c.args[1] for c in events if c.args[0] == 'audio_chunk']
        self.assertEqual([c['sequence'] for c in chunks], [0, 1])
        self.assertEqual([c['sentence_index'] for c in chunks], [0, 1])
        self.assertEqual(sum(c.args[0] == 'audio_start' for c in events), 1)
        self.assertEqual(sum(c.args[0] == 'audio_done' for c in events), 1)
        with wave.open(io.BytesIO(context.emit_audio.call_args.args[0])) as wav:
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.readframes(480), b"\x20\x00" * 480)
