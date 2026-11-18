"""Memory step 8 (docs/hafiza-tasarimi.md §16): search and the profile block. THE ranking policy (the order is the shown real
%, a word match only marks, unlike is a filter) is one implementation: the page's list, the detail panel and the chat tool
give facts, events, chat windows and notes the same order and the same %, whatever the query. Then `about`, `when`,
`include_past`, `budget` and the entity card of search_memory, and the profile block (pinning, the token limit, immediate
rebuild, locked / past / trashed never in it, user separation). On a throwaway real PostgreSQL (test_memory_search);
embeddings are hand-made vectors."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.api import routes
from orion.kernel import (memory, memory_collections as mc, memory_entities as me, memory_events as ev, memory_groups,
                          memory_profile as mp, memory_trash, registry)
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools
import legacy_add

from test_memory_search import SPACE, PostgresMemoryCase, vec


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


def unit(axis):
    values = [0.0] * 768
    values[axis] = 1.0
    return values


class V2Case(PostgresMemoryCase):
    """The memory with collections on, and an embedding that reads its score off the text: RULES maps a lowercase part of a
    text to (score against the query, axis)."""
    RULES: list = []

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_proposal, orion_mem_fact, orion_mem_entity_merge, orion_mem_entity, orion_mem_event, '
                  'orion_mem_link, orion_mem_trash, orion_mem_profile cascade')
        await sql("insert into orion_chats(id, user_id) values ('c2', 'alice'), ('b1', 'bob') on conflict do nothing")
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_trash_days=30, memory_profile=True)
        self.off = SimpleNamespace(router_model_group='chat', memory_v2=False, memory_trash_days=30, memory_profile=True)
        self.settings = self.v2
        self.current = self.v2
        self.table = {}  # query text -> its vector (everything else: the query axis)

        async def embed(text, settings, kind, local):
            low = text.lower()
            for part, score, axis in self.RULES:
                if part in low:
                    return [vec(score, axis)]
            return [vec(0.05, 40 + sum(map(ord, low)) % 700)]

        async def target(settings, space=None):
            return settings, SPACE, False

        async def query_vector(q, settings, space=None):
            return SPACE, memory._vector(self.table.get(q.strip(), self.query)), None
        for module, name, value in ((memory, '_embed', embed), (memory, 'embedding_target', target),
                                    (memory, '_query_vector', query_vector)):
            guard = patch.object(module, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        from orion.kernel import memory_backup
        guard = patch.object(memory_backup, 'backup', AsyncMock(return_value=None))
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch('orion.kernel.config.get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.current))
        guard.start()
        self.addCleanup(guard.stop)

    def context(self, user='alice', chat='chat'):
        return ToolContext(user, chat, 't', None)

    async def write(self, text, **kw):  # the tool, as the model calls it (type fact)
        user = kw.pop('user', 'alice')
        args = legacy_add.MemoryAddInput.model_validate({'text': text, 'type': kw.pop('type', 'fact'), **kw})
        return await legacy_add.add_memory(args, self.context(user, kw.get('chat', 'chat')))

    async def find(self, queries=(), user='alice', **kw):
        args = memory_tools.MemorySearchInput.model_validate({'queries': list(queries), **kw})
        return await memory_tools.search_memory(args, self.context(user))

    async def fact(self, entity, category, statement, user='alice', since=None, relation=None, kind='person'):
        async with me._db() as conn:
            found, _ = await me.resolve_entity(conn, user, entity, kind=kind, relation=relation)
        done = await me.add_fact(user, self.v2, found, category, statement, origin='user', source={'by_user': True},
                                 since=since)
        return done['fact']['id']


class RankingConsistencyTests(V2Case):
    RULES = [('yemeği', 0.80, 5), ('annemin doğum günü 17 kasım', 0.90, 2), ('ilkokula', 0.55, 3), ('kapadokya', 0.70, 4)]

    async def seed(self):
        await self.write('Annemin doğum günü 17 Kasım.', about='Ayşe', relation='anne', category='date')
        await self.write('Kızım Elif ilkokula başladı.', about='Elif', relation='kızım', category='other')
        await self.write('Kapadokya gezisi, balonlara bindik.', type='event', title='Kapadokya gezisi', date='2026-06-05',
                       end_date='2026-06-08', participants=['Elif'])
        await self.write('Annemin doğum günü yemeği.', type='event', title='Doğum günü yemeği', date='2026-11-17',
                       participants=['Ayşe'])
        self.window1 = await self.add_chat('Kullanıcı: kapadokya hakkında konuşalım', 0.62, 6)
        self.window2 = await self.add_chat('Kullanıcı: akşam yemeği ne olsun', 0.35, 7)
        self.note = await self.add_row('Çarşamba pazara gidilecek', 0.45, 8)
        for k in range(14):  # A pool of ordinary memories the good ones stand out of.
            await self.add_row(f'sıradan {k}', 0.04, 20 + k)

    async def add_chat(self, text, score, axis):
        row = await self.add(text, score=score, axis=axis, group=f'chat{axis}', source='assistant', chat='chat',
                             minute=axis)
        await sql("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) values "
                  "('alice', 'belgeler', $1, true) on conflict do nothing", f'chat{axis}')
        return row

    async def add_row(self, text, score, axis):
        return await self.add(text, score=score, axis=axis, group=f'n{axis}', minute=axis)

    async def run_case(self, queries, mode='together', collections=None, about=None, when=None):
        plain = [{'text': q.get('text', ''), 'id': q.get('id'), 'keyword': q.get('keyword', ''), 'like': q.get('like', True)}
                 for q in queries]
        page = await memory_groups.search_queries('alice', self.v2, plain, mode='each' if mode == 'separate' else 'together',
                                                  limit=20, collections=collections, about=about, when=when)
        tool_args = {'queries': queries, 'mode': mode, 'page_size': 20}
        if collections:
            tool_args['in'] = collections
        if about is not None:
            tool_args['about'] = 'Ayşe'
        if when is not None:
            tool_args['when'] = {'from': '2026-06', 'to': '2026-06'}
        tool = await memory_tools.search_memory(memory_tools.MemorySearchInput.model_validate(tool_args), self.context())
        return page, tool

    async def test_one_order_and_one_percent_for_every_kind_and_every_query(self):
        await self.seed()
        self.table.update({'b': unit(5), 'gezi tatil': unit(4)})
        everything = None  # The whole memory (notes are in no collection).
        cases = [
            ('single text', [{'text': 'a'}], 'together', everything, None, None),
            ('a word that matches rows only marks them', [{'text': 'kapadokya'}], 'together', everything, None, None),
            ('together', [{'text': 'a'}, {'text': 'b'}], 'together', everything, None, None),
            ('separate', [{'text': 'a'}, {'text': 'b'}], 'separate', everything, None, None),
            ('unlike filters', [{'text': 'a'}, {'text': 'gezi tatil', 'like': False}], 'together', everything, None, None),
            ('me', [{'text': 'a'}], 'together', ['me'], None, None),
            ('experiences', [{'text': 'a'}], 'together', ['experiences'], None, None),
            ('keyword', [{'keyword': 'kapadokya'}], 'together', everything, None, None),
            ('about', [{'text': 'a'}], 'together', None, 'about', None),
            ('when', [{'text': 'a'}], 'together', None, None, 'when'),
        ]
        seen_kinds = set()
        for name, queries, mode, collections, about, when in cases:
            with self.subTest(name):
                ayse = None
                if about:
                    async with me._db() as conn:
                        ayse = [(await me.find_entities(conn, 'alice', 'Ayşe'))[0]['id']]
                span = ev.parse_range('2026-06', '2026-06') if when else None
                page, tool = await self.run_case(queries, mode, collections, ayse, span)
                cards = page['groups']
                self.assertTrue(cards, name)
                shown = [c['score'] for c in cards if c.get('score') is not None]
                self.assertEqual(shown, sorted(shown, reverse=True))  # The order IS the shown %: nothing else lifts a card.
                n = len(tool['results'])
                self.assertEqual([r['id'] for r in tool['results']], [c['matched_part'] for c in cards[:n]])
                if 'score' in tool['results'][0]:  # A keyword alone has no meaning score: its words only.
                    self.assertEqual([r['score'] for r in tool['results']], [round(c['score'] * 100) for c in cards[:n]])
                seen_kinds |= {key for r in tool['results'] for key in ('fact', 'event') if key in r}
                if len([q for q in queries if q.get('like', True)]) == 1 and queries[0].get('text'):
                    vector = memory._vector(self.table.get(queries[0]['text'], self.query))
                    for card in cards[:4]:  # The detail panel's number for the same memory is the card's.
                        panel = await memory_groups.part_scores('alice', card['group_id'], self.v2, q=queries[0]['text'],
                                                                vector=vector, space=SPACE)
                        self.assertEqual((panel['score'], panel['best'], panel['match'], panel['strength']),
                                         (card['score'], card['matched_part'], card['match'], card['strength']))
        self.assertEqual(seen_kinds, {'fact', 'event'})

    async def test_the_mixed_order_is_by_real_score_across_kinds_not_by_kind(self):
        await self.seed()
        page, tool = await self.run_case([{'text': 'a'}], collections=['me', 'experiences', 'documents'])
        scores = [round(c['score'], 2) for c in page['groups']]
        self.assertEqual(scores, [0.90, 0.80, 0.70, 0.62, 0.55, 0.35])  # fact, event, event, note, fact, note: interleaved.
        kinds = ['fact' if c.get('fact') else 'event' if c.get('event') else 'note' for c in page['groups']]
        self.assertEqual(kinds, ['fact', 'event', 'event', 'note', 'fact', 'note'])
        word, _ = await self.run_case([{'text': 'kapadokya'}], collections=['me', 'experiences', 'documents'])
        self.assertEqual([c['score'] for c in word['groups']], [c['score'] for c in page['groups']])  # Words did not reorder.
        self.assertIn('text', {c['match'] for c in word['groups']})  # ...they only mark.

    async def test_unlike_is_a_filter_that_leaves_the_rest_in_order(self):
        await self.seed()
        self.table['gezi tatil'] = unit(4)
        full, _ = await self.run_case([{'text': 'a'}], collections=['me', 'experiences', 'documents'])
        page, tool = await self.run_case([{'text': 'a'}, {'text': 'gezi tatil', 'like': False}],
                                         collections=['me', 'experiences', 'documents'])
        left = [c['group_id'] for c in page['groups']]
        self.assertLess(len(left), len(full['groups']))
        self.assertEqual(left, [g for g in (c['group_id'] for c in full['groups']) if g in left])  # Same relative order.
        self.assertEqual([c['score'] for c in page['groups']],
                         [c['score'] for c in full['groups'] if c['group_id'] in left])  # Same %.
        self.assertTrue(tool.get('left_out'))
        self.assertEqual([r['id'] for r in tool['results']], [c['matched_part'] for c in page['groups']][:len(tool['results'])])


class SearchOptionTests(V2Case):
    """about, when, include_past, budget and the entity card of search_memory."""
    RULES = [('yaşıyor', 0.80, 2), ('doğum', 0.60, 3), ('okul', 0.50, 4)]

    async def lives(self):
        """Ayşe lived in Ankara from 2019 and moved to İstanbul on 2026-11-01 (the old version is kept as history)."""
        first = await self.fact('Ayşe', 'yer', "Ayşe Ankara'da yaşıyor.", since='2019-01-01', relation='anne')
        done = await me.supersede('alice', self.v2, first, "Ayşe İstanbul'da yaşıyor.", origin='user',
                                  source={'by_user': True}, since='2026-11-01')
        return first, done['fact']['id']

    async def test_defaults_are_todays_search_and_the_new_keys_appear_only_when_asked(self):
        await self.lives()
        found = await self.find([{'text': 'nerede yaşıyor'}])
        self.assertEqual([r['fact']['about'] for r in found['results']], ['Ayşe (anne)'])
        self.assertEqual([r['label'] for r in found['results']], ["Ayşe İstanbul'da yaşıyor."])
        self.assertNotIn('past_facts', found)
        self.assertNotIn('budget', found)
        self.assertEqual(found['page'], 1)

    async def test_include_past_lists_replaced_versions_by_words_without_a_score(self):
        first, head = await self.lives()
        found = await self.find([{'text': 'Ankara yaşıyor'}], include_past=True)
        self.assertEqual([p['id'] for p in found['past_facts']], [first])
        past = found['past_facts'][0]
        self.assertEqual((past['statement'], past['since'], past['until'], past['past'], past['about']),
                         ("Ayşe Ankara'da yaşıyor.", '2019-01-01', '2026-11-01', True, 'Ayşe (anne)'))
        self.assertNotIn('score', past)
        self.assertNotIn(first, [r['fact']['id'] for r in found['results'] if 'fact' in r])  # Not among the scored results.
        none = await self.find([{'text': 'zürafa'}], include_past=True)  # No word of the query in any past version.
        self.assertEqual(none['past_facts'], [])

    @staticmethod
    def span(first, last=None):
        return {'from': first, 'to': last if last is not None else first}

    async def test_when_shows_the_facts_as_they_stood_in_that_range(self):
        first, head = await self.lives()
        then = await self.find([{'text': 'nerede yaşıyor'}], when=self.span('2022-06-01'))
        self.assertEqual([r['fact']['id'] for r in then['results'] if 'fact' in r], [])  # Istanbul had not begun.
        self.assertEqual([p['id'] for p in then['past_facts']], [first])
        now = await self.find([{'text': 'nerede yaşıyor'}], when=self.span('2027-01-01'))
        self.assertEqual([r['fact']['id'] for r in now['results'] if 'fact' in r], [head])
        self.assertEqual(now['past_facts'], [])  # Ankara ended on 2026-11-01.
        before = await self.find([{'text': 'nerede yaşıyor'}], when=self.span('2018-01-01'))
        self.assertEqual(before['past_facts'], [])  # It had not begun in 2018.
        year = await self.find([{'text': 'nerede yaşıyor'}], when=self.span('2026'))  # A year: both versions touch it.
        self.assertEqual([r['fact']['id'] for r in year['results'] if 'fact' in r], [head])
        self.assertEqual([p['id'] for p in year['past_facts']], [first])
        bad = await self.find([{'text': 'x'}], when=self.span('13/2022'))
        self.assertIn('error', bad)

    async def test_a_past_version_without_an_end_date_ends_when_orion_stopped_counting_it(self):
        old = await self.fact('Ayşe', 'yer', "Ayşe Bursa'da yaşıyor.")
        await me.supersede('alice', self.v2, old, "Ayşe İzmir'de yaşıyor.", origin='user', source={'by_user': True})
        await sql("update orion_mem_fact set expired_at = timestamptz '2025-03-01' where id = $1", old)
        before = await self.find([{'text': 'Bursa'}], when=self.span('2025-02-01'))
        self.assertEqual([p['id'] for p in before['past_facts']], [old])
        after = await self.find([{'text': 'Bursa'}], when=self.span('2025-04-01'))
        self.assertEqual(after['past_facts'], [])

    async def test_a_locked_fact_is_never_a_past_version_in_a_search(self):
        old = await self.fact('Ayşe', 'sağlık', 'Ayşe diyabet hastası.')
        await me.supersede('alice', self.v2, old, 'Ayşe diyabet tedavisi gördü.', origin='user', source={'by_user': True})
        found = await self.find([{'text': 'diyabet'}], include_past=True)
        self.assertEqual(found['past_facts'], [])
        self.assertNotIn('diyabet', json.dumps(found, ensure_ascii=False))

    async def test_about_limits_to_one_entity_and_ranks_inside_it(self):
        ayse = await self.fact('Ayşe', 'tarih', 'Annemin doğum günü 17 Kasım.', relation='anne')
        await self.fact('Elif', 'tarih', 'Elif doğum günü 3 Mart.', relation='kızım')
        await self.write('Annemin doğum günü yemeği', type='event', title='Doğum günü yemeği', date='2026-11-17',
                         participants=['Ayşe'])
        await self.write('Elif okul gezisi', type='event', title='Okul gezisi', date='2026-05-02', participants=['Elif'])
        found = await self.find([{'text': 'doğum günü'}], about='Ayşe')
        labels = [r['label'] for r in found['results']]
        self.assertEqual(len(labels), 2)
        self.assertTrue(all('Elif' not in label for label in labels))
        self.assertEqual(found['entity']['name'], 'Ayşe')
        newest = await self.find([], about='Ayşe')  # No query: the newest of that entity's memories.
        self.assertEqual(len(newest['results']), 2)
        self.assertEqual([f['id'] for f in found['entity']['facts']], [ayse])
        nobody = await self.find([{'text': 'x'}], about='Zeynep')
        self.assertEqual(nobody['results'], [])
        self.assertIn('no one called', nobody['note'])
        async with me._db() as conn:
            two = await conn.fetchval("insert into orion_mem_entity (user_id, kind, name, keys) values ('alice', 'person', "
                                      "'Ayşe', array['ayse']) returning id")
        ambiguous = await self.find([{'text': 'x'}], about='Ayşe')
        self.assertIn('More than one', ambiguous['error'])
        chosen = await self.find([{'text': 'x'}], about=f'#{two}')
        self.assertEqual(chosen['results'], [])  # The second Ayşe has nothing.

    async def test_the_entity_card_comes_when_the_query_names_one(self):
        await self.fact('Ayşe', 'tarih', 'Annemin doğum günü 17 Kasım.', relation='anne')
        await self.fact('Ayşe', 'sağlık', 'Ayşe diyabet hastası.')
        await self.write('Annemin doğum günü yemeği', type='event', title='Doğum günü yemeği', date='2026-11-17',
                         participants=['Ayşe'])
        found = await self.find([{'text': 'annem ne zaman doğdu'}])
        card = found['entity']
        self.assertEqual((card['name'], card['relation']), ('Ayşe', 'anne'))
        self.assertEqual([f['statement'] for f in card['facts']], ['Annemin doğum günü 17 Kasım.'])
        self.assertEqual(card['locked_facts'], 1)  # Counted, never shown.
        self.assertNotIn('diyabet', json.dumps(found, ensure_ascii=False))
        self.assertEqual([e['title'] for e in card['recent_events']], ['Doğum günü yemeği'])
        suffix = await self.find([{'text': 'annemin yaşı'}])
        self.assertEqual(suffix['entity']['name'], 'Ayşe')  # A Turkish suffix on the name still names it.
        other = await self.find([{'text': 'annelerimizle konuştuk'}])  # A word that only starts with it is not the name.
        self.assertNotIn('entity', other)
        sentence = await self.find([{'text': 'ben bugün ne yedim'}])  # "ben" in a sentence is not about the user.
        self.assertNotIn('entity', sentence)
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        self.assertEqual((await self.find([{'text': 'ben'}]))['entity']['kind'], 'self')
        async with me._db() as conn:
            await conn.execute("insert into orion_mem_entity (user_id, kind, name, keys) values ('alice', 'person', 'Ayşe', "
                               "array['ayse'])")
        two = await self.find([{'text': 'ayşe'}])
        self.assertNotIn('entity', two)
        self.assertEqual(len(two['entity_candidates']), 2)  # Two fit: no card, they are named.

    async def test_memory_v2_off_ignores_every_new_argument(self):
        await self.lives()
        self.current = self.off
        found = await self.find([{'text': 'yaşıyor'}], when=self.span('2022-06-01'), include_past=True, about='Ayşe')
        for key in ('entity', 'past_facts', 'entity_candidates'):
            self.assertNotIn(key, found)

    async def test_a_budget_returns_whole_results_best_first_and_says_when_it_cut(self):
        for k in range(12):
            await self.add(f'uzun not numara {k} ' + 'söz ' * 40, score=0.9 - k * 0.05, axis=2 + k, group=f'g{k}', minute=k)
        full = await self.find([{'text': 'a'}], page_size=12)
        sizes = [memory_tools._tokens(r) for r in full['results']]
        small = await self.find([{'text': 'a'}], budget=sum(sizes[:3]) + 5)
        self.assertEqual([r['id'] for r in small['results']], [r['id'] for r in full['results'][:3]])  # A prefix, in order.
        self.assertTrue(small['more'])
        self.assertLessEqual(small['budget']['used'], small['budget']['limit'])
        big = await self.find([{'text': 'a'}], budget=8000)
        self.assertEqual([r['id'] for r in big['results']], [r['id'] for r in full['results']])
        self.assertFalse(big['more'])
        self.assertNotIn('page', big)
        paged = await self.find([{'text': 'a'}], budget=500, page=2)
        self.assertIn('error', paged)
        tiny = await self.find([{'text': 'a'}], budget=100)
        self.assertLessEqual(tiny['budget']['used'], 100)

    async def test_a_budget_counts_the_card_and_the_past_first_and_trims_the_card_whole_facts_at_a_time(self):
        for k in range(8):
            await self.fact('Ayşe', 'diğer', f'Ayşe şeyi {k} sever ve bunu uzun uzun anlatır durur her zaman.',
                            relation='anne')
        await self.lives()
        found = await self.find([{'text': 'Ayşe nerede yaşıyor'}], budget=300, include_past=True)
        self.assertLessEqual(found['budget']['used'], 300)
        self.assertTrue(found['entity'].get('trimmed'))
        self.assertLess(memory_tools._tokens(found['entity']), 300 * 0.6 + 1)
        for fact in found['entity']['facts']:
            self.assertTrue(fact['statement'].endswith('.'))  # Never half a fact.


class PageSearchRouteTests(V2Case):
    """The memory page's `GET /api/v1/memory` takes the tool's `about` and the one date filter (one code: list_groups)."""
    RULES = [('yaşıyor', 0.80, 2), ('doğum', 0.60, 3), ('okul', 0.50, 4)]

    async def lives(self):
        first = await self.fact('Ayşe', 'yer', "Ayşe Ankara'da yaşıyor.", since='2019-01-01', relation='anne')
        done = await me.supersede('alice', self.v2, first, "Ayşe İstanbul'da yaşıyor.", origin='user',
                                  source={'by_user': True}, since='2026-11-01')
        return first, done['fact']['id']

    async def page(self, q='', **kw):
        guard = patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.current))
        guard.start()
        self.addCleanup(guard.stop)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        args = dict(q=q, limit=20, offset=0, vector_space='all', kind='all', exclude=[], collection=[], when_from='',
                    when_to='', about='', current_user='alice')
        return await routes.list_memory(request, **{**args, **kw})

    async def test_about_and_the_date_reach_the_page_and_past_versions_stay_in_the_facts_history(self):
        first, head = await self.lives()
        found = await self.page('nerede yaşıyor', about='Ayşe')
        self.assertEqual([g['fact']['id'] for g in found['groups'] if 'fact' in g], [head])
        self.assertEqual(found['entity']['name'], 'Ayşe')
        self.assertNotIn('past_facts', found)  # The page lists no past versions: the fact's detail does.
        self.assertNotIn('side', found)
        then = await self.page('nerede yaşıyor', about='#' + str(found['entity']['id']), when_from='2022-06-01',
                               when_to='2022-06-01')
        self.assertEqual([g['fact']['id'] for g in then['groups'] if 'fact' in g], [])  # Istanbul had not begun.
        self.assertNotIn('past_facts', then)
        history = await me.history('alice', self.v2, head)
        self.assertEqual([v['id'] for v in history['versions']], [first, head])

    async def test_the_page_without_the_new_params_is_what_it_was(self):
        await self.lives()
        found = await self.page('nerede yaşıyor')
        for key in ('past_facts', 'entity_candidates', 'note', 'side'):
            self.assertNotIn(key, found)
        self.assertEqual(found['entity']['name'], 'Ayşe') if 'entity' in found else None  # A query naming her gets the card.

    async def test_unknown_and_ambiguous_names_answer_with_a_note_not_an_error(self):
        await self.lives()
        async with me._db() as conn:  # A second Ayşe.
            await conn.execute("insert into orion_mem_entity (user_id, kind, name, keys) values ('alice', 'person', 'Ayşe', "
                               "array['ayse'])")
        nobody = await self.page('x', about='Zeynep')
        self.assertEqual((nobody['groups'], nobody['more']), ([], False))
        self.assertIn('Zeynep', nobody['note'])
        two = await self.page('x', about='Ayşe')
        self.assertEqual(two['groups'], [])
        self.assertEqual(len(two['entity_candidates']), 2)

    async def test_memory_v2_off_ignores_the_new_params(self):
        await self.lives()
        self.current = self.off
        found = await self.page('yaşıyor', about='Ayşe', when_from='2022-06-01', when_to='2022-06-01')
        for key in ('entity', 'past_facts', 'entity_candidates', 'note'):
            self.assertNotIn(key, found)

    async def test_a_bad_day_is_a_400(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            await self.page('x', when_from='13/2022')
        self.assertEqual(caught.exception.status_code, 400)


class ProfileTests(V2Case):
    async def test_the_block_is_the_users_own_facts_newest_first_inside_the_limits_with_whole_facts(self):
        await self.fact('Ben', 'diğer', 'Kahve içmeyi sever.')
        await self.fact('Ben', 'tercih', 'Çayı şekersiz içer.')
        await self.fact('Ayşe', 'ilişki', 'Ayşe annesidir.', relation='anne')  # A close person's fact: not in by itself.
        await self.fact('Ben', 'iş', 'Yazılım mühendisi olarak çalışır.')
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        block = await mp.build('alice', self.v2)
        self.assertEqual([i['text'] for i in block['lines']],
                         ['Adı Kürşat.', 'Yazılım mühendisi olarak çalışır.', 'Çayı şekersiz içer.', 'Kahve içmeyi sever.'])
        self.assertTrue(block['text'].startswith(mp.HEADER))
        self.assertLessEqual(block['tokens'], mp.TOKEN_LIMIT)
        self.assertEqual((mp.SOFT_LIMIT, mp.TOKEN_LIMIT), (600, 800))
        with patch.object(mp, '_compact_later') as later:  # Past the hard limit the model is asked to compact (not here).
            for k in range(40):
                await self.fact('Ben', 'diğer', f'Uzun sırada gelen bir bilgi, numara {k}, tamamen yazılmış cümle. ' + 'x' * 60)
            block = await mp.build('alice', self.v2)
        later.assert_called_once()
        self.assertTrue(block['needs_compaction'])
        self.assertLessEqual(block['tokens'], mp.SOFT_LIMIT)  # Until the compaction is ready the soft limit holds.
        self.assertGreater(block['left_out'], 0)
        for line in block['text'].split('\n')[1:]:
            self.assertTrue(line.startswith('- ') and line.endswith(('.', 'x')))
        # With the model's compacted lines the non-pinned part is those lines.
        done = mp.compose([], [{'id': 1, 'statement': 'Uzun bir bilgi. ' * 400, 'category': 'diğer'}], mp.TOKEN_LIMIT,
                          mp.SOFT_LIMIT, ['Kısa özet.'])
        self.assertEqual([i['text'] for i in done['lines']], ['Kısa özet.'])
        self.assertFalse(done['needs_compaction'])

    async def test_a_fact_that_does_not_fit_is_left_out_whole_and_a_shorter_one_after_it_still_goes_in(self):
        for letter in 'ABCDE':
            await self.fact('Ben', 'kimlik', letter * 480)  # About 160 tokens each: together they pass the hard limit.
        await self.fact('Ben', 'tercih', 'Kısa.')
        with patch.object(mp, '_compact_later'):
            block = await mp.build('alice', self.v2)
        self.assertEqual([i['text'] for i in block['lines']], ['Kısa.', 'E' * 480, 'D' * 480, 'C' * 480])  # Newest first.
        self.assertEqual(block['left_out'], 2)  # B and A were left out whole.

    async def test_never_in_the_block_locked_past_trashed_or_another_users(self):
        locked = await self.fact('Ben', 'sağlık', 'Diyabet hastası.')
        old = await self.fact('Ben', 'yer', "Ankara'da yaşar.")
        await me.supersede('alice', self.v2, old, "İstanbul'da yaşar.", origin='user', source={'by_user': True})
        gone = await self.fact('Ben', 'tercih', 'Çayı sever.')
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        await self.fact('Ben', 'kimlik', 'Adı Bob.', user='bob')
        block = await mp.build('alice', self.v2)
        text = block['text']
        self.assertNotIn('Diyabet', text)
        self.assertNotIn('Ankara', text)
        self.assertNotIn('Bob', text)
        self.assertIn("İstanbul'da yaşar.", text)
        self.assertIn('Çayı sever.', text)
        self.assertNotIn(locked, block['fact_ids'])
        removed = await me.remove_fact('alice', self.v2, gone)  # To the trash: gone from the very next build.
        self.assertNotIn('Çayı sever.', (await mp.build('alice', self.v2))['text'])
        await memory_trash.restore('alice', [removed['trash_id']])
        self.assertIn('Çayı sever.', (await mp.build('alice', self.v2))['text'])
        bob = await mp.build('bob', self.v2)
        self.assertEqual([i['text'] for i in bob['lines']], ['Adı Bob.'])

    async def test_the_text_stays_the_same_until_a_fact_changes_and_the_built_block_is_stored_only_then(self):
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        first = (await mp.for_chat('alice', self.v2))['text']
        built = await sql("select built_at from orion_mem_profile where user_id = 'alice'")
        again = (await mp.for_chat('alice', self.v2))['text']
        self.assertEqual(first, again)  # Byte for byte: the model's prompt cache stays valid.
        self.assertEqual(built, await sql("select built_at from orion_mem_profile where user_id = 'alice'"))  # No rewrite.
        await self.fact('Ben', 'yer', "İstanbul'da yaşar.")
        changed = (await mp.for_chat('alice', self.v2))['text']
        self.assertNotEqual(first, changed)
        self.assertIn("İstanbul'da yaşar.", changed)
        stored = json.loads(await sql("select generated from orion_mem_profile where user_id = 'alice'"))
        self.assertEqual([g['text'] for g in stored], ["İstanbul'da yaşar.", 'Adı Kürşat.'])  # Newest first.

    async def test_pinned_lines_always_go_in_first_and_survive_a_rebuild(self):
        fact = await self.fact('Ben', 'diğer', 'Kahve içmeyi sever.')
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        got = await mp.set_pinned('alice', self.v2, [{'text': 'Bana "sen" diye hitap et.'}])
        self.assertEqual(got['pinned'][0]['state'], 'ok')
        lines = [i['text'] for i in (await mp.build('alice', self.v2))['lines']]
        self.assertEqual(lines, ['Bana "sen" diye hitap et.', 'Adı Kürşat.', 'Kahve içmeyi sever.'])
        await self.fact('Ben', 'iş', 'Mühendis.')  # A rebuild does not drop the pin.
        self.assertEqual((await mp.build('alice', self.v2))['lines'][0]['text'], 'Bana "sen" diye hitap et.')
        pinned = await mp.pin_fact('alice', self.v2, fact, text='Kahveyi çok sever, sabah içer.')
        self.assertEqual([p['fact_id'] for p in pinned['pinned']], [None, fact])
        lines = [i['text'] for i in (await mp.build('alice', self.v2))['lines']]
        self.assertIn('Kahveyi çok sever, sabah içer.', lines)
        self.assertNotIn('Kahve içmeyi sever.', lines)  # The fact is not repeated beside its own pinned wording.
        await mp.unpin_fact('alice', self.v2, fact)
        self.assertIn('Kahve içmeyi sever.', [i['text'] for i in (await mp.build('alice', self.v2))['lines']])

    async def test_pinned_alone_past_the_limit_overflows_and_empties_the_generated_part(self):
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        items = [{'text': f'Satır {k} ' + 'x' * 280} for k in range(10)]
        got = await mp.set_pinned('alice', self.v2, items)
        self.assertTrue(got['overflow'])
        self.assertEqual(got['generated'], [])
        block = await mp.build('alice', self.v2)
        self.assertEqual(len(block['lines']), 10)  # Every pinned line is still in.
        self.assertGreater(block['tokens'], mp.TOKEN_LIMIT)
        await mp.set_pinned('alice', self.v2, items[:1])
        self.assertFalse((await mp.get('alice', self.v2))['overflow'])

    async def test_a_pinned_line_of_a_replaced_or_removed_fact_is_not_sent_and_says_why(self):
        old = await self.fact('Ben', 'yer', "Ankara'da yaşar.")
        gone = await self.fact('Ben', 'tercih', 'Çayı sever.')
        await mp.set_pinned('alice', self.v2, [{'text': "Ankara'da yaşar.", 'fact_id': old},
                                               {'text': 'Çayı sever.', 'fact_id': gone}])
        await me.supersede('alice', self.v2, old, "İstanbul'da yaşar.", origin='user', source={'by_user': True})
        await me.remove_fact('alice', self.v2, gone)
        got = await mp.get('alice', self.v2)
        self.assertEqual([p['state'] for p in got['pinned']], ['replaced', 'gone'])
        self.assertNotIn('Ankara', got['text'])
        self.assertNotIn('Çayı', got['text'])
        self.assertIn("İstanbul'da yaşar.", got['text'])  # The new version comes in by itself.

    async def test_what_cannot_be_pinned(self):
        secret = await self.fact('Ben', 'sağlık', 'Diyabet hastası.')
        bobs = await self.fact('Ben', 'kimlik', 'Adı Bob.', user='bob')
        old = await self.fact('Ben', 'yer', "Ankara'da yaşar.")
        await me.supersede('alice', self.v2, old, "İstanbul'da yaşar.", origin='user', source={'by_user': True})
        with self.assertRaises(LookupError):
            await mp.pin_fact('alice', self.v2, bobs)  # Another user's fact: not found.
        with self.assertRaises(ValueError):
            await mp.pin_fact('alice', self.v2, secret)
        with self.assertRaises(LookupError):
            await mp.set_pinned('alice', self.v2, [{'text': 'x', 'fact_id': bobs}])
        with self.assertRaises(ValueError):
            await mp.set_pinned('alice', self.v2, [{'text': 'x', 'fact_id': old}])  # Replaced: pin the new one.
        with self.assertRaises(ValueError):
            await mp.set_pinned('alice', self.v2, [{'text': 'y' * (mp.MAX_PINNED_CHARS + 1)}])
        with self.assertRaises(ValueError):
            await mp.set_pinned('alice', self.v2, [{'text': f't{k}'} for k in range(mp.MAX_PINNED + 1)])
        self.assertEqual((await mp.get('bob', self.v2))['pinned'], [])
        with self.assertRaises(ValueError):
            await mp.get('alice', self.off)

    async def test_off_means_off_and_the_system_message_carries_the_block(self):
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        self.assertIsNone(await mp.for_chat('alice', self.off))
        self.assertIsNone(await mp.for_chat('alice', SimpleNamespace(memory_v2=True, memory_profile=False)))
        self.assertIsNone(await mp.for_chat('alice', SimpleNamespace()))  # A plain settings object: nothing, no database.
        self.assertIsNone(await mp.for_chat('nobody', self.v2))  # Nothing known: no block.
        block = await mp.for_chat('alice', self.v2)
        self.assertEqual(mp.with_system_prompt('Sen Orion.', block), 'Sen Orion.\n\n' + block['text'])
        self.assertEqual(mp.with_system_prompt('', block), block['text'])
        self.assertEqual(mp.with_system_prompt('Sen Orion.', None), 'Sen Orion.')
        header = mp.log_header(block)
        self.assertEqual(list(header), ['x-orion-memory-profile'])
        self.assertEqual(header['x-orion-memory-profile'], f"tokens={block['tokens']}; facts={block['fact_ids'][0]}")
        self.assertNotIn('Kürşat', json.dumps(header))  # The Router's log line never has the text.
        with patch.object(mp, 'build', AsyncMock(side_effect=RuntimeError('db down'))):
            self.assertIsNone(await mp.for_chat('alice', self.v2))  # A chat never breaks for its profile.

    async def test_the_log_line_has_counts_and_ids_but_never_the_text(self):
        await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        with self.assertLogs('orion.kernel.memory_profile', level='INFO') as logged:
            await mp.for_chat('alice', self.v2)
        self.assertIn('tokens=', logged.output[0])
        self.assertNotIn('Kürşat', logged.output[0])

    async def test_the_routes_serve_the_block_and_the_pins(self):
        fact = await self.fact('Ben', 'kimlik', 'Adı Kürşat.')
        guard = patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.current))
        guard.start()
        self.addCleanup(guard.stop)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        got = await routes.memory_profile_get(request, current_user='alice')
        self.assertEqual(got['limit'], mp.TOKEN_LIMIT)
        self.assertEqual([g['fact_id'] for g in got['generated']], [fact])
        got = await routes.memory_profile_pin(routes.ProfilePin(fact_id=fact, text='Adım Kürşat.'), request,
                                              current_user='alice')
        self.assertEqual(got['pinned'][0]['text'], 'Adım Kürşat.')
        got = await routes.memory_profile_set_pinned(routes.ProfilePinned(items=[routes.ProfileLine(text='Merhaba')]),
                                                     request, current_user='alice')
        self.assertEqual([p['text'] for p in got['pinned']], ['Merhaba'])
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            await routes.memory_profile_pin(routes.ProfilePin(fact_id=fact + 999), request, current_user='alice')
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(HTTPException) as caught:
            await routes.memory_profile_set_pinned(routes.ProfilePinned(items=[routes.ProfileLine(text='x' * 400)]),
                                                   request, current_user='alice')
        self.assertEqual(caught.exception.status_code, 400)
        await routes.memory_profile_unpin(fact, request, current_user='alice')
        self.current = self.off
        with self.assertRaises(HTTPException) as caught:
            await routes.memory_profile_get(request, current_user='alice')
        self.assertEqual(caught.exception.status_code, 400)

    async def test_building_the_block_stays_cheap_with_a_few_thousand_facts(self):
        import time
        async with me._db() as conn:
            entity, _ = await me.resolve_entity(conn, 'alice', 'Ben')
            await conn.execute(
                "insert into orion_mem_fact (user_id, entity_id, lineage, category, statement, sensitive, origin) "
                "select 'alice', $1, g, (array['kimlik','ilişki','iş','yer','tercih','diğer'])[1 + g % 6], "
                "'Sayılı bilgi ' || g || ' cümlesi.', false, 'user' from generate_series(1, 3000) g", entity['id'])
        started = time.perf_counter()
        block = await mp.build('alice', self.v2)
        elapsed = time.perf_counter() - started
        self.assertLessEqual(block['tokens'], mp.TOKEN_LIMIT)
        self.assertLess(elapsed, 1.0, elapsed)  # A generous ceiling for a loaded machine; the numbers are in the hand-over.
