"""Crash/ownership/ACK integration tests against an isolated Redis 5 process."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from redis import Redis as SyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError

from orion.contracts.constants import CONSUMER_GROUP, STREAM_NAME
from orion.kernel import job_safety as safety
from orion.worker import main as worker
from orion.worker.services.job_processor import finalize_state_if_active


CHILD = r'''
import asyncio, json, os, socket, sys
from redis.asyncio import Redis
from orion.kernel.job_safety import SingleWorkerLock, acquire_owner, mark_started
from orion.worker.main import ensure_consumer_group, execute_job
from orion.worker.services.job_processor import finalize_state_if_active
from orion.contracts.constants import STREAM_NAME, CONSUMER_GROUP
from unittest.mock import patch

async def main():
    redis = Redis.from_url(sys.argv[1], decode_responses=True, protocol=2)
    owner = await acquire_owner(redis)
    consumer = socket.gethostname() + ':' + json.loads(owner)['token']
    await ensure_consumer_group(redis)
    stage = sys.argv[2]
    if stage == 'before_read': os._exit(17)
    response = await redis.xreadgroup(CONSUMER_GROUP, consumer, {STREAM_NAME:'>'}, count=1)
    stream_id, fields = response[0][1][0]
    if stage == 'after_read': os._exit(17)
    assert await mark_started(redis, fields, owner)
    if stage == 'after_started': os._exit(17)
    await redis.incr('external:' + stage)
    if stage != 'after_result': os._exit(17)
    assert await finalize_state_if_active(redis, fields['chat_id'], fields['turn_id'],
        'chat:state:' + fields['chat_id'],
        {'status':'completed','updated_at':'2026-10-10T00:00:00+00:00',
         'result_json':json.dumps({'text':'saved answer'})}, 86400)
    os._exit(17)

with SingleWorkerLock(sys.argv[3]):
    # The isolated test Redis must not conflict with the user's live Hub.
    with patch('orion.kernel.job_safety.psutil.process_iter', return_value=[]):
        asyncio.run(main())
'''


class PendingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='orion-redis-test-')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        cls.url = f'redis://127.0.0.1:{port}/0'
        executable = Path(__file__).resolve().parents[3] / '.local_db/redis/redis-server.exe'
        cls.server = subprocess.Popen(
            [str(executable), '--bind', '127.0.0.1', '--port', str(port), '--save', '', '--appendonly', 'no'],
            cwd=cls.directory.name, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == 'nt' else 0)
        client = SyncRedis.from_url(cls.url, socket_timeout=0.2, protocol=2)
        try:
            for _ in range(100):
                try:
                    client.ping()
                    cls.version = client.info()['redis_version']
                    break
                except ConnectionError:
                    if cls.server.poll() is not None:
                        raise RuntimeError('Isolated Redis exited before test startup')
                    time.sleep(.05)
            else:
                raise RuntimeError('Isolated Redis did not become ready')
        except BaseException:
            cls.server.terminate()
            cls.server.wait(timeout=10)
            cls.directory.cleanup()
            raise
        finally:
            client.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.redis = Redis.from_url(self.url, decode_responses=True, protocol=2, socket_timeout=2)
        await self.redis.flushdb()
        await worker.ensure_consumer_group(self.redis)
        self.fields = {'chat_id': 'chat', 'turn_id': 'turn', 'channel': 'room:user:test',
                       'user_id': 'test', 'created_at': safety.utc_now(), 'payload': '{}'}
        pipe = self.redis.pipeline()
        pipe.hset('chat:state:chat', mapping={
            'chat_id': 'chat', 'active_turn_id': 'turn', 'status': 'queued',
            'created_at': safety.utc_now(), 'updated_at': safety.utc_now(),
            'channel': self.fields['channel'], 'current_prompt': 'hello',
            'current_user_message': json.dumps({'role': 'user', 'content': 'hello', 'turn_id': 'turn'})})
        pipe.set('active_turn:chat', 'turn', ex=120)
        pipe.hset(safety.job_key('turn'), mapping={
            'phase': 'queued', 'fields': json.dumps(self.fields)})
        pipe.zadd(safety.WAITING_KEY, {'turn': time.time() + 60})
        await pipe.execute()
        self.stream_id = await self.redis.xadd(STREAM_NAME, self.fields)

    async def asyncTearDown(self):
        await self.redis.aclose()

    async def owner(self):
        with patch.object(safety.psutil, 'process_iter', return_value=[]):
            return await safety.acquire_owner(self.redis)

    async def crash(self, stage):
        child = subprocess.Popen([sys.executable, '-c', CHILD, self.url, stage,
                                  str(Path(self.directory.name) / 'worker.lock')],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            stdout, stderr = await asyncio.to_thread(child.communicate, timeout=15)
            self.assertEqual(child.returncode, 17, (stdout + stderr).decode(errors='replace'))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)

    async def successful_process(self, *args):
        await finalize_state_if_active(self.redis, 'chat', 'turn', 'chat:state:chat',
            {'status': 'completed', 'updated_at': safety.utc_now(), 'result_json': '{"text":"answer"}'}, 86400)

    async def test_redis_5_and_crash_before_read_is_safe(self):
        self.assertTrue(self.version.startswith('5.'))
        await self.crash('before_read')
        owner = await self.owner()
        await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)
        reads = await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname() + ':new', {STREAM_NAME: '>'})
        with patch.object(worker, 'process_message', AsyncMock(side_effect=self.successful_process)) as process:
            await worker.execute_job(self.redis, *reads[0][1][0], 'new', owner)
        process.assert_awaited_once()

    async def test_crash_after_receipt_before_started_recovers_once(self):
        await self.crash('after_read')
        owner = await self.owner()
        with patch.object(worker, 'process_message', AsyncMock(side_effect=self.successful_process)) as process:
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        process.assert_awaited_once()
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)

    async def test_started_router_and_tool_crashes_never_replay(self):
        for stage in ['after_started', 'router', 'tool']:
            with self.subTest(stage=stage):
                if stage != 'after_started':
                    await self.asyncSetUp()
                await self.crash(stage)
                owner = await self.owner()
                with patch.object(worker, 'process_message', AsyncMock()) as process:
                    await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
                process.assert_not_awaited()
                self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')
                self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'terminal')
                self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)
                if stage != 'after_started':
                    self.assertEqual(await self.redis.get('external:' + stage), '1')
                self.assertTrue(await self.redis.lrange('chat:history:chat', 0, -1))
                if stage != 'tool':
                    await self.redis.aclose()

    async def test_crash_after_saved_result_only_acks(self):
        await self.crash('after_result')
        owner = await self.owner()
        with patch.object(worker, 'process_message', AsyncMock()) as process:
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        process.assert_not_awaited()
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'completed')
        self.assertEqual(await self.redis.get('external:after_result'), '1')
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)

    async def test_second_worker_cannot_claim_active_work(self):
        owner = await self.owner()
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname() + ':live', {STREAM_NAME: '>'})
        self.assertTrue(await safety.mark_started(self.redis, self.fields, owner))
        with self.assertRaises(RuntimeError):
            await self.owner()
        await safety.sweep_waiting(self.redis)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'processing')
        self.assertEqual((await self.redis.xpending_range(STREAM_NAME, CONSUMER_GROUP, '-', '+', 1))[0]['consumer'], socket.gethostname() + ':live')

    async def test_atomic_owner_race_has_one_winner(self):
        results = await asyncio.gather(self.owner(), self.owner(), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, str) for item in results), 1)
        self.assertEqual(sum(isinstance(item, RuntimeError) for item in results), 1)

    async def test_remote_owner_cannot_be_stolen(self):
        await self.redis.set(safety.OWNER_KEY, json.dumps({'host': 'other-host', 'pid': 1, 'created': 1}))
        with self.assertRaises(RuntimeError):
            await self.owner()

    async def test_windows_launcher_ancestor_is_not_a_second_worker(self):
        from types import SimpleNamespace
        parent = SimpleNamespace(pid=os.getppid(), info={'cmdline': ['python.exe', '-m', 'orion.worker.main']})
        with patch.object(safety.psutil, 'process_iter', return_value=[parent]):
            owner = await safety.acquire_owner(self.redis)
        self.assertEqual(json.loads(owner)['pid'], os.getpid())

    async def test_marker_write_failure_prevents_router_and_ack(self):
        owner = await self.owner()
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        with patch.object(worker, 'mark_started', AsyncMock(side_effect=TimeoutError('write lost'))), patch.object(
            worker, 'process_message', AsyncMock()) as process:
            with self.assertRaises(TimeoutError):
                await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        process.assert_not_awaited()
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 1)

    async def test_terminal_write_failure_keeps_pending_and_never_replays_started(self):
        owner = await self.owner()
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        with patch.object(worker, 'process_message', AsyncMock()), patch.object(
            worker, 'fail_job', AsyncMock(side_effect=ConnectionError('offline'))):
            with self.assertRaises(ConnectionError):
                await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 1)
        await self.redis.delete(safety.OWNER_KEY)
        owner = await self.owner()
        with patch.object(worker, 'process_message', AsyncMock()) as process:
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        process.assert_not_awaited()

    async def test_queue_timeout_without_worker_and_status_survives_missed_pubsub(self):
        await self.redis.zadd(safety.WAITING_KEY, {'turn': time.time() - 1})
        await safety.sweep_waiting(self.redis)
        from orion.api.services import job_service
        with patch.object(job_service, 'ensure_chat_access', AsyncMock()):
            status = await job_service.get_job(self.redis, 'test', 'chat')
        self.assertEqual(status.status, 'failed')
        self.assertEqual(status.turn_id, 'turn')
        self.assertEqual(status.retry_text, 'hello')
        self.assertIn('kuyrukta', status.error)
        owner = await self.owner()
        with patch.object(worker, 'process_message', AsyncMock()) as process:
            await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        process.assert_not_awaited()

    async def test_legacy_pending_is_failed_not_replayed(self):
        await self.redis.delete(safety.job_key('turn'))
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        owner = await self.owner()
        with patch.object(worker, 'process_message', AsyncMock()) as process:
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        process.assert_not_awaited()
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')

    async def test_timeout_race_cannot_abort_started_job(self):
        owner = await self.owner()
        self.assertTrue(await safety.mark_started(self.redis, self.fields, owner))
        self.assertEqual(await safety.fail_job(self.redis, self.fields, safety.QUEUE_TIMEOUT, 'queued'), 0)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'processing')

    async def test_ack_rejects_nonterminal_journal(self):
        owner = await self.owner()
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        result = await self.redis.eval(worker.ACK_TERMINAL, 3, safety.OWNER_KEY,
            safety.job_key('turn'), STREAM_NAME, owner, CONSUMER_GROUP, self.stream_id)
        self.assertEqual(result, 0)
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 1)

    async def test_ambiguous_started_write_never_launches_external_work(self):
        owner = await self.owner()
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})

        async def lost_response(*args):
            self.assertTrue(await safety.mark_started(*args))
            raise TimeoutError('Marker written, response lost')

        with patch.object(worker, 'mark_started', AsyncMock(side_effect=lost_response)), patch.object(
            worker, 'process_message', AsyncMock()) as process:
            with self.assertRaises(TimeoutError):
                await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        process.assert_not_awaited()
        self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'started')
        with patch.object(worker, 'process_message', AsyncMock()) as process:
            await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        process.assert_not_awaited()
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')

    async def test_duplicate_entries_wait_for_active_turn_and_both_ack(self):
        owner = await self.owner()
        duplicate_id = await self.redis.xadd(STREAM_NAME, self.fields)
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'}, count=2)
        started, release = asyncio.Event(), asyncio.Event()

        async def process(*args):
            started.set()
            await release.wait()
            await self.successful_process()

        active = {}
        with patch.object(worker, 'process_message', AsyncMock(side_effect=process)) as handler:
            first = asyncio.create_task(worker.execute_serialized(self.redis, self.stream_id, self.fields, 'new', owner, active))
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(worker.execute_serialized(self.redis, duplicate_id, self.fields, 'new', owner, active))
            await asyncio.sleep(.05)
            self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'processing')
            self.assertEqual(handler.await_count, 1)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assertEqual(handler.await_count, 1)
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)
        self.assertEqual(active, {})

    async def test_remote_legacy_pending_is_not_claimed(self):
        await self.redis.xreadgroup(CONSUMER_GROUP, 'remote-host', {STREAM_NAME: '>'})
        owner = await self.owner()
        await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        pending = await self.redis.xpending_range(STREAM_NAME, CONSUMER_GROUP, '-', '+', 1)
        self.assertEqual(pending[0]['consumer'], 'remote-host')
        self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'queued')

    async def test_legacy_queue_timeout_is_indexed_without_worker(self):
        await self.redis.delete(safety.job_key('turn'), safety.WAITING_KEY)
        await self.redis.hset('chat:state:chat', 'created_at', '2020-01-01T00:00:00+00:00')
        await safety.index_legacy_queued(self.redis)
        self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'legacy')
        await safety.sweep_waiting(self.redis)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')

    async def test_api_detects_dead_worker_without_worker_recovery(self):
        await self.crash('tool')
        await safety.sweep_waiting(self.redis)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')
        self.assertEqual(await self.redis.get('external:tool'), '1')

    async def test_real_redis_connection_drop_keeps_pending_recoverable(self):
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff
        reader = Redis.from_url(self.url, decode_responses=True, protocol=2,
                                client_name='drop-test', retry=Retry(NoBackoff(), 0))
        # Consume the first message, then block waiting for another.
        await reader.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        waiting = asyncio.create_task(reader.xreadgroup(CONSUMER_GROUP, socket.gethostname(),
                                                       {STREAM_NAME: '>'}, block=5000))
        try:
            await asyncio.sleep(.05)
            clients = await self.redis.client_list()
            connection = next(client for client in clients if client['name'] == 'drop-test')
            await self.redis.client_kill(connection['addr'])
            with self.assertRaises(ConnectionError):
                await asyncio.wait_for(waiting, 2)
            owner = await self.owner()
            with patch.object(worker, 'process_message', AsyncMock(side_effect=self.successful_process)) as process:
                await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
            process.assert_awaited_once()
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            await reader.aclose()

    async def test_retried_admission_cannot_clear_started_or_terminal_marker(self):
        owner = await self.owner()
        self.assertTrue(await safety.mark_started(self.redis, self.fields, owner))
        self.assertEqual(await self.redis.eval(safety.INIT_JOURNAL, 1, safety.job_key('turn'),
            'turn', 'chat', json.dumps(self.fields)), 0)
        self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'started')
        await self.successful_process()
        await self.redis.eval(safety.INIT_JOURNAL, 1, safety.job_key('turn'), 'turn', 'chat', json.dumps(self.fields))
        await self.redis.hset('chat:state:chat', 'status', 'queued')
        await self.redis.zadd(safety.WAITING_KEY, {'turn': time.time() + 60})
        await safety.sweep_waiting(self.redis)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'completed')
        self.assertEqual(json.loads(await self.redis.hget('chat:state:chat', 'result_json'))['text'], 'answer')

    async def test_real_processor_observes_started_before_router_and_commits_before_ack(self):
        from orion.worker.services import job_processor
        from orion.contracts.settings import RuntimeSettings
        self.fields['payload'] = json.dumps({'user_id': 'test', 'input': {'text': 'hello'}})
        self.fields['enabled_tools'] = '[]'
        # The already-read stream entry uses the same identifiers; execute the
        # validated request payload through the actual production processor.
        await self.redis.xreadgroup(CONSUMER_GROUP, socket.gethostname(), {STREAM_NAME: '>'})
        owner = await self.owner()
        observations = []

        async def router(*args, **kwargs):
            observations.append(await self.redis.hget(safety.job_key('turn'), 'phase'))
            yield 'content', 'hello answer'

        with patch.object(job_processor, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings(tts_enabled=False))), patch.object(
            job_processor, 'get_chat_history_db', AsyncMock(return_value=[])), patch.object(
            job_processor, 'insert_messages', AsyncMock()), patch.object(
            job_processor, 'generate_chat_title', AsyncMock()), patch.object(
            job_processor, 'llama_stream_chat_typed', router):
            await worker.execute_job(self.redis, self.stream_id, self.fields, 'new', owner)
        self.assertEqual(observations, ['started'])
        self.assertEqual(await self.redis.hget(safety.job_key('turn'), 'phase'), 'terminal')
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'completed')
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)

    async def test_pubsub_failure_does_not_lose_terminal_error(self):
        await self.crash('tool')
        owner = await self.owner()
        with patch.object(self.redis, 'publish', AsyncMock(side_effect=ConnectionError('notification offline'))):
            await worker.recover_pending(self.redis, socket.gethostname() + ':new', owner)
        self.assertEqual(await self.redis.hget('chat:state:chat', 'status'), 'failed')
        self.assertIn('belirsiz', await self.redis.hget('chat:state:chat', 'error'))
        self.assertEqual((await self.redis.xpending(STREAM_NAME, CONSUMER_GROUP))['pending'], 0)

    async def test_api_lifespan_runs_watchdog_without_worker(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from orion.api import main as api
        app = SimpleNamespace(state=SimpleNamespace(redis=self.redis))

        @asynccontextmanager
        async def services(app):
            yield

        await self.redis.zadd(safety.WAITING_KEY, {'turn': time.time() - 1})
        with patch.object(api, 'service_lifespan', services):
            async with api.lifespan(app):
                for _ in range(50):
                    if await self.redis.hget('chat:state:chat', 'status') == 'failed':
                        break
                    await asyncio.sleep(.01)
                else:
                    self.fail('API watchdog did not run')

    async def test_api_admission_records_safe_queue_journal_atomically(self):
        from orion.api.services import job_service
        from orion.contracts.http import JobCreateRequest
        from orion.contracts.settings import RuntimeSettings
        with patch.object(job_service, 'get_runtime_settings', AsyncMock(return_value=RuntimeSettings())), patch.object(
            job_service, 'upsert_chat', AsyncMock()):
            admitted = await job_service.create_job(self.redis, JobCreateRequest(user_id='test', input={'text': 'new request'}))
        journal = await self.redis.hgetall(safety.job_key(admitted.turn_id))
        self.assertEqual(journal['phase'], 'queued')
        self.assertEqual(json.loads(journal['fields']), {
            'chat_id': admitted.chat_id, 'turn_id': admitted.turn_id, 'channel': 'room:user:test'})
        self.assertIsNotNone(await self.redis.zscore(safety.WAITING_KEY, admitted.turn_id))
        queued = await self.redis.xrevrange(STREAM_NAME, count=1)
        self.assertEqual(queued[0][1]['turn_id'], admitted.turn_id)


class WorkerFileLockTests(unittest.TestCase):
    def test_two_processes_cannot_hold_lock_and_crash_releases_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'worker.lock')
            code = ('import sys,time; from orion.kernel.job_safety import SingleWorkerLock; '
                    'lock=SingleWorkerLock(sys.argv[1]); lock.__enter__(); print("ready",flush=True); time.sleep(60)')
            child = subprocess.Popen([sys.executable, '-c', code, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                line = child.stdout.readline().strip()
                if line != b'ready':
                    child.wait(timeout=10)
                    self.fail(child.stderr.read().decode(errors='replace'))
                with self.assertRaises(RuntimeError):
                    with safety.SingleWorkerLock(path):
                        pass
            finally:
                child.kill()
                child.communicate(timeout=10)
            with safety.SingleWorkerLock(path):
                pass
