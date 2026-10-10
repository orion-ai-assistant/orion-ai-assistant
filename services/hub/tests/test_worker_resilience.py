import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

from redis.exceptions import (
    AuthenticationError, AuthorizationError, ConnectionError, DataError,
    ResponseError, TimeoutError as RedisTimeoutError,
)

from orion.contracts.constants import CONSUMER_GROUP, STREAM_NAME
from orion.worker import main as worker


class WorkerResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These tests isolate the read/backoff loop; journal/recovery and locks
        # have separate real-Redis integration tests.
        for name, replacement in [
            ('SingleWorkerLock', MagicMock()),
            ('acquire_owner', AsyncMock(return_value='{"token":"test"}')),
            ('recover_pending', AsyncMock()),
        ]:
            patcher = patch.object(worker, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def execute(redis, stream_id, fields, consumer, owner):
            await worker.process_message(redis, stream_id, fields, consumer)
            await redis.xack(STREAM_NAME, CONSUMER_GROUP, stream_id)

        patcher = patch.object(worker, 'execute_job', execute)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_worker(self, redis):
        with patch.object(worker.Redis, 'from_url', return_value=redis) as factory, patch.object(
            worker, 'get_redis_url', return_value='redis://localhost:6379/0'
        ), patch.object(worker, 'get_runtime_settings', AsyncMock(
            return_value=SimpleNamespace(worker_max_concurrency=1)
        )), patch('orion.worker.tools._registry.get_registry'), patch.object(
            worker, 'close_session', AsyncMock()
        ):
            try:
                await worker.run()
            finally:
                self.assertGreater(factory.call_args.kwargs['socket_timeout'], worker.READ_BLOCK_MS / 1000)

    def redis(self, reads):
        redis = AsyncMock()
        redis.xreadgroup.side_effect = reads
        return redis

    async def test_transient_errors_retry_with_capped_backoff_and_reset(self):
        errors = [RedisTimeoutError('slow'), ConnectionError('offline'),
                  TimeoutError('slow'), OSError('socket'),
                  RedisTimeoutError('slow'), RedisTimeoutError('slow')]
        redis = self.redis([*errors, [], RedisTimeoutError('again'), asyncio.CancelledError()])
        with patch.object(worker.asyncio, 'sleep', AsyncMock()) as sleep, self.assertLogs(level='WARNING') as logs:
            with self.assertRaises(asyncio.CancelledError):
                await self.run_worker(redis)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.5, 1, 2, 4, 5, 5, 0.5])
        self.assertEqual(len(logs.output), 7)
        redis.xack.assert_not_awaited()
        redis.xgroup_create.assert_awaited_once()
        redis.close.assert_awaited_once()
        for call in redis.xreadgroup.await_args_list:
            self.assertEqual(call.kwargs['streams'], {STREAM_NAME: '>'})
            self.assertEqual(call.kwargs['groupname'], CONSUMER_GROUP)
            self.assertEqual(call.kwargs['block'], 2000)

    async def test_programming_configuration_and_auth_errors_propagate(self):
        for error in [ValueError('bug'), TypeError('bug'), DataError('bad args'),
                      ResponseError('NOGROUP'), AuthenticationError('bad credentials'),
                      AuthorizationError('denied')]:
            with self.subTest(error=type(error).__name__):
                redis = self.redis([error])
                with patch.object(worker.asyncio, 'sleep', AsyncMock()) as sleep:
                    with self.assertRaises(type(error)):
                        await self.run_worker(redis)
                sleep.assert_not_awaited()
                redis.xack.assert_not_awaited()
                redis.close.assert_awaited_once()

    async def test_recovery_processes_and_acks_each_received_message_once(self):
        redis = self.redis([RedisTimeoutError('slow'),
                            [(STREAM_NAME, [('1-0', {'message': 'one', 'turn_id': 'one'})])],
                            ConnectionError('offline'),
                            [(STREAM_NAME, [('2-0', {'message': 'two', 'turn_id': 'two'})])],
                            asyncio.CancelledError()])
        with patch.object(worker.asyncio, 'sleep', AsyncMock()), patch.object(
            worker, 'process_message', AsyncMock()
        ) as process:
            with self.assertRaises(asyncio.CancelledError):
                await self.run_worker(redis)
        self.assertEqual([call.args[1] for call in process.await_args_list], ['1-0', '2-0'])
        self.assertEqual([call.args for call in redis.xack.await_args_list], [
            (STREAM_NAME, CONSUMER_GROUP, '1-0'), (STREAM_NAME, CONSUMER_GROUP, '2-0')])

    async def test_cancellation_during_backoff_closes_without_retry(self):
        redis = self.redis([RedisTimeoutError('slow')])
        with patch.object(worker.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_worker(redis)
        self.assertEqual(redis.xreadgroup.await_count, 1)
        redis.xack.assert_not_awaited()
        redis.close.assert_awaited_once()

    async def test_read_retry_preserves_in_flight_processing(self):
        started = asyncio.Event()
        released = asyncio.Event()
        redis = AsyncMock()
        reads = 0

        async def read(**kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                return [(STREAM_NAME, [('1-0', {'message': 'one', 'turn_id': 'one'})])]
            if reads == 2:
                await started.wait()
                raise RedisTimeoutError('slow')
            raise asyncio.CancelledError()

        async def process(*args):
            started.set()
            await released.wait()

        async def backoff(delay):
            redis.xack.assert_not_awaited()
            released.set()

        redis.xreadgroup.side_effect = read
        with patch.object(worker.Redis, 'from_url', return_value=redis), patch.object(
            worker, 'get_redis_url', return_value='redis://localhost:6379/0'
        ), patch.object(worker, 'get_runtime_settings', AsyncMock(
            return_value=SimpleNamespace(worker_max_concurrency=2)
        )), patch('orion.worker.tools._registry.get_registry'), patch.object(
            worker, 'close_session', AsyncMock()
        ), patch.object(worker, 'process_message', AsyncMock(side_effect=process)) as handler, patch.object(
            worker.asyncio, 'sleep', AsyncMock(side_effect=backoff)
        ):
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(worker.run(), timeout=2)
        handler.assert_awaited_once()
        redis.xack.assert_awaited_once_with(STREAM_NAME, CONSUMER_GROUP, '1-0')

    def test_real_client_socket_timeout_exceeds_block(self):
        redis = worker.Redis.from_url(
            'redis://localhost:6379/0', decode_responses=True, protocol=2,
            socket_timeout=worker.REDIS_SOCKET_TIMEOUT)
        self.assertGreater(redis.connection_pool.connection_kwargs['socket_timeout'], worker.READ_BLOCK_MS / 1000)


if __name__ == '__main__':
    unittest.main()
