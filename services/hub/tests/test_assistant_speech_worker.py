import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from orion.contracts.http import JobCreateRequest, JobInput
from orion.contracts.settings import RuntimeSettings
from orion.worker.infra.context import JobContext
from orion.worker.services import job_processor as processor

PCM = {"format": "pcm", "encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1, "bits_per_sample": 16}


class WorkerSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, mode, fail_audio=False, audio=True):
        events = []
        heard = asyncio.Event()
        ended = False
        requests = []
        redis = AsyncMock()
        pipe = MagicMock()
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock()
        pipe.watch = AsyncMock()
        pipe.get = AsyncMock(return_value='turn')
        pipe.execute = AsyncMock()
        redis.pipeline = MagicMock(return_value=pipe)
        async def publish(channel, payload):
            event = json.loads(payload)
            events.append(event)
            if event['type'] == 'audio_chunk':
                heard.set()
                if mode == 'adaptive':
                    self.assertFalse(ended and len(requests) == 1)
        redis.publish.side_effect = publish
        record = SimpleNamespace(chat_id='chat', user_id='user', channel='events', turn_id='turn',
                                 created_at=datetime.now(timezone.utc).isoformat(), enabled_tools='[]')
        context = JobContext(record, JobCreateRequest(user_id='user', origin_session_id='session',
            input=JobInput(text='istek', audio=audio, speech_mode=mode)), redis)
        async def llm(*args):
            nonlocal ended
            yield 'thinking', 'PRIVATE THINKING'
            yield 'content', 'Merhaba, nasıl yardımcı olabilirim? '
            if mode == 'adaptive' and not fail_audio:
                await asyncio.wait_for(heard.wait(), 1)
            else:
                self.assertEqual(requests, [])
            yield 'content', 'Sonraki.'
            yield 'content_snapshot', 'Merhaba, nasıl yardımcı olabilirim? Sonraki.'
            ended = True
        conversation = SimpleNamespace(stream=llm, activity=[], transcript=[], rounds=[])
        async def provider(text, *_):
            requests.append(text)
            if mode == 'whole':
                self.assertTrue(ended)
            if fail_audio:
                raise RuntimeError('TTS unavailable')
            yield b'\x01\x02', PCM
        history = AsyncMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(processor.JobContext, 'from_stream_fields', return_value=context))
            stack.enter_context(patch.object(processor, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings(tts_enabled=True))))
            stack.enter_context(patch.object(processor, 'is_active_turn', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(processor, 'should_stop', AsyncMock(return_value=False)))
            stack.enter_context(patch.object(processor, 'load_history', AsyncMock(return_value=[])))
            stack.enter_context(patch.object(processor, 'hset_if_active', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(processor, 'append_history', history))
            stack.enter_context(patch.object(processor, 'finalize_state_if_active', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(processor, 'generate_chat_title', AsyncMock()))
            stack.enter_context(patch.object(processor, 'ToolConversation', return_value=conversation))
            stack.enter_context(patch('orion.worker.services.tts_stream.stream_tts', provider))
            await asyncio.wait_for(processor.process_message(redis, '1-0', {}, 'test'), 2)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['data']['status'], 'completed')
        self.assertFalse(any(event['type'] == 'error' for event in events))
        self.assertEqual(history.call_args.args[2][-1]['content'], 'Merhaba, nasıl yardımcı olabilirim? Sonraki.')
        self.assertTrue(all('PRIVATE' not in text for text in requests))
        return events, requests

    async def test_adaptive_is_early_and_whole_waits_for_llm(self):
        for mode in ('adaptive', 'whole'):
            events, requests = await self.run_turn(mode)
            self.assertEqual(requests, ['Merhaba, nasıl yardımcı olabilirim?', 'Sonraki.'] if mode == 'adaptive'
                             else ['Merhaba, nasıl yardımcı olabilirim? Sonraki.'])
            self.assertEqual(sum(event['type'] == 'audio_start' for event in events), 1)
            self.assertEqual(sum(event['type'] == 'audio_done' for event in events), 1)

    async def test_plain_chat_without_audio_field_speaks_early_too(self):
        events, requests = await self.run_turn('adaptive', audio=None)
        self.assertEqual(requests, ['Merhaba, nasıl yardımcı olabilirim?', 'Sonraki.'])
        self.assertEqual(sum(event['type'] == 'audio_done' for event in events), 1)

    async def test_tts_failure_does_not_fail_or_duplicate_the_text_response(self):
        events, requests = await self.run_turn('adaptive', fail_audio=True)
        self.assertEqual(len(requests), 1)
        self.assertTrue(any(event['type'] == 'warning' for event in events))
