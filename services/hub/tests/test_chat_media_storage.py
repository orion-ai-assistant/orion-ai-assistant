"""Media integration tests use a NEW temporary PostgreSQL cluster, never live DB."""
import asyncio
import base64
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

from orion.api import routes
from orion.api.services import job_service, tool_service
from orion.contracts.access import ChatAccessResult
from orion.contracts.http import Attachment, JobCreateRequest
from orion.contracts.settings import RuntimeSettings
from orion.kernel import registry, media_store
from orion.worker.infra.context import JobContext
from orion.worker.services.attachments import history_content
from orion.worker.services.job_processor import append_history
from orion.worker.services.tool_history import model_history


class ChatMediaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        binary = Path(__file__).resolve().parents[3] / '.local_db/postgres/bin'
        initdb = binary / ('initdb.exe' if os.name == 'nt' else 'initdb')
        cls.pg_ctl = binary / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl')
        if not initdb.exists():
            raise unittest.SkipTest('Local PostgreSQL test binaries are unavailable')
        cls.temp = tempfile.TemporaryDirectory(prefix='orion-media-test-')
        cls.root = Path(cls.temp.name).resolve()
        assert cls.root.parent == Path(tempfile.gettempdir()).resolve()
        cls.data = cls.root / 'data'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        cls.url = f'postgresql://postgres@127.0.0.1:{port}/postgres'
        def run(args):
            # pg_ctl's detached server can inherit pipes on Windows. A file avoids
            # communicate waiting forever for the running server to close them.
            with (cls.root / 'control.log').open('ab') as log:
                subprocess.run(args, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=40,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            run([str(initdb), '-D', str(cls.data), '-U', 'postgres', '-A', 'trust', '--no-locale', '-E', 'UTF8'])
            run([str(cls.pg_ctl), '-D', str(cls.data), '-l', str(cls.root / 'postgres.log'),
                 '-o', f'-F -h 127.0.0.1 -p {port}', '-w', 'start'])
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            subprocess.run([str(cls.pg_ctl), '-D', str(cls.data), '-m', 'immediate', '-w', 'stop'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        finally:
            assert cls.root.parent == Path(tempfile.gettempdir()).resolve()
            cls.temp.cleanup()

    async def asyncSetUp(self):
        async def connection(*args, **kwargs):
            return await asyncpg.connect(self.url, command_timeout=5)
        guard = patch.object(registry, '_connect', connection)
        guard.start()
        self.addCleanup(guard.stop)
        conn = await connection()
        try:
            await registry._ensure_tables(conn)
            await media_store.ensure_table(conn)
            await conn.execute('truncate orion_chats cascade')
            await conn.execute("insert into orion_chats(id,user_id) values('chat','alice'),('other','bob')")
        finally:
            await conn.close()

    def attachment(self, raw=b'image bytes', mime='image/png'):
        return dict(name='photo.png', mime_type=mime, isText=False,
                    data=f'data:{mime};base64,' + base64.b64encode(raw).decode())

    async def count(self):
        conn = await registry._connect()
        try:
            return await conn.fetchval('select count(*) from orion_chat_media')
        finally:
            await conn.close()

    async def test_binary_dedup_concurrency_references_and_chat_delete(self):
        refs = await asyncio.gather(*[media_store.store_attachments('chat', 'alice', [self.attachment()]) for _ in range(4)])
        self.assertEqual(await self.count(), 1)
        self.assertTrue(all(ref == refs[0] for ref in refs))
        ref = refs[0][0]
        self.assertNotIn('data', ref)
        self.assertEqual(await media_store.read_media('chat', ref['media_id']), ('image/png', b'image bytes'))
        with self.assertRaises(LookupError):
            await media_store.read_media('other', ref['media_id'])
        with self.assertRaises(ValueError):
            await media_store.store_attachments('other', 'bob', [ref])
        with self.assertRaises(ValueError):
            await media_store.store_attachments('chat', 'bob', [ref])
        conn = await registry._connect()
        try:
            await conn.execute("delete from orion_chats where id='chat'")
        finally:
            await conn.close()
        self.assertEqual(await self.count(), 0)

    async def test_partial_attachment_failure_rolls_back_every_blob(self):
        with self.assertRaises(ValueError):
            await media_store.store_attachments('chat', 'alice', [self.attachment(), self.attachment(b'')])
        self.assertEqual(await self.count(), 0)

    async def test_model_order_and_bytes_are_rebuilt_without_mutating_history(self):
        items = [self.attachment(), dict(name='note.txt', mime_type='text/plain', data='note', isText=True),
                 self.attachment(b'video', 'video/mp4')]
        refs = await media_store.store_attachments('chat', 'alice', items)
        history = [dict(role='user', content='question', display_text='question', attachments=refs)]
        original = copy.deepcopy(history)
        resolved = await media_store.hydrate_model_media('chat', model_history(history, 20))
        expected = history_content(dict(role='user', content='question', display_text='question', attachments=items))
        self.assertEqual(resolved[0]['content'], expected)
        self.assertEqual(history, original)
        self.assertNotIn('hub_media', json.dumps(resolved))

    async def test_tts_live_wav_and_durable_opus_have_same_duration(self):
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
            output.writeframes(b'\0\0' * 24000)
        redis = Mock(publish=AsyncMock(), hsetnx=AsyncMock(return_value=True))
        context = JobContext(SimpleNamespace(chat_id='chat', turn_id='turn', channel='events'), None, redis)
        live_recording = await context.emit_audio(wav.getvalue(), sample_rate=24000)
        live = json.loads(redis.publish.call_args.args[1])['data']
        self.assertEqual(base64.b64decode(live['audio']), wav.getvalue())
        self.assertEqual(live['format'], 'wav')
        self.assertEqual(live_recording, live)
        redis.pipeline.return_value.execute = AsyncMock()
        await append_history(redis, 'chat', [dict(role='assistant', content='hello', turn_id='turn', audio=live_recording)],
                             20, RuntimeSettings())
        recording = (await registry.get_chat_history_db('chat'))[0]['audio']
        self.assertNotIn('audio', recording)
        self.assertEqual(recording['format'], 'ogg')
        self.assertEqual(recording['duration_ms'], 1000)
        mime, raw = await media_store.read_media('chat', recording['media_id'])
        self.assertEqual(mime, 'audio/ogg')
        self.assertLess(len(raw), len(wav.getvalue()) / 5)
        import av
        with av.open(io.BytesIO(raw)) as check:
            self.assertEqual(check.streams.audio[0].codec_context.name, 'opus')
            seconds = sum(frame.samples / frame.sample_rate for frame in check.decode(audio=0))
        self.assertAlmostEqual(seconds, 1, delta=.1)
        stored = await registry.get_turn_audio_db('chat', 'turn')
        self.assertEqual(stored, recording)
        self.assertEqual(base64.b64decode((await job_service._audio_payload('chat', stored))['audio']), raw)

    async def test_opus_failure_keeps_original_binary(self):
        source = dict(audio=base64.b64encode(b'original').decode(), format='wav')
        with patch.object(media_store, '_opus', side_effect=RuntimeError('no encoder')), self.assertLogs(level='ERROR'):
            ref = await media_store.store_audio('chat', source)
        self.assertEqual(await media_store.read_media('chat', ref['media_id']), ('audio/wav', b'original'))
        self.assertNotIn('audio', ref)

    async def test_admission_removes_inline_media_from_queue_state_and_echo(self):
        redis = Mock(exists=AsyncMock(return_value=False), hgetall=AsyncMock(return_value={}))
        redis.pipeline.return_value.execute = AsyncMock()
        request = JobCreateRequest(user_id='alice', chat_id='chat', input={
            'text': 'question', 'attachments': [self.attachment()],
            'metadata': {'attachments': [self.attachment()]}})
        with patch.object(job_service, 'check_chat_access', AsyncMock(return_value=ChatAccessResult(state='ok', meta={'name': 'chat'}))), \
             patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), \
             patch.object(tool_service, 'read_selection', AsyncMock(return_value={'enabled_tools': []})):
            result = await job_service.create_job(redis, request)
        queue = json.loads(redis.pipeline.return_value.xadd.call_args.kwargs['fields']['payload'])
        self.assertNotIn('data', queue['input']['attachments'][0])
        self.assertNotIn('attachments', queue['input']['metadata'])
        self.assertNotIn('data', result.attachments[0])
        self.assertNotIn('base64', str(redis.pipeline.return_value.mock_calls))
        redis.reset_mock()
        request = JobCreateRequest(user_id='alice', chat_id='chat', input={'text': 'question', 'attachments': [self.attachment()]})
        with patch.object(job_service, 'check_chat_access', AsyncMock(return_value=ChatAccessResult(state='ok', meta={'name': 'chat'}))), \
             patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), \
             patch.object(tool_service, 'read_selection', AsyncMock(return_value={'enabled_tools': []})), \
             patch.object(media_store, '_connection', AsyncMock(side_effect=RuntimeError('offline'))), self.assertLogs(level='ERROR'):
            with self.assertRaises(HTTPException) as error:
                await job_service.create_job(redis, request)
        self.assertEqual(error.exception.status_code, 503)
        redis.pipeline.assert_not_called()

    async def test_binary_endpoint_owner_check_range_and_head(self):
        ref = (await media_store.store_attachments('chat', 'alice', [self.attachment(b'0123456789')]))[0]
        def request(method='GET', headers=()):
            app = SimpleNamespace(state=SimpleNamespace(redis=Mock()))
            return Request(dict(type='http', method=method, headers=headers, app=app))
        with patch.object(routes, 'ensure_chat_access', AsyncMock(side_effect=HTTPException(403))), \
             patch.object(media_store, 'read_media', AsyncMock()) as read, \
             patch.object(media_store, 'read_media_metadata', AsyncMock()) as metadata:
            with self.assertRaises(HTTPException):
                await routes.chat_media_endpoint('chat', ref['media_id'], request(), 'bob')
            read.assert_not_called()
            metadata.assert_not_called()
        with patch.object(routes, 'ensure_chat_access', AsyncMock()):
            full = await routes.chat_media_endpoint('chat', ref['media_id'], request(), 'alice')
            with patch.object(media_store, 'read_media', AsyncMock(wraps=media_store.read_media)) as read:
                part = await routes.chat_media_endpoint('chat', ref['media_id'], request(headers=[(b'range', b'bytes=2-5')]), 'alice')
                read.assert_awaited_once_with('chat', ref['media_id'], offset=2, length=4)
            with patch.object(media_store, 'read_media', AsyncMock(side_effect=AssertionError('Payload must not be read'))) as read:
                head = await routes.chat_media_endpoint('chat', ref['media_id'], request('HEAD', [(b'range', b'bytes=2-5')]), 'alice')
                invalid = await routes.chat_media_endpoint('chat', ref['media_id'], request(headers=[(b'range', b'bytes=10-')]), 'alice')
                read.assert_not_called()
            for value, expected_status, expected_body in [
                (b'bytes=-3', 206, b'789'), (b'bytes=7-', 206, b'789'),
                (b'bytes=8-99999999999999999999999999999', 206, b'89'),
                (b'bytes=99999999999999999999999-', 416, b''),
                (b'bytes=-0', 416, b''), (b'bytes=5-2', 416, b''),
                (b'bytes=0-1,4-5', 200, b'0123456789'), (b'invalid', 200, b'0123456789'),
            ]:
                response = await routes.chat_media_endpoint('chat', ref['media_id'], request(headers=[(b'range', value)]), 'alice')
                self.assertEqual((response.status_code, response.body), (expected_status, expected_body))
        self.assertEqual(full.body, b'0123456789')
        self.assertEqual(part.body, b'2345')
        self.assertEqual(part.status_code, 206)
        self.assertEqual(head.body, b'')
        self.assertEqual(head.headers['content-length'], '10')
        self.assertEqual(invalid.status_code, 416)
        self.assertEqual(full.headers['cache-control'], 'private, no-store')

    async def age_media(self, *, hours=72):
        conn = await registry._connect()
        try:
            await conn.execute("update orion_chat_media set last_used_at=now()-$1::int * interval '1 hour'", hours)
        finally:
            await conn.close()

    async def test_orphan_cleanup_retains_messages_and_recent_media(self):
        refs = await media_store.store_attachments('chat', 'alice', [self.attachment(b'orphan'), self.attachment(b'attachment'),
                                                                  self.attachment(b'tts'), self.attachment(b'tool')])
        await registry.insert_messages('chat', [dict(role='user', content='hello', attachments=[refs[1]]),
            dict(role='assistant', content='reply', audio=refs[2]),
            dict(role='tool', content=json.dumps({'media_id': refs[3]['media_id']}))])
        await self.age_media()
        recent = (await media_store.store_attachments('chat', 'alice', [self.attachment(b'recent')]))[0]
        redis = Mock(exists=AsyncMock(return_value=0))
        self.assertEqual(await media_store.cleanup_orphan_media(redis), 1)
        with self.assertRaises(LookupError):
            await media_store.read_media('chat', refs[0]['media_id'])
        for ref in [*refs[1:], recent]:
            await media_store.read_media('chat', ref['media_id'])
        self.assertEqual(await self.count(), 4)

    async def test_orphan_cleanup_protects_redis_history_jobs_and_reused_references(self):
        ref = (await media_store.store_attachments('chat', 'alice', [self.attachment()]))[0]
        redis = Mock(exists=AsyncMock(return_value=1))
        for key in ['chat:history:chat', 'chat:state:chat', 'active_turn:chat']:
            await self.age_media()
            async def exists(*keys):
                return int(key in keys)
            redis.exists = AsyncMock(side_effect=exists)
            self.assertEqual(await media_store.cleanup_orphan_media(redis), 0)
        await self.age_media()
        await media_store.store_attachments('chat', 'alice', [ref])
        redis.exists = AsyncMock(return_value=0)
        self.assertEqual(await media_store.cleanup_orphan_media(redis), 0)
        self.assertEqual(await self.count(), 1)

    async def test_orphan_cleanup_is_bounded_skips_busy_chat_and_rolls_back_on_redis_error(self):
        refs = await media_store.store_attachments('chat', 'alice', [self.attachment(b'one'), self.attachment(b'two')])
        await media_store.store_attachments('other', 'bob', [self.attachment(b'three')])
        await self.age_media()
        lock = await registry._connect()
        transaction = lock.transaction()
        await transaction.start()
        try:
            await lock.fetchval("select id from orion_chats where id='chat' for update")
            self.assertEqual(await media_store.cleanup_orphan_media(Mock(exists=AsyncMock(return_value=0))), 1)
            self.assertEqual(await self.count(), 2)
        finally:
            await transaction.rollback()
            await lock.close()
        await media_store.store_attachments('other', 'bob', [self.attachment(b'four')])
        await self.age_media()
        with self.assertRaises(RuntimeError):
            await media_store.cleanup_orphan_media(Mock(exists=AsyncMock(side_effect=[0, RuntimeError('Redis offline')])))
        self.assertEqual(await self.count(), 3, 'Earlier deletes must roll back if cache safety checks fail')
        self.assertEqual(await media_store.cleanup_orphan_media(Mock(exists=AsyncMock(return_value=0)), limit=1), 1)
        self.assertEqual(await self.count(), 2)
        with self.assertRaises(ValueError):
            await media_store.cleanup_orphan_media(Mock(), limit=101)

    async def test_cleanup_loop_is_hourly_and_stops_when_worker_owner_changes(self):
        redis = Mock(get=AsyncMock(side_effect=['owner', 'replacement']))
        with patch.object(media_store.asyncio, 'sleep', AsyncMock()) as sleep, \
             patch.object(media_store, 'cleanup_orphan_media', AsyncMock(return_value=1)) as cleanup:
            await media_store.media_cleanup_loop(redis, 'owner')
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [3600, 3600])
        cleanup.assert_awaited_once_with(redis)


    async def test_delete_failed_exchange_preserves_success_and_shared_media(self):
        from orion.kernel.chat_history import failed_turn_key
        shared, unique = (await media_store.store_attachments('chat', 'alice', [
            self.attachment(b'shared'), self.attachment(b'unique')]))
        good = [{'role': 'user', 'content': 'keep', 'attachments': [shared]},
                {'role': 'assistant', 'content': 'answer'}]
        failed = [{'role': 'user', 'content': 'bad', 'turn_id': 'failed', 'attachments': [shared, unique]},
                  {'role': 'assistant', 'tool_calls': [{}]}, {'role': 'tool', 'content': 'result'},
                  {'role': 'assistant', 'content': 'error', 'generation_error': 'offline'}]
        other = [{'role': 'user', 'content': 'another failure'},
                 {'role': 'assistant', 'content': 'error', 'failed_turn': True}]
        await registry.insert_messages('chat', good + failed + other)
        self.assertFalse(await registry.delete_failed_turn_db('chat', 'failed', delete_empty=False))
        self.assertEqual(await registry.get_chat_history_db('chat'), good + other)
        self.assertEqual(await self.count(), 1)
        self.assertEqual((await media_store.read_media('chat', shared['media_id']))[1], b'shared')
        with self.assertRaises(LookupError):
            await media_store.read_media('chat', unique['media_id'])
        with self.assertRaises(ValueError):
            await registry.delete_failed_turn_db('chat', failed_turn_key(good), delete_empty=True)
        self.assertIsNotNone(await registry.get_chat_db('chat'))

    async def test_delete_last_legacy_failure_cascades_chat_and_media(self):
        from orion.kernel.chat_history import visible_history
        attachment = (await media_store.store_attachments('chat', 'alice', [self.attachment()]))[0]
        failed = [{'role': 'user', 'content': 'bad', 'attachments': [attachment]},
                  {'role': 'assistant', 'content': 'error', 'generation_error': 'offline'}]
        await registry.insert_messages('chat', failed)
        key = visible_history(failed)[-1]['failed_turn_key']
        self.assertTrue(await registry.delete_failed_turn_db('chat', key, delete_empty=True))
        self.assertIsNone(await registry.get_chat_db('chat'))
        self.assertEqual(await registry.get_chat_history_db('chat'), [])
        self.assertEqual(await self.count(), 0)


if __name__ == '__main__':
    unittest.main()
