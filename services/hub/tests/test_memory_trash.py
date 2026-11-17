"""Memory step 2 (docs/hafiza-tasarimi.md §11.2, §16): remove -> trash (out of every list, search, map, count and tool;
restorable), the chat delete confirmation through the trash, deleting for good (every copy, the file with its last
link, the confirmation's preview) and the periodic purge by each user's trash days; user isolation of all of it. On
a throwaway real PostgreSQL (test_memory_search)."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from orion.api import routes
from orion.kernel import media_store, memory, memory_groups, memory_pending, memory_trash, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import SPACE, PostgresMemoryCase, png, vec

OTHER = 'jina-v5'


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class TrashTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings = SimpleNamespace(router_model_group='chat', memory_trash_days=30)

    async def image(self, group, user='alice', axis=5):
        raw = png() + group.encode()  # Its own bytes: its own stored file.
        media_id = hashlib.sha256(raw).hexdigest()
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await media_store.hold_media(conn, media_id, 'image/png', raw)
        finally:
            await conn.close()
        row = await self.add('kalp.png — bu ne', score=0.4, kind='image', media_id=media_id, axis=axis, group=group,
                             user=user)
        await media_store.link_media(media_id, 'memory', str(row))
        return row, media_id

    async def long_text(self, group='g-long', user='alice'):
        """A text item of three pieces and its whole vector (the mean of the pieces)."""
        pieces = [await self.add(f'uzun metin parça {k}', score=0.1 * k, axis=10 + k, group=group, user=user)
                  for k in range(3)]
        await sql("update orion_memory set item_id = 'item-1', item_order = id where id = any($1::bigint[])", pieces)
        whole = await self.add('uzun metin', axis=30, group=group, user=user)
        await sql("update orion_memory set item_id = 'item-1', whole = true where id = $1", whole)
        return pieces, whole

    async def listed(self, user='alice', **kwargs):
        page = await memory_groups.list_groups(user, self.settings, limit=20, **kwargs)
        return page, sorted(c['group_id'] for c in page['groups'])

    async def test_a_removed_memory_is_gone_everywhere_until_restored(self):
        text = await self.add('kedim Maviş', group='g1', score=0.9, axis=2)
        image, media_id = await self.image('g1')
        await self.add('başka bir not', group='g2', axis=3)
        before, groups = await self.listed()
        self.assertEqual((groups, before['summary']['total']), (['g1', 'g2'], 2))
        result = await routes.delete_memory_items(routes.MemoryDelete(group_ids=['g1']), 'alice')
        self.assertEqual(sorted(result['deleted']), sorted([text, image]))
        trash_id = result['trash_id']
        page, groups = await self.listed()
        self.assertEqual((groups, page['summary']['total'], page['summary']['per_kind']), (['g2'], 1, {'text': 1}))
        _, found = await self.listed(q='maviş')
        self.assertEqual(found, ['g2'] if found else [])
        self.assertNotIn('g1', found)
        self.assertIsNone(await memory_groups.group_detail('alice', 'g1', self.settings))
        graph = await memory_groups.map_graph('alice', self.settings)
        self.assertEqual([n['group_id'] for n in graph['nodes']], ['g2'])
        self.assertIsNone(await memory_groups.memory_context('alice', text, self.settings))
        with self.assertRaises(LookupError):  # A memory in the trash takes no new parts.
            conn = await registry._connect()
            try:
                await memory.join_group(conn, 'alice', 'g1')
            finally:
                await conn.close()
        self.assertEqual(await sql('select count(*) from orion_media where id = $1', media_id), 1)  # File kept.
        trash = await memory_trash.list_trash('alice', self.settings)
        self.assertEqual(len(trash['entries']), 1)
        entry = trash['entries'][0]
        self.assertEqual((entry['trash_id'], entry['scope'], entry['group_id'], entry['days_left'], trash['days']),
                         (trash_id, 'memory', 'g1', 30, 30))
        self.assertEqual(entry['card']['counts'], {'text': 1, 'image': 1})
        restored = await routes.memory_trash_restore(routes.MemoryTrashIds(trash_ids=[trash_id]), 'alice')
        self.assertEqual((sorted(restored['restored']), restored['groups']), (sorted([text, image]), ['g1']))
        after, groups = await self.listed()
        self.assertEqual((groups, after['summary']), (['g1', 'g2'], before['summary']))
        self.assertEqual((await memory_trash.list_trash('alice', self.settings))['entries'], [])

    async def test_a_removed_part_takes_its_copies_and_the_whole_vector_follows_the_live_pieces(self):
        pieces, whole = await self.long_text()
        copy = await self.add('uzun metin parça 0', space=OTHER, group='g-long', copied_from=pieces[0], axis=40)
        removed = await memory_trash.trash_items('alice', [pieces[0]])
        self.assertEqual([r['id'] for r in removed['removed']], [pieces[0]])
        states = {r['id']: r['gone'] for r in await sql(
            'select id, exists (select 1 from orion_mem_trash t where t.memory_id = id) as gone from orion_memory',
            fetch=True)}
        self.assertEqual((states[pieces[0]], states[copy], states[pieces[1]], states[whole]), (True, True, False, False))
        # The whole vector is the mean of the two live pieces now.
        cos = await sql("select 1 - (w.embedding <=> l2_normalize((select avg(embedding) from orion_memory "
                        "where id = any($2::bigint[])))) from orion_memory w where w.id = $1", whole, pieces[1:])
        self.assertGreater(cos, 0.9999)
        trash = (await memory_trash.list_trash('alice', self.settings))['entries']
        self.assertEqual((len(trash), trash[0]['scope']), (1, 'part'))  # The memory itself is still there.
        rest = await memory_trash.trash_whole_items('alice', ['item-1'])
        self.assertTrue(await sql('select exists (select 1 from orion_mem_trash where memory_id = $1)', whole))
        self.assertEqual(await sql('select trash_id from orion_mem_trash where memory_id = $1', whole), rest['trash_id'])
        await memory_trash.restore('alice', [removed['trash_id']])  # One piece back: the whole comes back with it.
        self.assertFalse(await sql('select exists (select 1 from orion_mem_trash where memory_id = $1)', whole))
        await memory_trash.restore('alice', [rest['trash_id']])
        cos = await sql("select 1 - (w.embedding <=> l2_normalize((select avg(embedding) from orion_memory "
                        "where id = any($2::bigint[])))) from orion_memory w where w.id = $1", whole, pieces)
        self.assertGreater(cos, 0.9999)
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)

    async def test_the_chat_confirmation_moves_to_the_trash_and_the_tools_no_longer_see_it(self):
        text = await self.add('kedim Maviş', group='g1', score=0.9, axis=2)
        other = await self.add('köpeğim Karabaş', group='g2', score=0.8, axis=3)
        pending = await memory_pending.create('alice', 'chat', [text], whole=True)
        done = await routes.memory_pending_confirm(pending['token'], 'alice')
        self.assertEqual((done['state'], done['deleted']), ('deleted', [text]))
        self.assertTrue(done['trash_id'])
        self.assertTrue(await sql('select exists (select 1 from orion_mem_trash where memory_id = $1)', text))
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            context = ToolContext('alice', 'chat', 't', None)
            found = await memory_tools.search_memory(memory_tools.MemorySearchInput(), context)
            self.assertEqual([r['id'] for r in found['results']], [other])
            gone = await memory_tools.memory_context(memory_tools.MemoryContextInput(id=text), context)
            self.assertNotIn('parts', gone)
            again = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[text]), context)
            self.assertIn('note', again)  # Already in the trash: nothing to ask about.
            outcome = await memory_tools.delete_memory(
                memory_tools.MemoryDeleteInput(confirmation_id=pending['token']), context)
            self.assertIn('trash', outcome['note'])
        self.assertEqual(done['trash'], 'in')
        await memory_trash.restore('alice', [done['trash_id']])
        self.assertFalse(await sql('select exists (select 1 from orion_mem_trash where memory_id = $1)', text))
        self.assertEqual((await memory_pending.status('alice', pending['token']))['trash'], 'restored')
        again = await memory_trash.trash_items('alice', [text])
        await memory_trash.purge('alice', [again['trash_id']])
        self.assertEqual((await memory_pending.status('alice', pending['token']))['trash'], 'gone')

    async def test_deleting_for_good_takes_every_copy_the_file_and_the_confirmation_label(self):
        text = await self.add('kedim Maviş', group='g1', axis=2)
        copy = await self.add('kedim Maviş', space=OTHER, group='g1', copied_from=text, axis=3)
        image, media_id = await self.image('g1')
        kept, kept_media = await self.image('g2', axis=6)
        pending = await memory_pending.create('alice', 'chat', [text], whole=False)
        await memory_pending.resolve('alice', pending['token'], False)  # Declined, but its label names the text.
        removed = await memory_trash.trash_groups('alice', ['g1'])
        with self.assertRaises(HTTPException):  # Never without the explicit confirmation.
            await routes.memory_trash_purge(routes.MemoryTrashIds(trash_ids=[removed['trash_id']]),
                                            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
                                            'alice')
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=self.settings)):
            result = await routes.memory_trash_purge(
                routes.MemoryTrashIds(trash_ids=[removed['trash_id']], confirm=True),
                SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))), 'alice')
        self.assertEqual(sorted(result['deleted']), sorted([text, copy, image]))
        self.assertEqual(await sql('select count(*) from orion_memory where id = any($1::bigint[])',
                                   [text, copy, image]), 0)
        self.assertEqual(await sql('select count(*) from orion_media where id = $1', media_id), 0)  # File gone.
        self.assertEqual(await sql('select count(*) from orion_media where id = $1', kept_media), 1)
        self.assertEqual((await memory_pending.status('alice', pending['token']))['will_delete'], [])
        self.assertEqual(await sql('select count(*) from orion_memory where id = $1', kept), 1)

    async def test_a_live_row_is_never_deleted_for_good(self):
        text = await self.add('kedim Maviş', group='g1', axis=2)
        removed = await memory_trash.trash_items('alice', [text])
        await memory_trash.restore('alice', [removed['trash_id']])
        self.assertEqual((await memory_trash.purge('alice', [removed['trash_id']]))['deleted'], [])
        self.assertEqual(await sql('select count(*) from orion_memory where id = $1', text), 1)

    async def test_the_purge_job_takes_what_is_older_than_each_user_s_days(self):
        old = await self.add('eski', group='g-old', axis=2)
        new = await self.add('yeni', group='g-new', axis=3)
        bobs = await self.add('bob eski', group='g-bob', user='bob', axis=4)
        for user, row in (('alice', old), ('alice', new), ('bob', bobs)):
            await memory_trash.trash_items(user, [row])
        await sql("update orion_mem_trash set deleted_at = now() - interval '10 days' where memory_id = any($1::bigint[])",
                  [old, bobs])
        days = {'alice': 7, 'bob': 30}

        async def settings_of(user):
            return SimpleNamespace(router_model_group='chat', memory_trash_days=days[user])
        self.assertEqual(await memory_trash.purge_expired(settings_of), 1)
        left = {r['id'] for r in await sql('select id from orion_memory', fetch=True)}
        self.assertEqual(left, {new, bobs})  # Bob keeps his for 30 days.
        entry = (await memory_trash.list_trash('alice', SimpleNamespace(router_model_group='chat',
                                                                        memory_trash_days=7)))['entries'][0]
        self.assertEqual(entry['days_left'], 7)

    async def test_users_never_touch_each_other_s_trash(self):
        alice = await self.add('alice notu', group='g-a', axis=2)
        bob = await self.add('bob notu', group='g-b', user='bob', axis=3)
        self.assertEqual((await memory_trash.trash_items('bob', [alice]))['removed'], [])
        self.assertEqual((await memory_trash.trash_groups('bob', ['g-a']))['removed'], [])
        removed = await memory_trash.trash_items('alice', [alice])
        await memory_trash.trash_items('bob', [bob])
        self.assertEqual([e['group_id'] for e in (await memory_trash.list_trash('bob', self.settings))['entries']],
                         ['g-b'])
        self.assertEqual((await memory_trash.restore('bob', [removed['trash_id']]))['restored'], [])
        self.assertEqual((await memory_trash.purge('bob', [removed['trash_id']]))['deleted'], [])
        await memory_trash.purge('bob')  # Bob empties his whole trash: alice's stays.
        self.assertEqual(await sql('select count(*) from orion_memory where id = $1', alice), 1)
        self.assertEqual(await sql('select count(*) from orion_memory where id = $1', bob), 0)
        self.assertEqual((await memory_trash.restore('alice', [removed['trash_id']]))['restored'], [alice])
