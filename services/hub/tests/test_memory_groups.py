"""Memory cards (groups), the context tool, the move to another embedding model and the map, on a throwaway real
PostgreSQL (see test_memory_search.PostgresMemoryCase)."""
import asyncio
import datetime as dt
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.api import routes
from orion.contracts.tools import ToolSelection
from orion.kernel import media_store, memory, memory_groups, memory_move, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import JINA, SPACE, PostgresMemoryCase, png, vec

OTHER = 'jina-v5'


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class MemoryGroupTests(PostgresMemoryCase):
    async def image(self, group, space=SPACE, score=0.3, axis=5, minute=0, title=None, source='chat_save', **extra):
        raw = png()
        media_id = hashlib.sha256(raw).hexdigest()
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await media_store.hold_media(conn, media_id, 'image/png', raw)
        finally:
            await conn.close()
        row = await self.add('kalp.png — bu ne', score=score, kind='image', media_id=media_id, axis=axis, group=group,
                             space=space, minute=minute, title=title, source=source, **extra)
        await media_store.link_media(media_id, 'memory', str(row))
        return row, media_id

    # --- grouping -----------------------------------------------------------------------------------------------

    async def test_backfill_groups_a_chat_save_with_its_images_file_chunks_and_keeps_notes_apart(self):
        text = await self.add('Kullanıcı: bu ne\n\nAsistan: kırmızı kalp', source='chat_save', title='bu ne')
        image, _ = await self.image(None, title='bu ne', minute=0)
        await sql("update orion_memory set created_at = created_at + interval '40 seconds' where id = $1", image)
        later = await self.add('Kullanıcı: başka', source='chat_save', title='bu ne', minute=10)
        chunks = [await self.add(f'parça {k}', source='file', title='notlar.md', seconds=k) for k in range(3)]
        notes = [await self.add('not bir', minute=20), await self.add('not iki', minute=21)]
        conn = await registry._connect()
        try:
            self.assertEqual(await memory_groups.backfill_groups(conn), 8)
            self.assertEqual(await memory_groups.backfill_groups(conn), 0)  # Once.
        finally:
            await conn.close()
        groups = {r['id']: r['group_id'] for r in await sql('select id, group_id from orion_memory', fetch=True)}
        self.assertEqual(groups[text], groups[image])
        self.assertNotEqual(groups[text], groups[later])
        self.assertEqual(len({groups[c] for c in chunks}), 1)
        self.assertNotEqual(groups[notes[0]], groups[notes[1]])

    async def test_a_chat_save_and_a_file_are_one_card_each_and_a_hit_names_its_part(self):
        text = await self.add('Kullanıcı: kalpli resim\n\nAsistan: kırmızı bir kalp', score=0.2, group='g1',
                              source='chat_save', title='kalpli resim')
        image, _ = await self.image('g1', score=0.6, title='kalpli resim')
        chunks = [await self.add(f'notlar parça {k}', score=0.1 + 0.05 * k, axis=10 + k, group='g2', source='file',
                                 title='notlar.md') for k in range(3)]
        page = await memory_groups.list_groups('alice', self.settings, limit=10)
        self.assertEqual(page['matching'], 2)
        cards = {c['group_id']: c for c in page['groups']}
        self.assertEqual((cards['g1']['kind'], cards['g1']['counts']), ('mixed', {'text': 1, 'image': 1}))
        self.assertEqual(cards['g1']['text_part'], text)
        self.assertEqual([p['id'] for p in cards['g1']['parts']], [image])
        self.assertEqual((cards['g2']['part_count'], cards['g2']['title'], cards['g2']['text_index']), (3, 'notlar.md', 1))
        found = await memory_groups.list_groups('alice', self.settings, q='görsel', limit=10)
        self.assertEqual([(c['group_id'], c['matched_part']) for c in found['groups']], [('g1', image), ('g2', chunks[2])])
        self.assertEqual(found['groups'][1]['text_index'], 3)  # The chunk that matched, not the first.
        self.assertEqual((found['groups'][0]['strength'], found['groups'][1]['strength']), ('strong', 'weak'))

    async def test_a_copy_in_the_active_model_lists_the_memory_once_and_deleting_a_part_takes_its_copies(self):
        original = await self.add('kedimin adı Maviş', space=OTHER, group='g1', axis=2)
        copy = await self.add('kedimin adı Maviş', score=0.7, group='g1', copied_from=original, axis=3)
        await self.add('yalnız eski modelde', space=OTHER, group='g2', axis=4)
        page = await memory_groups.list_groups('alice', self.settings, limit=10)
        self.assertEqual(page['matching'], 2)
        card = next(c for c in page['groups'] if c['group_id'] == 'g1')
        self.assertEqual((card['text_part'], card['vector_spaces'], card['searchable']), (copy, [SPACE, OTHER], True))
        summary = page['summary']
        self.assertEqual((summary['total'], summary['not_searchable']), (2, 1))
        self.assertEqual((summary['movable'], summary['copied']), ({OTHER: 1}, {OTHER: 1}))
        found = await memory_groups.list_groups('alice', self.settings, q='maviş', limit=10)
        self.assertEqual([c['group_id'] for c in found['groups']], ['g1'])  # Text match in both spaces: one card.
        detail = await memory_groups.group_detail('alice', 'g1', self.settings)
        self.assertEqual([(p['id'], p['spaces']) for p in detail['parts']], [(copy, [SPACE, OTHER])])
        self.assertEqual([r['id'] for r in await memory.delete_items('alice', [copy])], [copy])
        self.assertEqual(await sql("select count(*) from orion_memory where group_id = 'g1'"), 0)

    async def test_the_detail_names_the_source_chat_and_whole_memories_are_deleted(self):
        await self.add('Kullanıcı: merhaba', group='g1', source='chat_save', chat='chat')
        image, media_id = await self.image('g1')
        detail = await memory_groups.group_detail('alice', 'g1', self.settings)
        self.assertEqual(detail['source_chat'], {'id': 'chat', 'exists': True, 'title': 'New Chat'})
        self.assertEqual(detail['parts'][1]['media'], True)
        self.assertIsNone(await memory_groups.group_detail('bob', 'g1', self.settings))
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=self.settings)):
            result = await routes.delete_memory_items(routes.MemoryDelete(group_ids=['g1']), 'alice')
        self.assertEqual(len(result['deleted']), 2)
        self.assertEqual(await sql('select count(*) from orion_media_links where media_id = $1', media_id), 0)

    # --- tools --------------------------------------------------------------------------------------------------

    async def tool(self, handler, model, **kwargs):
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            return await handler(model(**kwargs), ToolContext('alice', 'chat', 't', None))

    async def test_search_results_say_what_was_saved_with_them(self):
        text = await self.add('Kullanıcı: kalpli resim\n\nAsistan: kırmızı bir kalp', score=0.2, group='g1',
                              source='chat_save', title='kalpli resim')
        image, _ = await self.image('g1', score=0.6)
        await self.add('uzun dosya ilk parça', score=0.0, group='g2', source='file', title='notlar.md', axis=8)
        chunk = await self.add('uzun dosya ikinci parça', score=0.5, group='g2', source='file', title='notlar.md',
                               axis=7)
        found = await self.tool(memory_tools.search_memory, memory_tools.MemorySearchInput,
                                queries=[memory_tools.MemoryQuery(text='kalp resmi')])
        rows = {row['id']: row for row in found['results']}
        # One result per memory, shown by its best part (here the text, its words are spelled like the query).
        self.assertEqual((rows[text]['memory_has'], rows[text]['match']), ('1 text, 1 image', 'spelling'))
        self.assertNotIn(image, rows)
        self.assertTrue(rows[chunk]['label'].startswith('notlar.md: '))
        self.assertIn('memory_context', found['note'])

    async def chat(self, *messages, assistant=True):
        await sql("update orion_chats set assistant = $1 where id = 'chat'", assistant)
        ids = []
        for role, text in messages:
            ids.append(await sql("insert into orion_chat_messages (chat_id, role, content_json, plain_text) "
                                 "values ('chat', $1, $2, $3) returning id", role,
                                 json.dumps({'role': role, 'content': text}), text))
        return ids

    async def test_the_context_tool_gives_the_whole_group_and_the_chat_around_the_save(self):
        ids = await self.chat(*[('user' if k % 2 == 0 else 'assistant', f'mesaj {k}') for k in range(10)])
        text = await self.add('Kullanıcı: mesaj 6\n\nAsistan: mesaj 7', group='g1', source='chat_save',
                              chat='chat', message=ids[6])
        image, _ = await self.image('g1', chat='chat', message=ids[6])
        result = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=image, chat=True,
                                 message_count=4)
        self.assertEqual([p['id'] for p in result['parts']], [text, image])
        self.assertEqual(result['parts'][0]['text'], 'Kullanıcı: mesaj 6\n\nAsistan: mesaj 7')
        messages = result['chat']['messages']
        self.assertEqual([m['text'] for m in messages], ['mesaj 4', 'mesaj 5', 'mesaj 6', 'mesaj 7'])
        self.assertEqual([m.get('saved', False) for m in messages], [False, False, True, False])
        start = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=text, chat=True,
                                message_count=2, chat_position='start')
        self.assertEqual([m['text'] for m in start['chat']['messages']], ['mesaj 0', 'mesaj 1'])
        await sql('delete from orion_chat_messages where id = $1', ids[6])
        cleared = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=text, chat=True)
        self.assertIn('cleared', cleared['chat_state'])
        await sql("delete from orion_chats where id = 'chat'")
        gone = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=text, chat=True)
        self.assertIn('deleted', gone['chat_state'])
        self.assertIn('no memory', (await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput,
                                                    id=999999))['error'])

    async def test_an_old_chat_save_finds_its_chat_by_its_text_and_remembers_it(self):
        ids = await self.chat(('user', 'eski kayıt sorusu burada'), ('assistant', 'eski cevap'))
        # Saved after its messages, as a save always is (the helper's dates start in the past).
        old = await self.add('Kullanıcı: eski kayıt sorusu burada\n\nAsistan: eski cevap', group='g1',
                             source='chat_save', minute=60 * 24 * 366 * 2)
        result = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=old, chat=True)
        self.assertEqual([m['text'] for m in result['chat']['messages']], ['eski kayıt sorusu burada', 'eski cevap'])
        self.assertEqual(await sql('select source_message_id from orion_memory where id = $1', old), ids[0])
        note = await self.add('elle eklenen not', group='g2')
        unknown = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=note, chat=True)
        self.assertIn('not known', unknown['chat_state'])

    async def test_a_chat_save_stores_its_group_chat_and_the_message_each_chunk_starts_with(self):
        ids = await self.chat(('user', 'Benim adım Ayşe.'), ('assistant', 'Memnun oldum.'))

        async def target(settings):
            return settings, SPACE, True

        async def embed(text, settings, kind, local):
            return [vec(0.5, 2)]
        with patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed):
            await memory.save_chat('alice', 'chat', 'Tanışma', self.settings)
        row = (await sql('select group_id, source_chat_id, source_message_id from orion_memory', fetch=True))[0]
        self.assertEqual((len(row['group_id']), row['source_chat_id'], row['source_message_id']), (32, 'chat', ids[0]))

    async def save_chat_with(self, messages):
        """Saves a chat whose messages are given as (role, content_json dict); the embedding is patched."""
        await sql("update orion_chats set assistant = true where id = 'chat'")
        for role, body in messages:
            await sql("insert into orion_chat_messages (chat_id, role, content_json, plain_text) "
                      "values ('chat', $1, $2, $3) returning id", role, json.dumps({'role': role, **body}),
                      body.get('display_text') or body.get('content') or '')

        async def target(settings):
            return settings, SPACE, True

        async def embed(text, settings, kind, local):
            return [vec(0.5, 2)]
        with patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed):
            return await memory.save_chat('alice', 'chat', 'Tanışma', self.settings)

    async def test_a_chat_saved_with_a_text_file_keeps_the_file_as_its_own_part(self):
        big = 'benim adım Ahmet. ' * 400
        await self.save_chat_with([
            ('user', {'content': 'dosyaya bak', 'display_text': 'dosyaya bak',
                      'attachments': [{'name': 'benim adım Ahmet.txt', 'isText': True, 'data': big,
                                       'mime_type': 'text/plain'}]}),
            ('assistant', {'content': 'Ekleri inceledim.'}),
        ])
        rows = await sql('select text, source_kind, source_chat_title from orion_memory where not whole order by id',
                         fetch=True)
        transcript = [r for r in rows if r['source_kind'] == 'chat_save']
        files = [r for r in rows if r['source_kind'] == 'file']
        # The transcript names the file; its content is not inlined.
        self.assertTrue(all('benim adım Ahmet. benim adım Ahmet.' not in r['text'] for r in transcript))
        self.assertIn('benim adım Ahmet.txt', ' '.join(r['text'] for r in transcript))
        self.assertTrue(files and all(r['source_chat_title'] == 'benim adım Ahmet.txt' for r in files))
        self.assertIn('benim adım Ahmet.', files[0]['text'])
        group = (await sql('select group_id from orion_memory limit 1', fetch=True))[0]['group_id']
        detail = await memory_groups.group_detail('alice', group, self.settings)
        names = {p.get('file_name') for p in detail['parts'] if p['kind'] == 'text'}
        self.assertIn('benim adım Ahmet.txt', names)

    async def test_chat_sections_split_on_message_boundaries_and_keywords_find_them(self):
        messages = []
        for k in range(8):
            messages.append(('user', {'content': f'Soru {k}: ' + 'söz ' * 30}))
            messages.append(('assistant', {'content': f'Cevap {k}: ' + 'yanıt ' * 30}))
        messages.append(('user', {'content': 'Zürafa hakkında ne biliyorsun'}))
        messages.append(('assistant', {'content': 'Zürafalar uzun boyludur.'}))
        await self.save_chat_with(messages)
        cores = [r['text'] for r in await sql(
            'select text from orion_memory where not whole and source_kind = $1 order by id', 'chat_save', fetch=True)]
        self.assertGreater(len(cores), 1)
        # Every section starts at a user message (a turn is kept with its answer), none starts mid-answer.
        self.assertTrue(all(c.startswith('Kullanıcı: ') for c in cores), [c[:20] for c in cores])
        # A question and its answer are in the same section.
        for k in range(8):
            q = next(c for c in cores if f'Soru {k}:' in c)
            self.assertIn(f'Cevap {k}:', q)
        # Keyword and text matching work for a chat save like for any text.
        found = await memory_groups.search_queries('alice', self.settings, [{'keyword': 'zürafa'}])
        self.assertEqual(len(found['groups']), 1)
        page = await memory_groups.list_groups('alice', self.settings, q='zürafa')
        self.assertEqual(page['groups'][0]['match'], 'text')

    async def test_deleting_a_whole_part_takes_every_section_and_its_whole_vector(self):
        text = '\n\n'.join(f'Bölüm {k}: ' + 'kelime ' * 120 for k in range(20))
        conn = await registry._connect()
        try:
            for k in range(14):
                await conn.execute(
                    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, kind, group_id, "
                    "item_id) values ('alice', $1, $2, $3::vector, 'file', 'text', 'g', 'long')",
                    f'parça {k}', SPACE, memory._vector(vec(0.3, 2 + k)))
            await conn.execute(
                "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, kind, group_id, "
                "item_id, whole) values ('alice', 'bütün', $1, $2::vector, 'file', 'text', 'g', 'long', true)",
                SPACE, memory._vector(vec(0.3, 30)))
            await conn.execute(
                "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, kind, group_id, "
                "item_id) values ('alice', 'öteki parça', $1, $2::vector, 'manual', 'text', 'g', 'other')",
                SPACE, memory._vector(vec(0.3, 40)))
        finally:
            await conn.close()
        # The panel knows only a few rows of the long item; the delete goes by item.
        from orion.api import routes
        result = await routes.delete_memory_items(routes.MemoryDelete(item_ids=['long']), 'alice')
        self.assertEqual(len(result['deleted']), 14)
        left = await sql('select text from orion_memory', fetch=True)
        self.assertEqual([r['text'] for r in left], ['öteki parça'])
        self.assertEqual(await routes.delete_memory_items(routes.MemoryDelete(item_ids=['long']), 'bob'),
                         {'deleted': []})

    # --- new tools come on ----------------------------------------------------------------------------------------

    def test_new_memory_tools_come_on_with_their_group_but_an_unchecked_one_stays_off(self):
        tools = get_registry()
        # Saved under the tools' old Turkish ids: the choices carry over to the new ids.
        saved = ToolSelection(categories={'memory': True}, functions={'hafizada_ara': True, 'hafizadan_sil': False})
        self.assertEqual(saved.functions, {'search_memory': True, 'delete_memory': False})
        enabled = tools.enabled(saved)
        self.assertIn('open_memory_files', enabled)
        self.assertIn('memory_context', enabled)
        self.assertNotIn('delete_memory', enabled)
        complete = tools.complete(saved)
        self.assertEqual((complete.functions['memory_context'], complete.functions['delete_memory']), (True, False))
        self.assertEqual(tools.enabled(ToolSelection(categories={'memory': False}, functions={})), [])
        self.assertEqual(tools.enabled(ToolSelection(categories={'browser': True}, functions={})), [])  # Not default-on.
        masked = routes._mask_settings_for_client(SimpleNamespace(
            tool_selection=saved, router_api_key='', model_copy=lambda update: update))
        self.assertTrue(masked['tool_selection'].functions['open_memory_files'])

    # --- map ----------------------------------------------------------------------------------------------------

    async def test_the_map_links_pairs_above_the_spaces_own_floor_and_shows_another_model(self):
        await self.add('kedi', score=0.9, axis=1, group='a')
        await self.add('kedi maması', score=0.9, axis=2, group='b')
        await self.add('uzay', score=0.0, axis=3, group='c', minute=1)
        image, _ = await self.image('d', score=0.0, axis=4, minute=2)
        await self.add('eski model', space=OTHER, group='e')
        graph = await memory_groups.map_graph('alice', self.settings, k=3)
        self.assertEqual({n['group_id'] for n in graph['nodes']}, {'a', 'b', 'c', 'd'})
        # Six pairs: a-b 0.81, the rest 0: the floor is their mean, so only a-b is linked, at full strength.
        self.assertEqual((graph['spread']['pairs'], graph['floor']), (6, 0.135))
        self.assertEqual([(e['a'], e['b'], e['w']) for e in graph['edges']], [('a', 'b', 1.0)])
        self.assertAlmostEqual(graph['edges'][0]['sim'], 0.81, places=3)
        self.assertTrue(graph['active'])
        self.assertEqual(next(n for n in graph['nodes'] if n['group_id'] == 'd')['thumb'], image)
        capped = await memory_groups.map_graph('alice', self.settings, limit=2)
        self.assertEqual((len(capped['nodes']), capped['total'], capped['capped']), (2, 4, True))
        other = await memory_groups.map_graph('alice', self.settings, vector_space=OTHER)
        self.assertEqual(([n['group_id'] for n in other['nodes']], other['active'], other['floor'], other['edges']),
                         (['e'], False, memory_groups.MAP_MIN_SIM, []))
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=self.settings)):
            via_route = await routes.memory_map(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
                                                k=3, limit=300, vector_space='', current_user='alice')
        self.assertEqual(len(via_route['nodes']), 4)

    def test_the_map_floor_is_the_typical_pair_never_below_the_minimum(self):
        self.assertEqual(memory_groups.map_floor(0.24, 55), 0.24)
        self.assertEqual(memory_groups.map_floor(-0.1, 55), memory_groups.MAP_MIN_SIM)
        self.assertEqual(memory_groups.map_floor(0.9, 1), memory_groups.MAP_MIN_SIM)  # Too few pairs to tell.
        self.assertEqual(memory_groups.map_floor(None, 0), memory_groups.MAP_MIN_SIM)

    # --- one memory, vectors of one or more models --------------------------------------------------------------

    async def test_the_detail_lists_the_models_a_memory_is_searchable_with(self):
        old = await self.add('Kullanıcı: kalp', space=OTHER, group='g1', source='chat_save')
        await self.add('Kullanıcı: kalp', group='g1', source='chat_save', copied_from=old)
        await self.add('sonra eklenen', group='g1')
        detail = await memory_groups.group_detail('alice', 'g1', self.settings)
        self.assertEqual(detail['part_count'], 2)
        await self.add('yalnız eski modelde', space=OTHER, group='g2', minute=1)
        active = await memory_groups.list_groups('alice', self.settings, vector_space='active')
        self.assertEqual([c['group_id'] for c in active['groups']], ['g1'])
        self.assertEqual(len((await memory_groups.list_groups('alice', self.settings))['groups']), 2)
        self.assertEqual(detail['models'], [{'space': SPACE, 'active': True, 'parts': 2},
                                            {'space': OTHER, 'active': False, 'parts': 1}])

    async def test_removing_another_models_vectors_says_which_memories_lose_it_and_keeps_the_only_ones(self):
        moved = await self.add('taşınmış', space=OTHER, group='g1')
        await self.add('taşınmış', group='g1', copied_from=moved)
        await self.add('yalnız eski modelde', space=OTHER, group='g2', minute=1)
        preview = await memory_groups.space_vectors('alice', OTHER, self.settings)
        self.assertEqual((preview['memories'], preview['vectors'], preview['kept']), (1, 1, 1))
        self.assertEqual([(i['group_id'], i['label']) for i in preview['items']], [('g1', 'taşınmış')])
        with self.assertRaisesRegex(ValueError, 'kaldırılamaz'):
            await memory_groups.space_vectors('alice', SPACE, self.settings)
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=self.settings)):
            removed = await routes.memory_move_cleanup(
                routes.MemoryMove(vector_space=OTHER),
                SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))), current_user='alice')
        self.assertEqual(removed, {'deleted': 1})
        left = await sql('select text, vector_space from orion_memory order by id', fetch=True)
        self.assertEqual([(r['text'], r['vector_space']) for r in left],
                         [('taşınmış', SPACE), ('yalnız eski modelde', OTHER)])

    # --- adding parts to a memory ---------------------------------------------------------------------------------

    def add_patches(self):
        async def target(settings):
            return settings, SPACE, True

        async def embed(text, settings, kind, local):
            return [vec(0.5, 2)]

        async def embed_media(parts, target):
            return vec(0.3, 3)
        for guard in (patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed),
                      patch.object(memory, '_embed_media', embed_media),
                      patch.object(memory, 'media_support', lambda local, name: JINA)):
            guard.start()
            self.addCleanup(guard.stop)

    async def test_a_staged_note_and_files_become_one_memory_and_ekle_adds_to_an_existing_one(self):
        self.add_patches()
        note = await memory.add_note_to('alice', 'bugünkü notum', self.settings)
        group = note['group_id']
        image = await memory.add_file('alice', 'kalp.png', 'image/png', png(), self.settings, group=group)
        text = await memory.add_file('alice', 'ek.md', 'text/markdown', 'ek dosya metni'.encode(), self.settings,
                                     group=group)
        self.assertEqual((note['added'], image['group_id'], text['group_id']), (1, group, group))
        detail = await memory_groups.group_detail('alice', group, self.settings)
        self.assertEqual((detail['part_count'], detail['counts']), (3, {'text': 2, 'image': 1}))
        loose = await self.add('gruplanmadan önce', minute=5)
        added = await memory.add_note_to('alice', 'sonradan eklenen', self.settings, group=f'r{loose}')
        self.assertEqual(await sql('select group_id from orion_memory where id = $1', loose), added['group_id'])
        with self.assertRaises(LookupError):
            await memory.add_note_to('alice', 'başkasının', self.settings, group='yok')
        await self.add('bob notu', user='bob', group='bobs')
        with self.assertRaises(LookupError):
            await memory.add_file('alice', 'x.md', 'text/markdown', b'x', self.settings, group='bobs')
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        with patch.object(routes, 'get_runtime_settings', AsyncMock(return_value=self.settings)):
            via_route = await routes.add_memory_note(routes.MemoryNote(text='rotadan', group_id=group), request,
                                                     current_user='alice')
            self.assertEqual(via_route, {'added': 1, 'group_id': group})
            with self.assertRaises(routes.HTTPException) as refused:
                await routes.add_memory_note(routes.MemoryNote(text='x', group_id='bobs'), request,
                                             current_user='alice')
        self.assertEqual(refused.exception.status_code, 404)

    async def test_tools_give_the_local_date_and_time(self):
        row = await self.add('adım Ayşe', score=0.7, group='g1')
        result = await self.tool(memory_tools.memory_context, memory_tools.MemoryContextInput, id=row)
        stamp = await sql('select created_at from orion_memory where id = $1', row)
        self.assertEqual(result['date'], memory.local_time(stamp))
        self.assertRegex(result['date'], r'^2026-1[01]-\d\d \d\d:\d\d \(UTC[+-]\d\d:\d\d\)$')

    # --- move ---------------------------------------------------------------------------------------------------

    def move_patches(self, support=JINA, embed=None):
        async def target(settings):
            return SimpleNamespace(router_model_group='local-embed'), SPACE, True

        async def text_vector(text, settings, kind, local):
            return [vec(0.4, 20)]

        async def media_vector(parts, target):
            return vec(0.3, 21)
        return [patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed or text_vector),
                patch.object(memory, '_embed_media', media_vector),
                patch.object(memory, 'media_support', lambda local, name: support)]

    async def run_job(self, patches, start=True):
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)
        job = await memory_move.start('alice', OTHER, self.settings)
        self.assertEqual(job['state'], 'running')
        await asyncio.gather(*list(memory_move._tasks))
        return await memory_move.status('alice')

    async def test_moving_copies_text_and_images_keeps_groups_and_skips_what_the_model_cannot_take(self):
        text = await self.add('Kullanıcı: kalp', space=OTHER, group='g1', source='chat_save', chat='chat', message=5)
        image, media_id = await self.image('g1', space=OTHER)
        voice = await self.add('Ses kaydı 0:05', kind='audio', media_id='b' * 64, space=OTHER, group='g1')
        done = await self.add('zaten taşınmış', space=OTHER, group='g2')
        await self.add('zaten taşınmış', group='g2', copied_from=done)
        status = await self.run_job(self.move_patches())
        self.assertEqual((status['state'], status['total'], status['done'], status['copied'], status['existing']),
                         ('done', 3, 3, 2, 1))
        self.assertEqual(status['unsupported'], {'audio': 1})
        copies = await sql('select copied_from, group_id, kind, source_chat_id, source_message_id, created_at, media_id '
                           'from orion_memory where vector_space = $1 and copied_from = any($2::bigint[]) order by id',
                           SPACE, [text, image, voice], fetch=True)
        self.assertEqual([(c['copied_from'], c['group_id'], c['kind']) for c in copies],
                         [(text, 'g1', 'text'), (image, 'g1', 'image')])
        self.assertEqual((copies[0]['source_chat_id'], copies[0]['source_message_id']), ('chat', 5))
        self.assertEqual(copies[0]['created_at'], await sql('select created_at from orion_memory where id = $1', text))
        self.assertEqual(await sql('select count(*) from orion_media_links where media_id = $1', media_id), 2)
        page = await memory_groups.list_groups('alice', self.settings, limit=10)
        self.assertEqual(page['matching'], 2)
        self.assertEqual(page['summary']['movable'], {OTHER: 1})  # The voice note stays behind.
        again = await self.run_job([])
        self.assertEqual((again['copied'], again['existing'], again['total']), (0, 3, 1))
        self.assertEqual(await memory_groups.delete_copied('alice', OTHER, self.settings), 3)
        self.assertEqual(await sql('select count(*) from orion_memory where vector_space = $1', OTHER), 1)
        self.assertEqual(await sql('select count(*) from orion_media_links where media_id = $1', media_id), 1)

    async def test_a_move_can_be_cancelled_and_a_second_start_waits(self):
        for k in range(5):
            await self.add(f'not {k}', space=OTHER, group=f'g{k}')
        release = asyncio.Event()
        calls = []

        async def slow(text, settings, kind, local):
            calls.append(text)
            await release.wait()
            return [vec(0.4, 20)]
        for guard in self.move_patches(embed=slow):
            guard.start()
            self.addCleanup(guard.stop)
        await memory_move.start('alice', OTHER, self.settings)
        while not calls:
            await asyncio.sleep(0.01)
        with self.assertRaisesRegex(ValueError, 'zaten sürüyor'):
            await memory_move.start('alice', OTHER, self.settings)
        self.assertTrue((await memory_move.cancel('alice'))['cancel'])
        release.set()
        await asyncio.gather(*list(memory_move._tasks))
        status = await memory_move.status('alice')
        self.assertEqual((status['state'], status['done'], status['copied']), ('cancelled', 1, 1))
        await sql("update orion_memory_jobs set state = 'running', updated_at = now() - interval '5 minutes'")
        self.assertEqual((await memory_move.status('alice'))['state'], 'interrupted')
        with self.assertRaisesRegex(ValueError, 'zaten etkin'):
            await memory_move.start('alice', SPACE, self.settings)


    async def test_a_hub_stop_leaves_the_move_interrupted_not_done(self):
        await self.add('not', space=OTHER, group='g1')
        started = asyncio.Event()

        async def hang(text, settings, kind, local):
            started.set()
            await asyncio.Event().wait()
        for guard in self.move_patches(embed=hang):
            guard.start()
            self.addCleanup(guard.stop)
        await memory_move.start('alice', OTHER, self.settings)
        await started.wait()
        task = next(iter(memory_move._tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((await memory_move.status('alice'))['state'], 'interrupted')

def test_plan_groups_chains_rows_within_the_gap():
    t = dt.datetime(2026, 11, 1)
    rows = [{'id': k, 'user_id': 'u', 'vector_space': 's', 'source_kind': 'chat_save', 'title': 'x', 'kind': 'text',
             'media_id': None, 'created_at': t + dt.timedelta(seconds=s)} for k, s in enumerate((0, 100, 250, 1000))]
    plan = memory_groups.plan_groups(rows)
    assert plan[0] == plan[1] == plan[2] != plan[3]  # 100 s and 150 s gaps chain; 750 s does not.


def test_a_file_saved_alone_later_joins_the_chat_text_that_names_it():
    t = dt.datetime(2026, 11, 1)

    def row(k, kind, text, minute):
        return {'id': k, 'user_id': 'u', 'vector_space': 's', 'source_kind': 'chat_save', 'title': 'kalp',
                'kind': kind, 'media_id': 'm' if kind != 'text' else None, 'text': text,
                'created_at': t + dt.timedelta(minutes=minute)}
    plan = memory_groups.plan_groups([row(1, 'text', 'Kullanıcı: bu ne\nHome _ X.webp\n\nAsistan: bir site', 0),
                                      row(2, 'image', 'Home _ X.webp — bu ne', 9),
                                      row(3, 'image', 'baska.png — x', 20)])
    assert plan[1] == plan[2] != plan[3]


def test_local_time_shows_the_pcs_clock_and_offset():
    stamp = dt.datetime(2026, 11, 15, 11, 51, tzinfo=dt.timezone.utc)
    local = stamp.astimezone()
    offset = local.strftime('%z')
    assert memory.local_time(stamp) == f"{local:%Y-%m-%d %H:%M} (UTC{offset[:3]}:{offset[3:]})"
    assert memory.local_time(None) is None


def test_an_unrelated_input_never_demotes_a_strong_single_input_match():
    from orion.api import routes
    from orion.kernel import memory_groups as mg
    row = lambda i, g, s: {'id': i, 'group_id': g, 'score': s}
    names = [f'm{k}' for k in range(30)]
    # An unrelated picture: everything a little alike, Pamuk on top by chance.
    image = [row(100 + k, g, 0.48 if g == 'pamuk' else 0.40 + 0.002 * (k % 5)) for k, g in
             enumerate(['pamuk'] + names + ['elif'])]
    # "kahve": Elif's coffee break stands far out, the rest low.
    text = [row(200 + k, g, 0.52 if g == 'elif' else 0.12 + 0.003 * (k % 7)) for k, g in
            enumerate(['elif', 'pamuk'] + names)]
    labels = routes.input_labels(['image', 'query'], 2)
    assert labels == ['görsel', 'metin']
    fused = mg.fuse_inputs([image, text], labels)
    assert fused[0]['group_id'] == 'elif' and fused[0]['via'] == 'metin' and fused[0]['score'] == 0.52
    ranked = mg.merge_matches(fused, [], keep_order=True)
    assert ranked[0]['strength'] == 'strong' and ranked[0]['via'] == 'metin'
    # The noise of each input stays out of the strong ones (Pamuk does stand out for the picture: it may show).
    assert not [e for e in ranked if e['group'].startswith('m') and e['strength'] != 'weak']
    assert routes.input_labels(['image', 'image', 'query'], 3) == ['görsel 1', 'görsel 2', 'metin']
    # Named when the name is known: a text in quotes, a file by its name ("Ayrı" cards say which input it was).
    assert routes.input_labels(['image', 'query'], 2, ['kus.png', '"kuş"']) == ['kus.png', '"kuş"']
    assert routes.input_name(routes.MemorySearchInput(text='  kuş  ')) == '"kuş"'
    assert routes.input_name(routes.MemorySearchInput(name='kedi.jpg', data='x')) == 'kedi.jpg'


def test_a_long_pasted_query_is_cut_not_refused_and_matches_by_meaning_only():
    from orion.api import routes
    from orion.kernel import memory_groups as mg
    long = 'çadır ' * 2000
    body = routes.MemorySearch(inputs=[{'text': long}], q=long)
    assert len(body.q) == len(long)
    parts = [{'id': 1, 'kind': 'text', 'text': long, 'whole': False}]
    found = mg.score_parts(parts, {1: 0.4}, long)
    assert found['parts'][1]['match'] == 'meaning'



def test_a_long_query_is_embedded_as_the_mean_of_its_windows_sampled_evenly(monkeypatch):
    import asyncio
    from orion.kernel import memory
    seen = []

    async def target(settings):
        return None, 'space', True

    async def embed(text, target, kind, local):
        seen.append((text, kind))
        return [[1.0] + [0.0] * 767]
    monkeypatch.setattr(memory, 'embedding_target', target)
    monkeypatch.setattr(memory, '_embed', embed)
    memory._query_cache.clear()
    passage = '\n\n'.join(f'Paragraf {k}: ' + ' '.join([f'kelime{k}'] * 300) for k in range(200))
    windows, total = memory.query_windows(passage)
    assert total > memory.QUERY_WINDOWS and len(windows) == memory.QUERY_WINDOWS
    assert 'Paragraf 0' in windows[0] and any('Paragraf 19' in w for w in windows[-3:])  # Across the whole text.
    space, vector, reason = asyncio.run(memory._query_vector(passage, None))
    assert (space, reason, len(seen)) == ('space', None, memory.QUERY_WINDOWS)
    assert {kind for _, kind in seen} == {'query'}
    # Asked again (the detail's part scores): from the cache, nothing embedded.
    assert asyncio.run(memory._query_vector(passage, None))[1] == vector and len(seen) == memory.QUERY_WINDOWS
