"""One order, one %: the page's list, the detail panel and the chat tool rank a memory by the same score (memory_groups
.rank_matches / group_match); the text and window helpers they share. Real throwaway PostgreSQL (see
test_memory_search.PostgresMemoryCase)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.api import routes
from orion.kernel import memory, memory_groups, memory_move, memory_pieces, registry
from orion.kernel.memory_pieces import mean_vector

from test_memory_search import SPACE, PostgresMemoryCase, vec

REAL_QUERY_VECTOR = memory._query_vector  # The case patches it with hand-made vectors.


class RankTests(PostgresMemoryCase):
    async def tool(self, queries, mode='together'):
        from orion.worker.tools._base import ToolContext
        from orion.worker.tools.memory import tools as memory_tools
        queries = [memory_tools.MemoryQuery(**q) for q in queries]
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            return await memory_tools.search_memory(memory_tools.MemorySearchInput(queries=queries, mode=mode),
                                                    ToolContext('alice', 'chat', 't', None))

    async def seed(self):
        """Ten memories with distinct scores (a pool big enough for a strength), one with a word in it."""
        ids = {}
        for k in range(10):
            ids[k] = await self.add(f'not {k}' + (' maviş' if k == 7 else ''), score=0.9 - k * 0.08, axis=2 + k,
                                    group=f'g{k}', minute=k)
        return ids

    def vectors_for(self, queries):
        """Each query text is a vector: 'a' is the base axis, 'b' leans to another axis."""
        other = [0.0] * 768
        other[4] = 1.0
        return {'a': self.query, 'b': other}

    async def patch_vectors(self, table):
        async def query_vector(q, settings):
            return SPACE, memory._vector(table[q.strip()]), None
        guard = patch.object(memory, '_query_vector', query_vector)
        guard.start()
        self.addCleanup(guard.stop)

    async def test_the_order_is_the_shown_percent_and_a_word_match_only_marks(self):
        ids = await self.seed()
        for k in range(12):  # A pool of ordinary memories the best ones stand out of.
            await self.add(f'sıradan {k}', score=0.05, axis=20 + k, group=f's{k}', minute=20 + k)
        page = await memory_groups.list_groups('alice', self.settings, q='maviş', limit=30)
        scores = [g['score'] for g in page['groups']]
        self.assertEqual(scores, sorted(scores, reverse=True))  # No bonus: the word match does not lift its card.
        marked = next(g for g in page['groups'] if g['matched_part'] == ids[7])
        self.assertEqual(marked['match'], 'text')
        self.assertEqual([g['match'] for g in page['groups']].count('text'), 1)
        self.assertEqual(page['groups'][0]['matched_part'], ids[0])
        # The strength is measured against the query's own pool, never an absolute number.
        self.assertIn(page['groups'][0]['strength'], ('strong', 'medium'))
        self.assertEqual(page['groups'][-1]['strength'], 'weak')

    async def test_a_memory_found_only_by_its_words_is_ranked_by_its_real_score(self):
        low = await self.add('maviş burada', score=0.2, axis=2, group='a')
        high = await self.add('başka şey', score=0.5, axis=3, group='b')
        await self.add('maviş bir parça', score=0.1, axis=4, group='a')  # The group's best row is the 0.2 one.
        page = await memory_groups.list_groups('alice', self.settings, q='maviş', limit=10)
        self.assertEqual([(g['group_id'], g['score']) for g in page['groups']], [('b', 0.5), ('a', 0.2)])
        self.assertEqual((page['groups'][1]['match'], page['groups'][1]['matched_part']), ('text', low))
        self.assertEqual(page['groups'][0]['matched_part'], high)

    async def test_list_panel_and_tool_agree_on_order_and_percent_for_every_kind_of_query(self):
        ids = await self.seed()
        long_text = 'martılar ' * 120
        other = self.vectors_for(None)['b']
        await self.patch_vectors({'a': self.query, 'b': other, long_text.strip(): other})
        cases = [
            ('single text', [{'text': 'a'}], 'together', [self.query], 'a'),
            ('together', [{'text': 'a'}, {'text': 'b'}], 'together', [self.query, other], 'a b'),
            ('separate', [{'text': 'a'}, {'text': 'b'}], 'each', [self.query, other], 'a b'),
            ('long pasted text', [{'text': long_text.strip()}], 'together', [other], long_text.strip()),
        ]
        for name, queries, mode, vecs, words in cases:
            with self.subTest(name):
                found = await memory_groups.search_queries('alice', self.settings, queries, mode=mode, limit=20)
                cards = found['groups']
                shown = [c['score'] for c in cards]
                self.assertEqual(shown, sorted(shown, reverse=True))
                tool = await self.tool(queries, 'separate' if mode == 'each' else 'together')
                self.assertEqual([r['id'] for r in tool['results']],
                                 [c['matched_part'] for c in cards[:len(tool['results'])]])
                self.assertEqual([r['score'] for r in tool['results']],
                                 [round(c['score'] * 100) for c in cards[:len(tool['results'])]])
                for card in cards[:4]:
                    index = card.get('query_index', 0)
                    vector = vecs[index] if mode == 'each' and len(vecs) > 1 else mean_vector(vecs)
                    panel = await memory_groups.part_scores('alice', card['group_id'], self.settings, q=words,
                                                            vector=memory._vector(vector), space=SPACE)
                    self.assertEqual((panel['score'], panel['best'], panel['match'], panel['strength']),
                                     (card['score'], card['matched_part'], card['match'], card['strength']))
        # An id query: the vector of a stored row, its own memory left out.
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            found = await memory_groups.search_queries('alice', self.settings, [{'id': ids[3]}], limit=20)
            tool = await self.tool([{'id': ids[3]}])
        self.assertNotIn(ids[3], [c['matched_part'] for c in found['groups']])
        self.assertEqual([r['id'] for r in tool['results']], [c['matched_part'] for c in found['groups']][:10])
        self.assertEqual([r['score'] for r in tool['results']],
                         [round(c['score'] * 100) for c in found['groups']][:10])

    async def test_the_stored_copy_of_a_file_is_first_even_when_the_index_returns_rows_out_of_order(self):
        """pgvector's iterative scan may return rows a little out of order: a memory's score must be its highest row,
        not the first one seen."""
        exact = await self.add('met.txt parçası', score=1.0, axis=2, group='file')
        await self.add('met.txt ikinci parça', score=0.19, axis=3, group='file', minute=1)
        await self.add('başka bir not', score=0.19, axis=4, group='other', minute=2)
        real = memory.ann_fetch

        async def shuffled(conn, sql, *args):
            return list(reversed(await real(conn, sql, *args)))
        with patch.object(memory, 'ann_fetch', shuffled):
            page = await memory_groups.list_groups('alice', self.settings, vector=memory._vector(self.query),
                                                   vector_space_of_query=SPACE, limit=10)
        self.assertEqual([(g['group_id'], g['score']) for g in page['groups']], [('file', 1.0), ('other', 0.19)])
        self.assertEqual(page['groups'][0]['matched_part'], exact)

    async def test_a_text_file_and_the_same_text_pasted_make_one_vector(self):
        calls = []

        async def embed(text, settings, kind, local):
            calls.append((text, kind))
            seed = sum(map(ord, text)) % 97 + 1
            return [[float((i * seed) % 13 + 1) for i in range(768)]]
        target = (SimpleNamespace(router_model_group='e'), SPACE, True)
        memory._query_cache.clear()
        with patch.object(memory, '_embed', embed), patch.object(memory, '_query_vector', REAL_QUERY_VECTOR), \
                patch.object(memory, 'embedding_target', AsyncMock(return_value=target)):
            for text in ('kısa bir soru', 'Uzun bir metin. ' * 60):
                with self.subTest(len(text)):
                    memory._query_cache.clear()
                    pasted = (await memory._query_vector(text, self.settings))[1]
                    memory._query_cache.clear()
                    dropped = (await memory.file_query_vector('a.txt', 'text/plain', text.encode(), self.settings))[1]
                    self.assertEqual(pasted, dropped)
        self.assertEqual({kind for text, kind in calls if len(text) > memory.TEXT_MATCH_CHARS}, {'document'})

    async def test_the_words_are_folded_once_in_python_for_any_script(self):
        samples = ['İSTANBUL ÇIĞ ÖĞRENCİ ÜNLÜ', 'ışık Işık şeker', 'Straße ÄÖÜ äöü', 'ДОМ Привет ЁЖ', 'ΣΟΦΙΑ Ελλάδα']
        conn = await registry._connect()
        try:
            async with conn.transaction():
                for k, text in enumerate(samples):
                    await memory.insert_item(conn, 'alice', SPACE, f'g{k}', {'kind': 'text', 'source_kind': 'manual'},
                                             [{'text': text, 'vector': vec(0.5, 2 + k)}])
            stored = {r['text']: r['folded'] for r in await conn.fetch('select text, folded from orion_memory')}
            self.assertEqual(stored, {t: memory.fold(t) for t in samples})
            # An old row (no folded yet) is folded by the one-time backfill, with the same function.
            await conn.execute("update orion_memory set folded = null")
            await conn.execute("delete from orion_memory_meta where key = 'folded_v2'")
            await memory._fold_columns(conn)
            stored = {r['text']: r['folded'] for r in await conn.fetch('select text, folded from orion_memory')}
        finally:
            await conn.close()
        self.assertEqual(stored, {t: memory.fold(t) for t in samples})
        self.assertEqual(memory.fold('ДОМ'), 'дом')
        found = await memory_groups.list_groups('alice', self.settings, q='дом', limit=10)
        self.assertEqual([g['match'] for g in found['groups'] if g['match'] == 'text'], ['text'])

    async def test_one_fuzzy_threshold_for_sql_and_python(self):
        self.assertFalse(hasattr(memory, 'FUZZY_MIN_PY'))
        self.assertFalse(hasattr(memory, 'TEXT_BONUS'))
        hit, score = memory_groups.text_hit('selam nabe', 'Kullanıcı: selam naber nasılsın')
        self.assertEqual(hit, 'text')

    async def test_embedding_errors_tell_an_unreachable_router_from_a_missing_component(self):
        async def down(*args, **kwargs):
            raise RuntimeError('Model listelerine şu an erişilemiyor.')
        with patch('orion.kernel.router_models.get_router_catalog', down):
            with self.assertRaises(memory.MemoryUnavailable) as caught:
                await memory.embedding_target(self.settings)
        self.assertEqual(str(caught.exception), memory.ROUTER_DOWN_MESSAGE)
        with patch('orion.kernel.router_models.get_router_catalog', AsyncMock(return_value={'models': []})):
            with self.assertRaises(memory.MemoryUnavailable) as caught:
                await memory.embedding_target(self.settings)
        self.assertEqual(str(caught.exception), memory.SETUP_MESSAGE)

        async def refused(*args, **kwargs):
            raise ConnectionRefusedError('refused')
        with patch('orion.worker.services.router.embed_text', refused):
            with self.assertRaises(memory.MemoryUnavailable) as caught:
                await memory._embed('x', self.settings, 'query', True)
        self.assertEqual(str(caught.exception), memory.ROUTER_DOWN_MESSAGE)

    async def test_deleting_a_piece_embeds_its_neighbours_again_from_the_text_that_is_left(self):
        rows = []
        for k, text in enumerate(('birinci parça', 'ikinci parça', 'üçüncü parça')):
            rows.append(await self.add(text, score=0.5, axis=2 + k, group='g', minute=k))
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set item_id = 'it', item_order = id where group_id = 'g'")
        finally:
            await conn.close()
        windows = []

        async def embed(text, settings, kind, local):
            windows.append(text)
            return [[1.0] + [0.0] * 767]
        target = (SimpleNamespace(router_model_group='e'), SPACE, True)
        with patch.object(memory, '_embed', embed), patch.object(memory, 'embedding_targets', AsyncMock(return_value=[target])):
            await memory.delete_items('alice', [rows[1]], self.settings)
            await asyncio.gather(*memory._reembed_tasks)
        self.assertEqual(len(windows), 2)  # The piece before and the piece after.
        self.assertTrue(all('ikinci' not in w for w in windows))
        self.assertEqual(windows, memory_pieces.windows_of(['birinci parça', 'üçüncü parça']))

    async def test_the_windows_of_a_move_are_the_windows_the_pieces_were_cut_with(self):
        text = '\n\n'.join(f'Paragraf {k}. ' + 'söz ' * 160 for k in range(12))
        split = memory_pieces.text_pieces(text)
        self.assertGreater(len(split), 2)
        rows = [{'id': i, 'kind': 'text', 'item_id': 'it', 'whole': False, 'text': core, 'item_order': 1,
                 'copied_from': None} for i, (core, _) in enumerate(split, 1)]
        moved = memory_move.text_windows(rows)
        self.assertEqual([moved[i] for i in range(1, len(split) + 1)], [window for _, window in split])

    async def test_a_chat_piece_starts_at_its_own_message_even_when_messages_repeat(self):
        messages = ['Kullanıcı: tamam', 'Asistan: ' + 'cevap ' * 400, 'Kullanıcı: tamam', 'Asistan: ' + 'başka ' * 400,
                    'Kullanıcı: tamam', 'Asistan: kısa']
        pieces = memory_pieces.chat_pieces(messages)
        firsts = [first for _, _, first, _ in pieces]
        self.assertEqual(firsts, sorted(firsts))
        self.assertEqual(firsts[0], 0)
        for core, _, first, last in pieces:
            self.assertGreaterEqual(last, first)  # Exact: the core starts inside the message it points at.
            self.assertIn(core.strip()[:30], messages[first], (first, core[:40]))
        self.assertIn(3, firsts)  # Not confused with the earlier message that repeats the same words.

    async def test_the_last_link_takes_the_media_file_with_it(self):
        from orion.kernel import media_store
        raw = b'\x89PNG-test-bytes'
        media_id = __import__('hashlib').sha256(raw).hexdigest()
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await media_store.hold_media(conn, media_id, 'image/png', raw)
        finally:
            await conn.close()
        await media_store.link_media(media_id, 'memory', '1')
        await media_store.link_media(media_id, 'memory', '2')
        await media_store.unlink_media(media_id, 'memory', '1')
        conn = await registry._connect()
        try:
            self.assertEqual(await conn.fetchval('select count(*) from orion_media where id = $1', media_id), 1)
            await media_store.unlink_media(media_id, 'memory', '2', conn=conn)
            self.assertEqual(await conn.fetchval('select count(*) from orion_media where id = $1', media_id), 0)
        finally:
            await conn.close()

    async def test_the_group_backfill_runs_once_and_is_skipped_after(self):
        conn = await registry._connect()
        try:
            self.assertTrue(await conn.fetchval("select 1 from orion_memory_meta where key = 'groups_backfilled'"))
        finally:
            await conn.close()

    async def test_chat_pieces_keep_their_end_message_and_old_rows_are_backfilled(self):
        messages = ['Kullanıcı: tamam', 'Asistan: ' + 'cevap ' * 400, 'Kullanıcı: tamam', 'Asistan: ' + 'başka ' * 400]
        pieces = memory_pieces.chat_pieces(messages)
        self.assertEqual(pieces[-1][3], 3)
        self.assertTrue(all(a[3] <= b[3] and a[2] <= b[2] for a, b in zip(pieces, pieces[1:])))
        conn = await registry._connect()
        try:
            ids = [await conn.fetchval(
                "insert into orion_chat_messages (chat_id, role, content_json, created_at) values ('chat', 'user', '{}', "
                "timestamptz '2026-10-31' + $1 * interval '1 minute') returning id", k) for k in range(6)]
            first = await self.add('parça bir', group='g', source='chat_save', chat='chat', message=ids[1])
            second = await self.add('parça iki', group='g', source='chat_save', chat='chat', message=ids[4], minute=1)
            await conn.execute("update orion_memory set item_id = 'it', item_order = id where group_id = 'g'")
            await conn.execute("delete from orion_memory_meta where key = 'message_last_backfilled'")
            self.assertEqual(await memory.backfill_message_last(conn), 2)
            self.assertEqual(await memory.backfill_message_last(conn), 0)  # Once.
            lasts = {r['id']: r['source_message_last'] for r in
                     await conn.fetch('select id, source_message_last from orion_memory')}
        finally:
            await conn.close()
        self.assertEqual(lasts[first], ids[3])   # Up to the message before the next piece starts.
        self.assertEqual(lasts[second], ids[5])  # The last piece: to the chat's last message at save time.

    async def test_a_deleted_pieces_words_leave_the_neighbours_in_every_model(self):
        other = 'jina-v5'
        originals, copies = [], []
        for k, text in enumerate(('birinci parça', 'ikinci parça', 'üçüncü parça')):
            originals.append(await self.add(text, score=0.9, axis=2 + k, group='g', minute=k))
            copies.append(await self.add(text, score=0.9, axis=2 + k, group='g', minute=k, space=other,
                                         copied_from=originals[-1]))
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set item_id = 'it', item_order = coalesce(copied_from, id) "
                               "where group_id = 'g'")
        finally:
            await conn.close()

        async def embed(text, settings, kind, local):  # A window that holds 'ikinci' is near the query.
            return [[1.0] + [0.0] * 767 if 'ikinci' in text else [0.0, 0.0, 0.0, 0.0, 1.0] + [0.0] * 763]
        targets = [(SimpleNamespace(router_model_group='a'), SPACE, True),
                   (SimpleNamespace(router_model_group='b'), other, False)]
        with patch.object(memory, '_embed', embed), patch.object(memory, 'embedding_targets', AsyncMock(return_value=targets)):
            await memory.delete_items('alice', [originals[1]], self.settings)
            await asyncio.gather(*memory._reembed_tasks)
        conn = await registry._connect()
        try:
            rows = await conn.fetch("select vector_space, 1 - (embedding <=> $1::vector) as s from orion_memory",
                                    memory._vector(self.query))
        finally:
            await conn.close()
        self.assertEqual(len(rows), 4)  # Both neighbours, in both models.
        self.assertTrue(all(abs(r['s']) < 1e-6 for r in rows), [dict(r) for r in rows])
        self.assertEqual({r['vector_space'] for r in rows}, {SPACE, other})

    async def test_a_model_that_cannot_be_reached_drops_its_stale_neighbour_copy(self):
        other = 'jina-v5'
        a = await self.add('birinci', score=0.9, axis=2, group='g', minute=0)
        await self.add('birinci', score=0.9, axis=2, group='g', minute=0, space=other, copied_from=a)
        b = await self.add('ikinci', score=0.9, axis=3, group='g', minute=1)
        await self.add('ikinci', score=0.9, axis=3, group='g', minute=1, space=other, copied_from=b)
        c = await self.add('üçüncü', score=0.9, axis=4, group='g', minute=2)
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set item_id = 'it', item_order = coalesce(copied_from, id) "
                               "where group_id = 'g'")
        finally:
            await conn.close()

        async def embed(text, settings, kind, local):
            return [[0.0] * 768]
        targets = [(SimpleNamespace(router_model_group='a'), SPACE, True)]
        with patch.object(memory, '_embed', embed), patch.object(memory, 'embedding_targets', AsyncMock(return_value=targets)):
            await memory.delete_items('alice', [b], self.settings)
            await asyncio.gather(*memory._reembed_tasks)
        conn = await registry._connect()
        try:
            spaces = [r['vector_space'] for r in await conn.fetch('select vector_space from orion_memory where id <> $1 '
                                                                    'order by id', c)]
        finally:
            await conn.close()
        self.assertEqual(spaces, [SPACE])  # The other model's copy of the neighbour is gone, not left stale.

    async def recount(self, space):
        """The summary by the old way: one pass over the user's rows."""
        conn = await registry._connect()
        try:
            rows = await conn.fetch(
                "with r as materialized (select coalesce(group_id, 'r' || id::text) as g, vector_space as s, kind, "
                "coalesce(item_id, coalesce(copied_from, id)::text) as i, whole, coalesce(copied_from, id) as c "
                "from orion_memory where user_id = 'alice') "
                "select 'total' as part, '' as key, count(distinct g) as n from r "
                "union all select 'space', s, count(distinct g) from r group by s "
                "union all select 'kind', kind, count(distinct i) from r where not whole group by kind "
                "union all select 'movable', s, count(distinct c) from r where s <> $1 and c not in "
                "(select c from r where s = $1) group by s "
                "union all select 'copied', s, count(distinct c) from r where s <> $1 and c in "
                "(select c from r where s = $1) group by s", space)
        finally:
            await conn.close()
        out = {'total': 0, 'space': {}, 'kind': {}, 'movable': {}, 'copied': {}}
        for r in rows:
            if r['part'] == 'total':
                out['total'] = r['n']
            else:
                out[r['part']][r['key']] = r['n']
        return out

    async def test_the_summary_counters_follow_every_kind_of_write(self):
        other = 'jina-v5'

        async def check(step):
            conn = await registry._connect()
            try:
                got = await memory_groups._summary(conn, 'alice', SPACE)
            finally:
                await conn.close()
            want = await self.recount(SPACE)
            self.assertEqual((got['total'], got['per_vector_space'], got['per_kind'], got['movable'], got['copied']),
                             (want['total'], want['space'], want['kind'], want['movable'], want['copied']), step)
        await check('empty')
        a = await self.add('bir', group='g1', minute=1)
        b = await self.add('iki', group='g1', minute=2, kind='image', media_id='m' * 64)
        await self.add('bir', group='g1', minute=1, space=other, copied_from=a)
        await self.add('üç', group='g2', minute=3, space=other)
        await self.add('dört', group='g3', minute=4, user='bob')
        await check('inserts')
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set group_id = 'g2' where id = $1", b)  # A part moves to another memory.
            await check('regroup')
            await conn.execute("update orion_memory set vector_space = $1 where group_id = 'g2' and vector_space <> $1", SPACE)
            await check('respace')
            await conn.execute("update orion_memory set text = text")  # Nothing counted changes.
            await check('text update')
        finally:
            await conn.close()
        await memory.delete_items('alice', [a])
        await check('delete')
        await memory_groups.delete_groups('alice', ['g2'])
        await check('delete group')
        conn = await registry._connect()
        try:
            await conn.execute('truncate orion_memory')
        finally:
            await conn.close()
        await check('truncate')

    async def test_a_file_name_and_a_caption_are_matched_as_words_not_as_meaning(self):
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await memory.insert_item(conn, 'alice', SPACE, 'g1', {'kind': 'text', 'source_kind': 'file', 'title': 'test.txt'},
                                         [{'text': 'bambaşka içerik', 'vector': vec(0.12, 2)}])
                await memory.insert_item(conn, 'alice', SPACE, 'g2', {'kind': 'text', 'source_kind': 'manual', 'caption': 'Deniz tatili'},
                                         [{'text': 'notum', 'vector': vec(0.1, 3)}])
        finally:
            await conn.close()
        page = await memory_groups.list_groups('alice', self.settings, q='test.txt', limit=10)
        card = next(g for g in page['groups'] if g['group_id'] == 'g1')
        self.assertEqual((card['match'], card['score']), ('text', 0.12))  # Marked as a word hit; the % stays the meaning's.
        tool = await self.tool([{'keyword': 'TEST.TXT'}])
        self.assertEqual(len(tool['results']), 1)
        caption = await memory_groups.list_groups('alice', self.settings, q='deniz', limit=10)
        self.assertEqual(next(g for g in caption['groups'] if g['group_id'] == 'g2')['match'], 'text')

    async def test_the_kind_filter_ranks_only_that_kinds_rows_in_list_panel_tool_and_map(self):
        text = await self.add('metin parçası', score=0.8, axis=2, group='g1', minute=1)
        image = await self.add('foto.png', score=0.3, axis=3, group='g1', minute=2, kind='image', media_id='a' * 64)
        await self.add('başka metin', score=0.5, axis=4, group='g2', minute=3)
        other = await self.add('başka foto', score=0.2, axis=5, group='g3', minute=4, kind='image', media_id='b' * 64)
        page = await memory_groups.list_groups('alice', self.settings, q='x', kind='image', limit=10)
        self.assertEqual([(g['group_id'], g['score'], g['matched_part']) for g in page['groups']],
                         [('g1', 0.3, image), ('g3', 0.2, other)])  # g1 by its image's own %, not its 0.8 text.
        panel = await memory_groups.part_scores('alice', 'g1', self.settings, q='x', kind='image')
        self.assertEqual((panel['score'], panel['best']), (0.3, image))
        tool = await self.tool([{'text': 'x'}])
        self.assertEqual(tool['results'][0]['id'], text)
        from orion.worker.tools.memory import tools as memory_tools
        from orion.worker.tools._base import ToolContext
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            only = await memory_tools.search_memory(memory_tools.MemorySearchInput(
                queries=[memory_tools.MemoryQuery(text='x')], kind='image'), ToolContext('alice', 'chat', 't', None))
        self.assertEqual([(r['id'], r['score']) for r in only['results']], [(image, 30), (other, 20)])
        graph = await memory_groups.map_graph('alice', self.settings, kind='image')
        self.assertEqual({n['group_id'] for n in graph['nodes']}, {'g1', 'g3'})
        self.assertEqual((await memory_groups.map_graph('alice', self.settings))['total'], 3)

    async def test_the_card_names_every_source_of_the_memory(self):
        await self.add('bir not', group='g', source='manual', minute=1)
        await self.add('foto.png', group='g', source='manual', kind='image', media_id='c' * 64, minute=2, axis=3)
        await self.add('dosya parçası', group='g', source='file', title='a.txt', minute=3, axis=4)
        page = await memory_groups.list_groups('alice', self.settings, limit=10)
        card = page['groups'][0]
        self.assertEqual(card['source_kinds'], ['manual', 'file'])
        self.assertEqual(card['sources_label'], 'Metin, Dosya, Görsel')

    async def test_a_kind_filter_uses_only_that_kinds_parts_everywhere_for_a_mixed_memory(self):
        """A chat memory with text (0.30, 0.20) and images (0.41, 0.05): under the text filter its rank, card, panel
        header (best part, all-memory %, matched count) and the tool see only the text; images come back unscored."""
        t1 = await self.add('sohbet metni bir', score=0.30, axis=2, group='g', source='chat_save', minute=1)
        t2 = await self.add('sohbet metni iki', score=0.20, axis=3, group='g', source='chat_save', minute=2)
        i1 = await self.add('a.png', score=0.41, axis=4, group='g', kind='image', media_id='a' * 64, minute=3)
        i2 = await self.add('b.png', score=0.05, axis=5, group='g', kind='image', media_id='b' * 64, minute=4)
        await self.add('başka not', score=0.10, axis=6, group='h', minute=5)
        for kind, best, own in (('text', t1, {t1, t2}), ('image', i1, {i1, i2}), ('all', i1, {t1, t2, i1, i2})):
            with self.subTest(kind):
                page = await memory_groups.list_groups('alice', self.settings, q='x', kind=kind, limit=10)
                card = next(g for g in page['groups'] if g['group_id'] == 'g')
                panel = await memory_groups.part_scores('alice', 'g', self.settings, q='x', kind=kind)
                self.assertEqual((panel['best'], panel['score'], panel['matched']),
                                 (card['matched_part'], card['score'], card['matched_parts']))
                self.assertEqual(panel['best'], best)
                scored = {int(i) for i, v in panel['parts'].items() if v['score'] is not None}
                self.assertEqual(scored, own)
                if kind != 'all':
                    others = {int(i): v for i, v in panel['parts'].items() if int(i) not in own}
                    self.assertTrue(others and all(v['score'] is None and not v['matched'] for v in others.values()))
                if kind != 'all':  # The mean of that kind's piece vectors against the query.
                    pair = {'text': ((0.30, 2), (0.20, 3)), 'image': ((0.41, 4), (0.05, 5))}[kind]
                    total = [a + b for a, b in zip(vec(*pair[0]), vec(*pair[1]))]
                    cosine = total[0] / __import__('math').sqrt(sum(x * x for x in total))
                    self.assertAlmostEqual(panel['all_score'], cosine, places=3)
        text_tool = await self.tool([{'text': 'x'}])
        self.assertEqual(text_tool['results'][0]['id'], i1)
        from orion.worker.tools._base import ToolContext
        from orion.worker.tools.memory import tools as memory_tools
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            only = await memory_tools.search_memory(memory_tools.MemorySearchInput(
                queries=[memory_tools.MemoryQuery(text='x')], kind='text'), ToolContext('alice', 'chat', 't', None))
        row = next(r for r in only['results'] if r['id'] == t1)
        self.assertEqual(row['score'], 30)

    async def test_matched_words_come_back_as_spans_in_text_title_and_caption(self):
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await memory.insert_item(
                    conn, 'alice', SPACE, 'g1', {'kind': 'text', 'source_kind': 'file', 'title': 'Test.TXT', 'caption': 'bir test daha'},
                    [{'text': 'İlk satır test.\nİkinci TEST burada', 'vector': vec(0.2, 2)}])
        finally:
            await conn.close()
        page = await memory_groups.list_groups('alice', self.settings, q='test', limit=10)
        spans = page['groups'][0]['spans']
        text = 'İlk satır test.\nİkinci TEST burada'
        got = [(s['field'], {'text': text, 'title': 'Test.TXT', 'caption': 'bir test daha'}[s['field']][s['start']:s['end']])
               for s in spans]
        self.assertEqual(got, [('text', 'test'), ('text', 'TEST'), ('title', 'Test'), ('caption', 'test')])
        panel = await memory_groups.part_scores('alice', 'g1', self.settings, q='test')
        self.assertEqual(list(panel['parts'].values())[0]['spans'], spans)
        near = await memory_groups.list_groups('alice', self.settings, q='tset satir', limit=10)
        self.assertTrue(all(s['end'] > s['start'] for s in near['groups'][0].get('spans', [])))

    async def test_a_kind_filter_finds_every_image_when_the_index_scan_stops_short(self):
        """95% text: a filtered index scan can end at its scan limit before the images do. The search must still
        return all of them (an exact scan settles it)."""
        for k in range(38):
            await self.add(f'metin {k}', score=0.9 - k * 0.01, axis=2 + k % 700, group=f't{k}', minute=k)
        images = [await self.add(f'foto{k}.png', score=0.2 + k * 0.01, axis=300 + k, group=f'i{k}', minute=50 + k,
                                 kind='image', media_id=f'{k:064d}') for k in range(2)]
        real = memory.ann_fetch

        async def capped(conn, sql, *args):  # Like a graph scan that gave up: the nearest rows, then the filter.
            rows = await real(conn, sql.replace(" and kind = 'image'", ""), *args)
            return [r for r in rows[:10] if r['kind'] == 'image']
        with patch.object(memory, 'ann_fetch', capped):
            page = await memory_groups.list_groups('alice', self.settings, q='x', kind='image', limit=10)
        self.assertEqual({g['matched_part'] for g in page['groups']}, set(images))
        self.assertEqual(memory.filters_sql('all', 'image', []), " and kind = 'image'")

    async def test_cores_concatenate_to_the_original_text_byte_for_byte(self):
        import random
        rng = random.Random(7)
        samples = [
            '"a": "Došlo k chybě",\n"common.success": "Úspěch",\n' * 300,                      # one huge paragraph of lines
            '{"x": "مجدداً",\n"settings.auth.newPassword": "كلمة"}\n' * 200,                   # RTL
            'satır bir\r\nsatır iki\r\n\r\n\r\nüçüncü 😀 paragraf\r\n' * 150,                   # CRLF, blank runs, emoji
            '\n\n  baş boşluk\n\n' + 'kelime ' * 3000 + '\n\nson\n\n\n',                         # edges, one long line
            '😀👍🏽' * 900,                                                                    # no spaces at all
            ''.join(rng.choice('ab c\n\r\t.!?。ç😀') for _ in range(20000)),                    # arbitrary
        ]
        for text in samples:
            with self.subTest(len(text)):
                self.assertEqual(''.join(core for core, _ in memory_pieces.text_pieces(text)), text)
        messages = ['Kullanıcı: ' + 'merhaba\n' * 500, 'Asistan: ' + 'cevap, ' * 900 + '\n\nikinci', 'Kullanıcı: kısa', 'Asistan: tamam']
        pieces = memory_pieces.chat_pieces(messages)
        self.assertEqual(''.join(core for core, _, _, _ in pieces), '\n\n'.join(messages))
        # The item's text comes back through join_pieces exactly; old (stripped) pieces still get a blank line.
        rows = [{'text': core, 'lossless': True} for core, _ in memory_pieces.text_pieces(samples[0])]
        self.assertEqual(memory.join_pieces(rows), samples[0])
        self.assertEqual(memory.join_pieces([{'text': 'a'}, {'text': 'b'}]), 'a\n\nb')

    async def test_a_note_has_an_optional_name_and_can_be_long(self):
        async def embed(text, settings, kind, local):
            return [[1.0] + [0.0] * 767]
        target = (SimpleNamespace(router_model_group='e'), SPACE, True)
        with patch.object(memory, '_embed', embed), patch.object(memory, 'embedding_target', AsyncMock(return_value=target)):
            one = await memory.add_note_to('alice', 'Elif 34 yaşında', self.settings, source_kind='assistant', title='  Elif   Kaya ')
            long_text = '\n\n'.join(f'Bölüm {k}: ' + 'söz ' * 300 for k in range(20))  # Far over the old 4000-character limit.
            long = await memory.add_note_to('alice', long_text, self.settings, title='Uzun not')
            self.assertEqual(one['added'], 1)
            self.assertEqual(memory.MAX_NOTE, 2 * 1024 * 1024)
            from orion.kernel import memory_media_jobs
            for _ in range(200):
                if not [j for j in await memory_media_jobs.jobs('alice') if j['state'] == 'running']:
                    break
                await asyncio.sleep(0.05)
        page = await memory_groups.list_groups('alice', self.settings, limit=10)
        titles = {g['group_id']: g['title'] for g in page['groups']}
        self.assertEqual(titles[one['group_id']], 'Elif Kaya')
        self.assertEqual(titles[long['group_id']], 'Uzun not')
        found = await memory_groups.list_groups('alice', self.settings, q='elif kaya', limit=10)
        self.assertEqual(next(g for g in found['groups'] if g['group_id'] == one['group_id'])['match'], 'text')  # Names count as words.
        self.assertTrue(long.get('job'))

    async def delete_tool(self, **kwargs):
        from orion.worker.tools._base import ToolContext
        from orion.worker.tools.memory import tools as memory_tools
        return await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(**kwargs), ToolContext('alice', 'chat', 't', None))

    async def test_the_chat_model_cannot_delete_without_the_users_press(self):
        a = await self.add('silinecek not', group='g1', minute=1)
        b = await self.add('kalacak not', group='g2', minute=2, axis=3)
        asked = await self.delete_tool(ids=[a])
        self.assertEqual(asked['status'], 'awaiting_user_confirmation')
        self.assertEqual([x['id'] for x in asked['will_delete']], [a])
        self.assertEqual(await self.count_rows(), 2)  # Nothing is gone.
        token = asked['confirmation_id']
        self.assertEqual((await self.delete_tool(confirmation_id=token))['state'], 'pending')
        # Another user cannot answer it.
        from orion.kernel import memory_pending
        self.assertIsNone(await memory_pending.resolve('bob', token, True))
        self.assertEqual(await self.count_rows(), 2)
        done = await routes.memory_pending_confirm(token, 'alice')
        self.assertEqual((done['state'], done['deleted']), ('deleted', [a]))
        self.assertEqual(await self.count_rows(), 1)
        again = await routes.memory_pending_confirm(token, 'alice')  # A second press deletes nothing more.
        self.assertEqual(again['state'], 'deleted')
        self.assertEqual((await self.delete_tool(confirmation_id=token))['state'], 'deleted')
        # Declined: the model is told, nothing goes.
        asked = await self.delete_tool(ids=[b], whole_memory=True)
        self.assertTrue(asked['whole_memory'])
        declined = await routes.memory_pending_decline(asked['confirmation_id'], 'alice')
        self.assertEqual(declined['state'], 'declined')
        self.assertEqual(await self.count_rows(), 1)
        self.assertIn('declined', (await self.delete_tool(confirmation_id=asked['confirmation_id']))['note'])
        self.assertEqual((await routes.memory_pending_confirm(asked['confirmation_id'], 'alice'))['state'], 'declined')
        self.assertEqual(await self.count_rows(), 1)
        # A whole-memory confirmation deletes every part saved with it.
        c = await self.add('bir', group='g3', minute=3, axis=4)
        await self.add('iki', group='g3', minute=4, axis=5)
        asked = await self.delete_tool(ids=[c], whole_memory=True)
        self.assertEqual(asked['parts'], 2)
        await routes.memory_pending_confirm(asked['confirmation_id'], 'alice')
        self.assertEqual(await self.count_rows(), 1)

    async def count_rows(self):
        conn = await registry._connect()
        try:
            return await conn.fetchval('select count(*) from orion_memory')
        finally:
            await conn.close()

    async def test_unlike_queries_leave_memories_out_the_same_way_in_list_panel_and_tool(self):
        ids = await self.seed()  # ten memories, 0.90 ... 0.18; id 7 holds the word "maviş"
        await self.patch_vectors({'a': self.query, 'b': self.vectors_for(None)['b'], 'maviş': self.vectors_for(None)['b']})
        queries = [{'text': 'a'}, {'text': 'maviş', 'like': False}, {'id': ids[2], 'like': False}]
        found = await memory_groups.search_queries('alice', self.settings, queries, limit=20)
        cards = found['groups']
        self.assertNotIn(ids[7], [c['matched_part'] for c in cards])  # Contains the unliked word.
        self.assertNotIn(ids[2], [c['matched_part'] for c in cards])  # The unliked id's own memory.
        shown = [c['score'] for c in cards]
        self.assertEqual(shown, sorted(shown, reverse=True))
        tool = await self.tool(queries)
        self.assertEqual([r['id'] for r in tool['results']], [c['matched_part'] for c in cards][:len(tool['results'])])
        self.assertEqual([r['score'] for r in tool['results']], [round(c['score'] * 100) for c in cards][:len(tool['results'])])
        self.assertTrue(tool.get('left_out'))
        for card in cards[:4]:
            panel = await memory_groups.part_scores('alice', card['group_id'], self.settings, q='a',
                                                    vector=memory._vector(self.query), space=SPACE)
            self.assertEqual((panel['score'], panel['best'], panel['strength']),
                             (card['score'], card['matched_part'], card['strength']))

    async def test_open_memory_files_opens_as_many_as_fit_the_chat_models_context(self):
        from orion.worker.tools._base import ToolContext
        from orion.worker.tools.memory import tools as memory_tools
        self.assertEqual(memory.plan_open([1000, 1000, 1000], 2500), 2)
        self.assertEqual(memory.plan_open([5000, 10], 100), 1)  # The first always opens.
        rows = [await self.add(f'foto{k}.png', kind='image', media_id=f'{k}' * 64, axis=2 + k, group=f'g{k}') for k in range(6)]
        for context, expected in ((8192, 3), (32768, 6)):  # 40% of the window / ~1000 tokens an image.
            with self.subTest(context):
                with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)), \
                        patch.object(memory, 'chat_context', AsyncMock(return_value=context)):
                    fit, later, plan = await memory.open_plan('alice', rows, self.settings)
                self.assertEqual((len(fit), len(later)), (expected, 6 - expected))
                self.assertEqual(fit + later, rows)
                self.assertEqual(plan['budget'], int(context * memory.OPEN_SHARE))

    async def mixed_memory(self):
        """A chat save: a long transcript (text), two screenshots; one is named 'Ekran Görüntüsü (28).png'."""
        transcript = 'Kullanıcı: bak şu resme\r\n\r\nAsistan: ' + 'uzun bir cevap ' * 80 + ' Ekran Görüntüsü (28).png ' + 'devam ' * 80
        text = await self.add(transcript, score=0.3, axis=2, group='g', source='chat_save', title='sohbet', minute=1)
        other = await self.add('Ekran Görüntüsü (5).png — ' + 'çöp altyazı ' * 40, score=0.35, axis=3, group='g',
                               kind='image', media_id='a' * 64, minute=2, source='chat_save')
        named = await self.add('Ekran Görüntüsü (28).png', score=0.2, axis=4, group='g', kind='image',
                               media_id='b' * 64, minute=3, source='chat_save')
        return text, other, named

    async def test_the_tool_shows_the_kind_asked_with_short_labels_and_the_other_media_by_name(self):
        text, other, named = await self.mixed_memory()
        # 6: a kind filter without a query shows that kind's part, not the memory's text.
        from orion.worker.tools._base import ToolContext
        from orion.worker.tools.memory import tools as memory_tools
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            images = await memory_tools.search_memory(memory_tools.MemorySearchInput(kind='image'),
                                                      ToolContext('alice', 'chat', 't', None))
        row = images['results'][0]
        self.assertEqual((row['id'], row['kind']), (other, 'image'))
        # 7: short, one-line labels.
        self.assertLessEqual(len(row['label']), memory_tools.LABEL_CHARS)
        self.assertNotIn('\n', row['label'])
        # 8: the memory's other media, as id/kind/name without captions.
        self.assertEqual(row['media'], [{'id': named, 'kind': 'image', 'name': 'Ekran Görüntüsü (28).png'}])
        # 9: a name hit: found, the named image listed first with a flag, the text label cut around the words.
        found = await self.tool([{'text': 'Ekran Görüntüsü (28).png'}])
        hit = found['results'][0]
        self.assertEqual(hit['match'], 'text')
        self.assertTrue(hit['media'][0].get('matched') or hit['id'] == named)
        ids = {hit['id']} | {m['id'] for m in hit.get('media', [])}
        self.assertIn(named, ids)
        self.assertLessEqual(len(hit['label']), memory_tools.LABEL_CHARS)
        self.assertNotIn('\r', hit['label'])
        if hit['id'] == text:
            self.assertIn('Ekran', hit['label'])  # A snippet around the matched words, not the transcript's start.
        # The no-query note stays short.
        self.assertLess(len(images['note']), 200)
