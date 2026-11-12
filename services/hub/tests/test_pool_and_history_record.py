"""PostgreSQL is the one record of chat history: pooled connections, retried saves, no Redis copy."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from orion.api.services import job_service
from orion.contracts.settings import RuntimeSettings
from orion.kernel import registry
from orion.worker.services import job_processor


class FakePool:
    def __init__(self):
        self.released = []
        self.acquired = 0
        self.closed = False

    def is_closing(self):
        return self.closed

    async def acquire(self, timeout=None):
        self.acquired += 1
        return Mock(name=f'conn{self.acquired}')

    async def release(self, conn):
        self.released.append(conn)

    async def close(self):
        self.closed = True

    def terminate(self):
        self.closed = True


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        registry._pools.clear()
        self.created = []

        async def create_pool(url, **kwargs):
            pool = FakePool()
            self.created.append((url, kwargs, pool))
            return pool

        for guard in (patch.object(registry.asyncpg, 'create_pool', create_pool),
                      patch.object(registry, 'get_postgres_url', lambda: 'postgresql://x/y')):
            guard.start()
            self.addCleanup(guard.stop)
        self.addCleanup(registry._pools.clear)

    async def test_close_returns_the_connection_to_the_pool_and_the_pool_is_reused(self):
        first = await registry._connect()
        conn = first._conn
        await first.close()
        await first.close()  # a second close does not release twice
        pool = self.created[0][2]
        self.assertEqual(pool.released, [conn])
        second = await registry._connect()
        await second.close()
        self.assertEqual(len(self.created), 1, 'one pool per event loop')
        self.assertEqual(pool.acquired, 2)
        self.assertEqual(self.created[0][1]['max_size'], registry.POOL_MAX)
        self.assertEqual(self.created[0][1]['min_size'], 0)

    async def test_attributes_come_from_the_underlying_connection(self):
        pooled = await registry._connect()
        self.assertIs(pooled.fetch, pooled._conn.fetch)

    async def test_a_changed_address_gets_a_new_pool_and_the_old_one_is_dropped(self):
        await (await registry._connect()).close()
        with patch.object(registry, 'get_postgres_url', lambda: 'postgresql://other/y'):
            await (await registry._connect()).close()
        self.assertEqual(len(self.created), 2)
        self.assertTrue(self.created[0][2].closed)

    async def test_close_pool_closes_this_loop_pool_and_a_new_call_opens_another(self):
        await (await registry._connect()).close()
        await registry.close_pool()
        self.assertTrue(self.created[0][2].closed)
        await (await registry._connect()).close()
        self.assertEqual(len(self.created), 2)

    async def test_an_unreachable_database_returns_none(self):
        async def fail(*args, **kwargs):
            raise OSError('down')

        with patch.object(registry.asyncpg, 'create_pool', fail):
            self.assertIsNone(await registry._connect(retries=2, delay=0))

    async def test_another_event_loop_gets_its_own_pool(self):
        await (await registry._connect()).close()

        async def other_loop():
            await (await registry._connect()).close()

        await asyncio.get_running_loop().run_in_executor(None, lambda: asyncio.run(other_loop()))
        self.assertEqual(len(self.created), 2)


class AppendHistoryTests(unittest.IsolatedAsyncioTestCase):
    def redis(self):
        redis = MagicMock()
        redis.pipeline.return_value.execute = AsyncMock()
        redis.rpush = AsyncMock()
        redis.lrange = AsyncMock(return_value=[])
        return redis

    async def test_a_short_database_outage_is_waited_out(self):
        redis = self.redis()
        messages = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}]
        insert = AsyncMock(side_effect=[RuntimeError('down'), RuntimeError('down'), None])
        sleep = AsyncMock()
        with patch.object(job_processor, 'insert_messages', insert), patch.object(job_processor.asyncio, 'sleep', sleep), \
                self.assertLogs(level='WARNING'):
            await job_processor.append_history(redis, 'chat', messages, 20, RuntimeSettings())
        self.assertEqual(insert.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.5, 1.0])
        redis.pipeline.assert_not_called()
        redis.rpush.assert_not_called()

    async def test_it_gives_up_after_the_last_try_without_raising(self):
        redis = self.redis()
        insert = AsyncMock(side_effect=RuntimeError('down'))
        sleep = AsyncMock()
        with patch.object(job_processor, 'insert_messages', insert), patch.object(job_processor.asyncio, 'sleep', sleep), \
                self.assertLogs(level='WARNING') as logs:
            await job_processor.append_history(redis, 'chat', [{'role': 'user', 'content': 'q'}], 20, RuntimeSettings())
        self.assertEqual(insert.await_count, 5)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.5, 1.0, 2.0, 4.0])
        self.assertTrue(any('could not be saved' in line for line in logs.output))
        redis.pipeline.assert_not_called()

    async def test_a_saved_turn_is_written_once_and_never_to_redis(self):
        redis = self.redis()
        insert = AsyncMock()
        with patch.object(job_processor, 'insert_messages', insert):
            await job_processor.append_history(redis, 'chat', [{'role': 'user', 'content': 'q'}], 20, RuntimeSettings())
        insert.assert_awaited_once()
        redis.pipeline.assert_not_called()
        redis.rpush.assert_not_called()


class HistoryReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_worker_model_history_reads_only_postgres(self):
        redis = Mock()
        stored = [{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}, {'content': 'no role'}]

        async def hydrate(chat, messages):
            return messages

        with patch('orion.kernel.registry.get_chat_tail_db', AsyncMock(return_value=(stored, 0))) as db, \
                patch.object(job_processor, 'hydrate_model_media', hydrate):
            history = await job_processor.load_history(redis, 'chat', 20, 3600)
        db.assert_awaited_once_with('chat', 200)
        self.assertEqual(history, stored[:2])
        self.assertEqual(redis.mock_calls, [])

    async def test_a_database_error_gives_an_empty_model_history(self):
        redis = Mock()
        with patch('orion.kernel.registry.get_chat_tail_db', AsyncMock(side_effect=RuntimeError('down'))), \
                self.assertLogs(level='ERROR'):
            self.assertEqual(await job_processor.load_history(redis, 'chat', 20, 3600), [])
        self.assertEqual(redis.mock_calls, [])

    async def test_the_api_reads_only_postgres_and_leaves_redis_alone(self):
        redis = Mock()
        stored = [{'role': 'user', 'content': 'hello'}]
        with patch.object(job_service, 'get_chat_history_db', AsyncMock(return_value=stored)):
            self.assertEqual(await job_service._load_history('chat'), stored)
        with patch.object(job_service, 'get_chat_history_db', AsyncMock(side_effect=RuntimeError('down'))), \
                self.assertLogs(level='ERROR'):
            self.assertEqual(await job_service._load_history('chat'), [])
        self.assertEqual(redis.mock_calls, [])
