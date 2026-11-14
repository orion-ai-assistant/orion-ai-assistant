"""One copy per file, linked from its owners (orion_media + orion_media_links).

Runs on a NEW temporary PostgreSQL cluster (the one test_chat_media_storage builds), never the live database.
"""
import asyncio
import base64
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

import test_chat_media_storage as base
from orion.api import routes
from orion.kernel import media_migration, media_store, registry


def attachment(raw=b'shared bytes', mime='image/png'):
    return dict(name='photo.png', mime_type=mime, isText=False,
                data=f'data:{mime};base64,' + base64.b64encode(raw).decode())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class MediaLinkCase(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(base.ChatMediaTests.setUpClass.__func__)
    tearDownClass = classmethod(base.ChatMediaTests.tearDownClass.__func__)

    async def asyncSetUp(self):
        async def connection(*args, **kwargs):
            return await asyncpg.connect(self.url, command_timeout=10)
        self.connection = connection
        for guard in (patch.object(registry, '_connect', connection), patch.object(registry, '_tables_ready', False)):
            guard.start()
            self.addCleanup(guard.stop)
        conn = await connection()
        try:
            await registry._ensure_tables(conn)
            await media_store.ensure_table(conn)
            await conn.execute('drop table if exists orion_chat_media')
            await conn.execute('truncate orion_chats, orion_media cascade')
            await conn.execute("insert into orion_chats(id,user_id) values"
                               "('chat','alice'),('chat2','alice'),('other','bob')")
        finally:
            await conn.close()

    async def fetchval(self, query, *args):
        conn = await self.connection()
        try:
            return await conn.fetchval(query, *args)
        finally:
            await conn.close()

    async def blobs(self):
        return await self.fetchval('select count(*) from orion_media')

    async def links(self):
        return await self.fetchval('select count(*) from orion_media_links')

    async def delete_chat(self, chat_id):
        conn = await self.connection()
        try:
            await conn.execute('delete from orion_chats where id=$1', chat_id)
        finally:
            await conn.close()


class SharedMediaTests(MediaLinkCase):
    async def test_the_same_file_in_several_chats_is_stored_once(self):
        refs = [(await media_store.store_attachments(chat, user, [attachment()]))[0]
                for chat, user in (('chat', 'alice'), ('chat2', 'alice'), ('other', 'bob'))]
        self.assertEqual(await self.blobs(), 1)
        self.assertEqual(await self.links(), 3)
        self.assertEqual({ref['media_id'] for ref in refs}, {sha(b'shared bytes')})
        # The URL shape the app caches by stays per chat.
        self.assertEqual([ref['media_path'] for ref in refs],
                         [f'/api/v1/chats/{chat}/media/{sha(b"shared bytes")}' for chat in ('chat', 'chat2', 'other')])
        for chat in ('chat', 'chat2', 'other'):
            self.assertEqual(await media_store.read_media(chat, refs[0]['media_id']), ('image/png', b'shared bytes'))
        self.assertEqual(await self.fetchval("select attstorage::text from pg_attribute where attrelid = 'orion_media'::regclass "
                                             "and attname = 'data'"), 'e')
        self.assertEqual(await self.fetchval('select size from orion_media'), len(b'shared bytes'))

    async def test_deleting_a_chat_keeps_a_file_linked_elsewhere_and_the_last_link_takes_it(self):
        ref = (await media_store.store_attachments('chat', 'alice', [attachment()]))[0]
        await media_store.store_attachments('chat2', 'alice', [attachment()])
        await self.delete_chat('chat')
        self.assertEqual((await self.blobs(), await self.links()), (1, 1))
        self.assertEqual(await media_store.read_media('chat2', ref['media_id']), ('image/png', b'shared bytes'))
        with self.assertRaises(LookupError):
            await media_store.read_media('chat', ref['media_id'])
        await self.delete_chat('chat2')
        self.assertEqual((await self.blobs(), await self.links()), (0, 0))

    async def test_a_memory_link_keeps_the_file_after_its_chats_are_gone(self):
        ref = (await media_store.store_attachments('chat', 'alice', [attachment()]))[0]
        await media_store.link_media(ref['media_id'], 'memory', 'm1')
        await media_store.link_media(ref['media_id'], 'memory', 'm1')  # Linking twice is one link.
        await self.delete_chat('chat')
        self.assertEqual((await self.blobs(), await self.links()), (1, 1))
        self.assertFalse(await media_store.unlink_media(ref['media_id'], 'memory', 'other-memory'))
        self.assertEqual(await self.blobs(), 1)
        self.assertTrue(await media_store.unlink_media(ref['media_id'], 'memory', 'm1'))
        self.assertEqual((await self.blobs(), await self.links()), (0, 0))
        with self.assertRaises(LookupError):
            await media_store.link_media(ref['media_id'], 'memory', 'm1')
        with self.assertRaises(ValueError):
            await media_store.link_media(ref['media_id'], 'album', 'x')
        with self.assertRaises(ValueError):
            await media_store.unlink_media(ref['media_id'], 'memory', '')

    async def test_a_file_with_links_cannot_be_deleted_directly(self):
        await media_store.store_attachments('chat', 'alice', [attachment()])
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self.fetchval('delete from orion_media')

    async def test_orphan_cleanup_drops_the_chat_link_but_a_memory_keeps_the_file(self):
        kept, gone = await media_store.store_attachments('chat', 'alice', [attachment(b'kept'), attachment(b'gone')])
        await media_store.link_media(kept['media_id'], 'memory', 'm1')
        await self.fetchval("update orion_media_links set last_used_at = now() - interval '72 hours'")
        self.assertEqual(await media_store.cleanup_orphan_media(Mock(exists=AsyncMock(return_value=0))), 2)
        self.assertEqual(await self.blobs(), 1)
        self.assertEqual(await self.fetchval('select id from orion_media'), kept['media_id'])
        self.assertEqual(await self.fetchval("select owner_kind from orion_media_links"), 'memory')
        with self.assertRaises(LookupError):
            await media_store.read_media('chat', kept['media_id'])  # The chat no longer links it.

    async def test_reusing_a_reference_needs_this_chats_link(self):
        ref = (await media_store.store_attachments('chat', 'alice', [attachment()]))[0]
        with self.assertRaises(ValueError):
            await media_store.store_attachments('chat2', 'alice', [ref])  # Same user, other chat: not linked there.
        again = await media_store.store_attachments('chat', 'alice', [ref, attachment(b'new')])
        self.assertEqual(again[0], ref)
        self.assertEqual(again[1]['media_id'], sha(b'new'))


class RaceTests(MediaLinkCase):
    async def test_concurrent_writers_of_one_file_store_it_once(self):
        files = [attachment(b'one'), attachment(b'two'), attachment(b'three')]
        jobs = []
        for index in range(6):
            chat, user = (('chat', 'alice'), ('chat2', 'alice'), ('other', 'bob'))[index % 3]
            # Opposite orders in parallel: writers lock files in id order, so they never deadlock.
            jobs.append(media_store.store_attachments(chat, user, files if index % 2 else files[::-1]))
        await asyncio.gather(*jobs)
        self.assertEqual((await self.blobs(), await self.links()), (3, 9))

    async def test_a_writer_holding_the_file_keeps_it_through_a_concurrent_last_link_delete(self):
        await media_store.store_attachments('chat', 'alice', [attachment()])
        writer = await self.connection()
        try:
            transaction = writer.transaction()
            await transaction.start()
            await writer.fetchval("select id from orion_chats where id='chat2' for update")
            await media_store._put(writer, 'chat2', 'image/png', b'shared bytes')
            deletion = asyncio.create_task(self.delete_chat('chat'))
            await asyncio.sleep(0.5)
            self.assertFalse(deletion.done(), 'The last-link delete must wait for the writer')
            await transaction.commit()
            await asyncio.wait_for(deletion, 10)
        finally:
            await writer.close()
        self.assertEqual((await self.blobs(), await self.links()), (1, 1))
        self.assertEqual(await media_store.read_media('chat2', sha(b'shared bytes')), ('image/png', b'shared bytes'))

    async def test_a_writer_after_an_uncommitted_last_link_delete_stores_the_file_again(self):
        await media_store.store_attachments('chat', 'alice', [attachment()])
        deleter = await self.connection()
        try:
            transaction = deleter.transaction()
            await transaction.start()
            await deleter.execute("delete from orion_chats where id='chat'")  # Trigger deleted the file, uncommitted.
            write = asyncio.create_task(media_store.store_attachments('chat2', 'alice', [attachment()]))
            await asyncio.sleep(0.5)
            self.assertFalse(write.done(), 'The writer must wait for the deletion to finish')
            await transaction.commit()
            ref = (await asyncio.wait_for(write, 10))[0]
        finally:
            await deleter.close()
        self.assertEqual((await self.blobs(), await self.links()), (1, 1))
        self.assertEqual(await media_store.read_media('chat2', ref['media_id']), ('image/png', b'shared bytes'))

    async def test_writes_and_deletes_racing_never_lose_a_linked_file(self):
        conn = await self.connection()
        try:
            await conn.executemany('insert into orion_chats(id,user_id) values($1, $2)',
                                   [(f'{kind}{index}', 'alice') for kind in ('old', 'new') for index in range(10)])
        finally:
            await conn.close()
        for index in range(10):
            await media_store.store_attachments(f'old{index}', 'alice', [attachment(b'a'), attachment(b'b')])
        # Every old chat goes while new chats link the same files: at some point a deletion is the last link.
        jobs = []
        for index in range(10):
            jobs.append(media_store.store_attachments(f'new{index}', 'alice', [attachment(b'b'), attachment(b'a')]))
            jobs.append(self.delete_chat(f'old{index}'))
        await asyncio.gather(*jobs)
        self.assertEqual((await self.blobs(), await self.links()), (2, 20))
        for index in range(10):
            for raw in (b'a', b'b'):
                self.assertEqual((await media_store.read_media(f'new{index}', sha(raw)))[1], raw)


class NotFoundTests(MediaLinkCase):
    async def test_every_refusal_is_the_same_404(self):
        ref = (await media_store.store_attachments('chat', 'alice', [attachment()]))[0]
        stray = (await media_store.store_attachments('other', 'bob', [attachment(b'bob only')]))[0]
        def request(etag=None):
            app = SimpleNamespace(state=SimpleNamespace(redis=Mock()))
            headers = [(b'if-none-match', f'"{etag}"'.encode())] if etag else []
            return Request(dict(type='http', method='GET', headers=headers, app=app))
        cases = [
            ('chat', ref['media_id'], 'bob'),        # another user's chat
            ('chat2', ref['media_id'], 'alice'),     # own chat that does not link the file
            ('chat', stray['media_id'], 'alice'),    # a file that exists, but only for someone else
            ('chat', 'f' * 64, 'alice'),             # a hash nobody stored
            ('missing', ref['media_id'], 'alice'),   # a chat that is not there
            ('chat', 'not-a-hash', 'alice'),
        ]
        answers = set()
        for chat_id, media_id, user in cases:
            for etag in (None, media_id):  # A matching ETag never turns a refusal into a 304.
                with self.assertRaises(HTTPException) as raised:
                    await routes.chat_media_endpoint(chat_id, media_id, request(etag), user)
                answers.add((raised.exception.status_code, raised.exception.detail, str(raised.exception.headers)))
        self.assertEqual(answers, {(404, 'Media not found', 'None')})
        allowed = await routes.chat_media_endpoint('chat', ref['media_id'], request(), 'alice')
        self.assertEqual(allowed.body, b'shared bytes')


LEGACY_TABLE = """
    create table orion_chat_media (
        chat_id text not null references orion_chats(id) on delete cascade,
        id text not null,
        mime_type text not null,
        data bytea not null,
        created_at timestamptz not null default now(),
        last_used_at timestamptz not null default now(),
        primary key(chat_id, id)
    )
"""


class LegacyTableMigrationTests(MediaLinkCase):
    async def seed_legacy(self):
        conn = await self.connection()
        try:
            await conn.execute(LEGACY_TABLE)
            rows = [('chat', sha(b'shared'), 'image/png', b'shared', 5),
                    ('chat2', sha(b'shared'), 'image/png', b'shared', 1),
                    ('other', sha(b'shared'), 'image/png', b'shared', 3),
                    ('chat', sha(b'unique'), 'audio/ogg', b'unique', 2),
                    ('chat2', sha(b'damaged'), 'image/png', b'not what the hash says', 1)]
            await conn.executemany("""insert into orion_chat_media(chat_id, id, mime_type, data, last_used_at)
                values($1, $2, $3, $4, now() - $5::int * interval '1 hour')""", rows)
        finally:
            await conn.close()

    async def migrate(self):
        conn = await self.connection()
        try:
            return await media_migration.migrate_chat_media_table(conn)
        finally:
            await conn.close()

    async def gaps(self):
        conn = await self.connection()
        try:
            return await media_migration.legacy_chat_media_gaps(conn)
        finally:
            await conn.close()

    async def test_moves_once_dedupes_and_is_idempotent(self):
        await self.seed_legacy()
        with self.assertLogs(media_migration.logger, level='WARNING'):
            self.assertEqual(await self.migrate(), {'moved': 4, 'skipped': 1})
        self.assertEqual((await self.blobs(), await self.links()), (2, 4))
        self.assertEqual(await self.gaps(), 1)  # Only the damaged row, which stays in the old table.
        self.assertEqual(await self.fetchval('select count(*) from orion_chat_media'), 5)  # Old table kept.
        for chat in ('chat', 'chat2', 'other'):
            self.assertEqual(await media_store.read_media(chat, sha(b'shared')), ('image/png', b'shared'))
        self.assertEqual(await media_store.read_media('chat', sha(b'unique')), ('audio/ogg', b'unique'))
        # last_used_at comes along: the cleaner's 48 hours keep counting from the old use.
        self.assertTrue(await self.fetchval("""select last_used_at < now() - interval '4 hours' from orion_media_links
            where owner_id = 'chat' and media_id = $1""", sha(b'shared')))
        with self.assertLogs(media_migration.logger, level='WARNING'):
            self.assertEqual(await self.migrate(), {'moved': 0, 'skipped': 1})
        self.assertEqual((await self.blobs(), await self.links()), (2, 4))
        # A link removed after the move is not brought back by a later run.
        await media_store.unlink_media(sha(b'unique'), 'chat', 'chat')
        with self.assertLogs(media_migration.logger, level='WARNING'):
            await self.migrate()
        self.assertEqual((await self.blobs(), await self.links()), (1, 3))

    async def test_an_interrupted_move_resumes_where_it_stopped(self):
        await self.seed_legacy()
        original, calls = media_migration._move_rows, []
        async def stop_on_second(conn, rows, result):
            calls.append(rows[0]['chat_id'])
            if len(calls) == 2:
                raise ConnectionError('Hub stopped')
            await original(conn, rows, result)
        with patch.object(media_migration, '_move_rows', stop_on_second):
            with self.assertRaises(ConnectionError):
                await self.migrate()
        self.assertEqual(await self.fetchval('select count(*) from orion_chat_media where migrated_at is not null'), 2)
        self.assertEqual(await self.fetchval('select count(*) from pg_locks where locktype = $1', 'advisory'), 0)
        with self.assertLogs(media_migration.logger, level='WARNING'):
            self.assertEqual(await self.migrate(), {'moved': 2, 'skipped': 1})
        self.assertEqual((await self.blobs(), await self.links()), (2, 4))
        self.assertEqual(await self.gaps(), 1)

    async def test_one_process_moves_while_the_others_wait(self):
        await self.seed_legacy()
        holder = await self.connection()
        try:
            await holder.execute('select pg_advisory_lock($1)', media_migration.TABLE_LOCK)
            waiting = asyncio.create_task(self.migrate())
            await asyncio.sleep(0.5)
            self.assertFalse(waiting.done())
            await holder.execute('select pg_advisory_unlock($1)', media_migration.TABLE_LOCK)
            with self.assertLogs(media_migration.logger, level='WARNING'):
                self.assertEqual((await asyncio.wait_for(waiting, 10))['moved'], 4)
        finally:
            await holder.close()

    async def test_a_deleted_chat_takes_its_old_rows_and_nothing_is_left_dangling(self):
        await self.seed_legacy()
        await self.delete_chat('chat2')
        with self.assertNoLogs(media_migration.logger, level='WARNING'):
            self.assertEqual(await self.migrate(), {'moved': 3, 'skipped': 0})
        self.assertEqual((await self.blobs(), await self.links()), (2, 3))
        self.assertEqual(await self.gaps(), 0)
        self.assertEqual(await self.migrate(), {'moved': 0, 'skipped': 0})

    async def test_without_an_old_table_there_is_nothing_to_do(self):
        self.assertEqual(await self.migrate(), {'moved': 0, 'skipped': 0})
        self.assertEqual(await self.gaps(), 0)


if __name__ == '__main__':
    unittest.main()
