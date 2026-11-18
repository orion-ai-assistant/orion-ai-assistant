"""Memory step 1 (docs/hafiza-tasarimi.md §16): the schema step with its backup, the per-user memory_v2 setting, the
collections with the sorting of existing memories (reversible), the `in` filter of the page and the tool, and the
source links; each new table checked for user isolation. On a throwaway real PostgreSQL (test_memory_search)."""
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.api import routes
from orion.kernel import memory, memory_backup, memory_collections as mc, memory_groups, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import PostgresMemoryCase, postgres_bin


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class CollectionTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True)
        self.backup = AsyncMock(return_value=None)
        guard = patch.object(memory_backup, 'backup', self.backup)
        guard.start()
        self.addCleanup(guard.stop)

    async def memories(self, user='alice'):
        """A chat save, a note and a text file (3 pieces) of `user`."""
        chat = await self.add('Kullanıcı: kedim Maviş', group=f'{user}-chat', source='chat_save', user=user, axis=2)
        note = await self.add('kızımın adı Elif', group=f'{user}-note', user=user, axis=3)
        doc = [await self.add(f'tez parça {k}', group=f'{user}-doc', source='file', title='tez.md', user=user,
                              axis=4 + k) for k in range(3)]
        return chat, note, doc

    async def groups(self, settings, user='alice', **kwargs):
        page = await memory_groups.list_groups(user, settings, limit=20, **kwargs)
        return page, sorted(c['group_id'] for c in page['groups'])

    async def test_the_schema_step_is_recorded_and_runs_once(self):
        self.assertEqual(await sql("select value from orion_memory_meta where key = 'schema_version'"),
                         str(memory.SCHEMA_VERSION))
        for table in ('orion_mem_collection', 'orion_mem_collection_item', 'orion_mem_source'):
            self.assertTrue(await sql('select to_regclass($1) is not null', table))
        await self.memories()
        with patch.object(memory, '_table_ready', False):
            conn = await registry._connect()
            try:
                await memory.ensure_memory_table(conn)  # Version already reached: no new step, no dump.
            finally:
                await conn.close()
        self.backup.assert_not_awaited()

    async def test_a_schema_step_over_existing_rows_dumps_first(self):
        await self.memories()
        await sql("update orion_memory_meta set value = '1' where key = 'schema_version'")
        with patch.object(memory, '_table_ready', False):
            conn = await registry._connect()
            try:
                await memory.ensure_memory_table(conn)
            finally:
                await conn.close()
        self.backup.assert_awaited_once_with(f'schema-1-to-{memory.SCHEMA_VERSION}')
        self.assertEqual(await sql("select value from orion_memory_meta where key = 'schema_version'"),
                         str(memory.SCHEMA_VERSION))

    async def test_with_the_setting_off_nothing_changes(self):
        await self.memories()
        before, groups = await self.groups(self.settings)
        after, same = await self.groups(self.settings, collections=['documents'])  # Ignored with the setting off.
        self.assertEqual(groups, same)
        self.assertNotIn('collections', after)
        self.assertEqual(await sql('select count(*) from orion_mem_collection'), 0)
        self.assertEqual(await sql('select count(*) from orion_mem_collection_item'), 0)
        self.backup.assert_not_awaited()
        self.assertEqual(before['matching'], 3)

    async def test_turning_it_on_sorts_existing_memories_once_and_filters(self):
        await self.memories()
        page, groups = await self.groups(self.v2)
        self.assertEqual(groups, ['alice-chat', 'alice-doc', 'alice-note'])  # Every memory still listed.
        self.backup.assert_awaited_once_with('collections')
        names = [c['name'] for c in page['collections']]
        self.assertEqual(names, ['Ben', 'Yaşadıklarım', 'Belgeler'])  # The four fixed ones, no "Eski hafıza".
        rows = {r['group_id']: r['collection_id'] for r in await sql(
            'select group_id, collection_id from orion_mem_collection_item where main', fetch=True)}
        self.assertEqual(rows, {'alice-doc': 'belgeler'})  # Only files are sorted; the rest is plain memory, listed above.
        _, docs = await self.groups(self.v2, collections=['documents'])
        self.assertEqual(docs, ['alice-doc'])
        _, folded = await self.groups(self.v2, collections=['BELGELER'])  # By name, Turkish folding.
        self.assertEqual(folded, ['alice-doc'])
        with self.assertRaises(ValueError):
            await self.groups(self.v2, collections=['eski hafıza'])  # That collection is gone.
        found, hits = await self.groups(self.v2, q='tez', collections=['documents'])
        self.assertEqual(hits, ['alice-doc'])
        _, none = await self.groups(self.v2, q='tez', collections=['me'])
        self.assertEqual(none, [])
        with self.assertRaises(ValueError):
            await self.groups(self.v2, collections=['yok böyle'])
        await self.groups(self.v2)
        self.backup.assert_awaited_once()  # Sorted once.

    async def test_the_map_takes_the_pages_collection_filter(self):
        await self.memories()
        whole = await memory_groups.map_graph('alice', self.v2)
        self.assertEqual({n['group_id'] for n in whole['nodes']}, {'alice-chat', 'alice-note', 'alice-doc'})
        docs = await memory_groups.map_graph('alice', self.v2, collections=['documents'])
        self.assertEqual(({n['group_id'] for n in docs['nodes']}, docs['total']), ({'alice-doc'}, 1))
        nobody = await memory_groups.map_graph('alice', self.v2, collections=['me'], about=[])
        self.assertEqual(nobody['nodes'], [])
        off = await memory_groups.map_graph('alice', self.settings, collections=['documents'])  # Ignored with it off.
        self.assertEqual(len(off['nodes']), 3)

    async def test_new_rows_join_their_memory_s_collection_and_a_new_file_goes_to_documents(self):
        await self.memories()
        await self.groups(self.v2)
        main = "select collection_id from orion_mem_collection_item where main and group_id = $1"
        old = await sql(main, 'alice-note')
        await self.add('ikinci not', group='alice-note', axis=20)
        self.assertEqual(await sql(main, 'alice-note'), old)  # A part belongs to its memory.
        await self.add('yeni dosya', group='g-new-file', source='file', title='a.md', axis=21)
        await self.add('ikinci parça', group='g-new-file', source='file', title='a.md', axis=24)
        self.assertEqual(await sql(main, 'g-new-file'), 'belgeler')
        await self.add('yeni not', group='g-new-note', axis=22)
        self.assertIsNone(await sql(main, 'g-new-note'))
        await self.add('sonradan dosya', group='g-new-note', source='file', title='c.md', axis=25)
        self.assertIsNone(await sql(main, 'g-new-note'))  # Its first row was a note: not moved to Belgeler.
        await self.add('bob notu', group='g-bob', source='file', title='b.md', user='bob', axis=23)
        self.assertIsNone(await sql(main, 'g-bob'))  # Bob has no collections.

    async def test_revert_puts_everything_back(self):
        await self.memories()
        before, groups = await self.groups(self.settings)
        await self.groups(self.v2)
        with patch.object(routes, 'update_runtime_settings', AsyncMock()) as update:
            result = await routes.memory_collections_revert(SimpleNamespace(app=SimpleNamespace(
                state=SimpleNamespace(redis=None))), 'alice')
        update.assert_awaited_once_with(None, {'memory_v2': 'false'}, 'alice')
        self.assertEqual(result, {'memories': 1, 'collections': 3})
        self.assertEqual(await sql('select count(*) from orion_mem_collection_item'), 0)
        after, again = await self.groups(self.settings)
        self.assertEqual((groups, before['matching']), (again, after['matching']))
        await self.groups(self.v2)  # On again: sorted again.
        self.assertEqual(await sql("select count(*) from orion_mem_collection where user_id = 'alice'"), 3)

    async def test_users_never_see_each_other_s_collections_members_or_sources(self):
        await self.memories('alice')
        await self.memories('bob')
        await self.groups(self.v2, user='alice')
        await self.groups(self.v2, user='bob')
        # Alice puts her note into Belgeler, and names bob's memory too: membership is hers only.
        await sql("insert into orion_mem_collection_item (user_id, collection_id, group_id) values "
                  "('alice', 'belgeler', 'alice-note'), ('alice', 'belgeler', 'bob-note')")
        _, alice_docs = await self.groups(self.v2, user='alice', collections=['documents'])
        self.assertEqual(alice_docs, ['alice-doc', 'alice-note'])
        _, bob_docs = await self.groups(self.v2, user='bob', collections=['documents'])
        self.assertEqual(bob_docs, ['bob-doc'])
        conn = await registry._connect()
        try:
            await mc.add_source(conn, 'alice', 'fact', 7, chat_id='chat', first_message=5, last_message=3)
            self.assertEqual(await mc.sources_of(conn, 'bob', 'fact', 7), [])
            self.assertEqual(await mc.sources_from_chat(conn, 'bob', 'chat'), [])
            mine = await mc.sources_of(conn, 'alice', 'fact', 7)
            self.assertEqual((mine[0]['first_message'], mine[0]['last_message']), (3, 5))
            with self.assertRaises(ValueError):
                await mc.add_source(conn, 'alice', 'fact', 8)  # A source must say where from.
            with self.assertRaises(ValueError):
                await mc.add_source(conn, 'alice', 'note', 8, by_user=True)
        finally:
            await conn.close()
        result = await mc.revert('alice')
        self.assertEqual(result['collections'], 3)
        self.assertEqual(await sql("select count(*) from orion_mem_collection where user_id = 'bob'"), 3)
        self.assertEqual(await sql("select count(*) from orion_mem_collection_item where user_id = 'bob'"), 1)

    async def test_the_tool_takes_in(self):
        await self.memories()
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.v2)):
            context = ToolContext('alice', 'chat', 't', None)
            args = memory_tools.MemorySearchInput.model_validate({'in': ['documents'], 'queries': [{'text': 'parça'}]})
            found = await memory_tools.search_memory(args, context)
            self.assertEqual({r['label'].split(':')[0] for r in found['results']}, {'tez.md'})
            wrong = await memory_tools.search_memory(
                memory_tools.MemorySearchInput.model_validate({'in': ['nowhere']}), context)
            self.assertIn('Unknown collection', wrong['error'])
        schema = memory_tools.MemorySearchInput.model_json_schema()
        self.assertIn('in', schema['properties'])
        self.assertNotIn('in_', schema['properties'])


class BackupTests(PostgresMemoryCase):
    async def test_a_dump_is_made_restorable_and_only_the_newest_are_kept(self):
        binary = postgres_bin()
        pg_dump = binary / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
        if not pg_dump.exists():
            self.skipTest('no pg_dump beside the test PostgreSQL')
        await self.add('yedeklenecek', group='g1')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ORION_PG_DUMP': str(pg_dump)}):
            made = []
            for k in range(memory_backup.KEEP + 2):
                with patch.object(memory_backup, 'dt') as clock:
                    clock.datetime.now.return_value.strftime.return_value = f'20261117-12000{k}'
                    made.append(await memory_backup.backup('test step', self.url, Path(folder)))
            self.assertTrue(all(made))
            kept = sorted(p.name for p in Path(folder).glob('*.dump'))
            self.assertEqual(len(kept), memory_backup.KEEP)
            self.assertEqual(kept[-1], made[-1].name)
            self.assertIn('test_step', kept[-1])
            listing = await asyncio.create_subprocess_exec(
                str(binary / ('pg_restore.exe' if os.name == 'nt' else 'pg_restore')), '-l', str(made[-1]),
                stdout=asyncio.subprocess.PIPE)
            out, _ = await listing.communicate()
            self.assertIn(b'orion_memory', out)
            self.assertIn(b'orion_mem_source', out)
            self.assertNotIn(b'orion_chats', out)

    async def test_without_pg_dump_or_with_a_failing_one_nothing_is_left_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(memory_backup, 'find_pg_dump', return_value=None):
                self.assertIsNone(await memory_backup.backup('x', self.url, Path(folder)))
            binary = postgres_bin()
            pg_dump = binary / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
            if pg_dump.exists():
                with patch.object(memory_backup, 'find_pg_dump', return_value=str(pg_dump)):
                    self.assertIsNone(await memory_backup.backup('x', self.url.replace('/postgres', '/nope'),
                                                                 Path(folder)))
            self.assertEqual(list(Path(folder).iterdir()), [])
