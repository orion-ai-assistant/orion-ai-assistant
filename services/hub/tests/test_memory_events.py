"""Memory step 5 (docs/hafiza-tasarimi.md §16): events and albums. Date resolution and precision, add_memory type=event
(sources, entities, photos, albums), the `when` filter of search_memory, versions, the trash and purge of events, albums
(kept apart from a memory's main collection), yearly recurrences and user separation of every new table. On a throwaway
real PostgreSQL (test_memory_search); embeddings are hand-made vectors (a text with "doğum" is near the query)."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from orion.kernel import (memory, memory_albums as al, memory_collections as mc, memory_entities as me, memory_events as ev,
                          memory_trash, registry)
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import SPACE, PostgresMemoryCase, vec

TODAY = date(2026, 11, 18)  # A Wednesday.


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class DateTests(unittest.TestCase):
    """resolve_date: no database, a fixed message time."""

    def resolve(self, *args, **kw):
        return ev.resolve_date(*args, reference=datetime(2026, 11, 18, 23, 30).astimezone(), **kw)

    def test_iso_forms_give_their_own_precision_and_range(self):
        day = self.resolve('2026-11-17')
        self.assertEqual((day['on'], day['end'], day['precision']), (date(2026, 11, 17), None, 'day'))
        month = self.resolve('2026-02')
        self.assertEqual((month['on'], month['end'], month['precision']), (date(2026, 2, 1), date(2026, 2, 28), 'month'))
        year = self.resolve('2025')
        self.assertEqual((year['on'], year['end'], year['precision']), (date(2025, 1, 1), date(2025, 12, 31), 'year'))
        trip = self.resolve('2026-06-05', '2026-06-08')
        self.assertEqual((trip['on'], trip['end'], trip['precision']), (date(2026, 6, 5), date(2026, 6, 8), 'day'))

    def test_precision_widens_a_date_and_approx_keeps_a_range(self):
        whole_month = self.resolve('2026-07-15', precision='month')
        self.assertEqual((whole_month['on'], whole_month['end']), (date(2026, 7, 1), date(2026, 7, 31)))
        summer = self.resolve('2025-07', precision='approx', said_as='geçen yaz')
        self.assertEqual((summer['precision'], summer['on'], summer['end'], summer['said_as']),
                         ('approx', date(2025, 7, 1), date(2025, 7, 31), 'geçen yaz'))
        around = self.resolve('2025-07-10', precision='approx')
        self.assertEqual((around['on'], around['end']), (date(2025, 7, 10), None))
        with self.assertRaises(ValueError):
            self.resolve('2026-07', precision='day')
        with self.assertRaises(ValueError):
            self.resolve('2026', precision='month')
        with self.assertRaises(ValueError):
            self.resolve('2026-07-10', '2026-07-01')
        with self.assertRaises(ValueError):
            self.resolve('2026-07-10', precision='soon')

    def test_relative_words_are_counted_from_the_messages_own_day(self):
        for words, expected in (('bugün', date(2026, 11, 18)), ('Dün', date(2026, 11, 17)),
                                ('evvelsi gün', date(2026, 11, 16)), ('yarın', date(2026, 11, 19)),
                                ('yesterday', date(2026, 11, 17)), ('today', date(2026, 11, 18)),
                                ('geçen cuma', date(2026, 11, 13)), ('last Wednesday', date(2026, 11, 11)),
                                ('gecen pazartesi', date(2026, 11, 16))):
            got = self.resolve(None, said_as=words)
            self.assertEqual((got['on'], got['precision']), (expected, 'day'), words)
        # A chat written yesterday: "bugün" is yesterday.
        old = ev.resolve_date(None, said_as='bugün', reference=datetime(2026, 11, 3, 10, 0).astimezone())
        self.assertEqual(old['on'], date(2026, 11, 3))

    def test_a_date_that_disagrees_with_a_relative_word_loses_and_the_answer_says_so(self):
        got = self.resolve('2026-11-10', said_as='dün')
        self.assertEqual(got['on'], date(2026, 11, 17))
        self.assertIn('2026-11-17', got['note'])
        same = self.resolve('2026-11-17', said_as='dün')
        self.assertIsNone(same['note'])
        vague = self.resolve('2025-07', precision='approx', said_as='geçen yaz')  # Not a word of the table: kept.
        self.assertEqual(vague['on'], date(2025, 7, 1))

    def test_no_date_is_refused_with_todays_date_for_the_model(self):
        with self.assertRaises(ValueError) as caught:
            self.resolve(None)
        self.assertIn('2026-11-18', str(caught.exception))
        with self.assertRaises(ValueError):
            self.resolve('17.11.2026')
        with self.assertRaises(ValueError):
            self.resolve('2026-02-30')

    def test_ranges_and_recurrence_days(self):
        self.assertEqual(ev.parse_range('2026-03', '2026-03'), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertEqual(ev.parse_range('2026', ''), (date(2026, 1, 1), None))
        self.assertIsNone(ev.parse_range('', ''))
        with self.assertRaises(ValueError):
            ev.parse_range('2026-05', '2026-04')
        self.assertEqual(me.recurring_of('2026-11-17'), 'yearly:11-17')
        self.assertEqual(me.recurring_of('--02-29'), 'yearly:02-29')
        self.assertEqual(me.recurring_of('03-04'), 'yearly:03-04')
        with self.assertRaises(ValueError):
            me.recurring_of('02-30')
        self.assertEqual(ev.next_occurrence('yearly:11-17', TODAY), date(2027, 11, 17))
        self.assertEqual(ev.next_occurrence('yearly:11-18', TODAY), TODAY)
        self.assertEqual(ev.next_occurrence('yearly:02-29', date(2026, 2, 1)), date(2026, 2, 28))  # No 29th in 2026.
        self.assertEqual(ev.next_occurrence('yearly:02-29', date(2027, 12, 1)), date(2028, 2, 29))


class EventTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_proposal, orion_mem_fact, orion_mem_entity_merge, orion_mem_entity, orion_mem_event, '
                  'orion_mem_link, orion_mem_trash cascade')
        await sql("insert into orion_chats(id, user_id) values ('c2', 'alice'), ('b1', 'bob') on conflict do nothing")
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_trash_days=30)
        self.off = SimpleNamespace(router_model_group='chat', memory_v2=False, memory_trash_days=30)
        self.embedded = []

        async def embed(text, settings, kind, local):
            self.embedded.append(text)
            return [vec(0.9, 5) if 'doğum' in text.lower() else vec(0.1, 6 + len(self.embedded) % 700)]

        async def target(settings, space=None):
            return settings, SPACE, False
        for module, name, value in ((memory, '_embed', embed), (memory, 'embedding_target', target),
                                    (ev, 'today_of', lambda reference=None: TODAY)):
            guard = patch.object(module, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        from orion.kernel import memory_backup
        guard = patch.object(memory_backup, 'backup', AsyncMock(return_value=None))
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch('orion.kernel.config.get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.v2))
        guard.start()
        self.addCleanup(guard.stop)

    def context(self, user='alice', chat='chat'):
        return ToolContext(user, chat, 't', None)

    async def add(self, text, user='alice', chat='chat', **kw):  # the tool, as the model calls it
        args = memory_tools.MemoryAddInput.model_validate({'text': text, 'type': 'event', **kw})
        return await memory_tools.add_memory(args, self.context(user, chat))

    async def find(self, sorgu='doğum günü', user='alice', **kw):
        queries = [{'text': sorgu}] if sorgu else []
        args = memory_tools.MemorySearchInput.model_validate({'queries': queries, **kw})
        return await memory_tools.search_memory(args, self.context(user))

    async def counts(self, user='alice'):
        """{album name: live members} of the user's albums (the project "Eski hafıza" left out)."""
        return {a['name']: a['count'] for a in await al.list_albums(user, self.v2) if a['name'] != mc.OLD_NAME}

    async def note(self, text, user='alice'):
        done = await memory.add_note_to(user, text, self.v2, 'manual')
        return done['group_id']

    async def photo(self, user='alice', name='kapadokya.jpg'):
        """An image memory (one row, no file behind it) and its group key."""
        group = memory.new_group()
        conn = await registry._connect()
        try:
            await mc.ensure_user(conn, user)
            row = await conn.fetchval(
                memory.ROW_SQL, user, name, SPACE, memory._vector(vec(0.3, 9)), 'file', name, 'image', None, 'image/jpeg',
                None, group, None, None, memory.new_group(), False, None, None, None, None, memory.item_order(), None,
                memory.fold(name), None, True)
        finally:
            await conn.close()
        return row, group

    async def mother_day(self):
        done = await self.add('Annemin doğum günüydü, pasta yedik.', title='Annemin doğum günü', date='2026-11-17',
                              participants=['Ayşe', 'me'], place='Kadıköy')
        return done['added']['event_id']

    # --- writing -------------------------------------------------------------------------------------------------

    async def test_an_event_has_a_source_a_vector_in_yasadiklarim_entities_and_is_found(self):
        done = await self.mother_day()
        row = (await sql('select * from orion_mem_event where id = $1', done, fetch=True))[0]
        self.assertEqual((row['title'], row['occurred_on'], row['occurred_end'], row['precision'], row['origin'],
                          row['lineage']), ('Annemin doğum günü', date(2026, 11, 17), None, 'day', 'model', done))
        self.assertEqual(row['body'], 'Annemin doğum günüydü, pasta yedik.')
        source = await sql("select chat_id, by_user from orion_mem_source where target_type = 'event' and target_id = $1",
                           done, fetch=True)
        self.assertEqual([(s['chat_id'], s['by_user']) for s in source], [('chat', False)])
        vector = await sql("select text, source_kind, source_chat_title from orion_memory where group_id = $1",
                           row['group_id'], fetch=True)
        self.assertEqual(len(vector), 1)
        self.assertEqual(vector[0]['source_kind'], 'event')
        self.assertTrue(vector[0]['text'].startswith('2026-11-17 Salı, Kasım 2026: Annemin doğum günü.'))
        self.assertIn('Katılanlar: Ayşe, Ben.', vector[0]['text'])  # Ayşe (a new person) and the user's own entity.
        self.assertIn('Yer: Kadıköy.', vector[0]['text'])
        member = await sql("select collection_id, main from orion_mem_collection_item where group_id = $1",
                           row['group_id'], fetch=True)
        self.assertEqual([(m['collection_id'], m['main']) for m in member], [('yasadiklarim', True)])
        links = await sql("select to_type, role from orion_mem_link where from_id = $1 order by role", done, fetch=True)
        self.assertEqual([(link['to_type'], link['role']) for link in links], [('entity', 'katılan'), ('entity', 'katılan'),
                                                                               ('entity', 'yer')])
        self.assertEqual(await sql("select kind from orion_mem_entity where name = 'Kadıköy'"), 'place')
        found = await self.find(**{'in': ['experiences']})
        self.assertEqual(len(found['results']), 1)
        self.assertEqual(found['results'][0]['event'], {
            'id': done, 'date': '2026-11-17', 'end': None, 'precision': 'day', 'title': 'Annemin doğum günü',
            'with': ['Ayşe', 'Ben'], 'place': 'Kadıköy', 'photos': 0})
        self.assertEqual(len((await self.find())['results']), 1)  # The default search is the whole memory.
        self.assertEqual((await self.find(**{'in': ['me']}))['results'], [])

    async def test_a_relative_word_is_resolved_by_the_hub_and_vague_times_stay_approx(self):
        today = await self.add('Bugün parkta yürüdüm.', title='Parkta yürüyüş', said_as='bugün')
        self.assertEqual((today['added']['date'], today['added']['precision'], today['added']['said_as']),
                         ('2026-11-18', 'day', 'bugün'))
        wrong = await self.add('Dün sinemaya gittik.', title='Sinema', date='2026-11-02', said_as='dün')
        self.assertEqual(wrong['added']['date'], '2026-11-17')
        self.assertIn('2026-11-17', wrong['date_note'])
        summer = await self.add('Geçen yaz Bodrum', title='Bodrum tatili', date='2025-07', precision='approx',
                                said_as='geçen yaz')
        self.assertEqual((summer['added']['date'], summer['added']['precision']), ('2025-07-01 to 2025-07-31', 'approx'))
        row = (await sql("select occurred_on, occurred_end, said_as from orion_mem_event where title = 'Bodrum tatili'",
                         fetch=True))[0]
        self.assertEqual((row['occurred_end'], row['said_as']), (date(2025, 7, 31), 'geçen yaz'))
        undated = await self.add('Bir şey oldu.')
        self.assertIn('error', undated)
        self.assertIn('2026-11-18', undated['error'])
        self.assertEqual(await sql('select count(*) from orion_mem_event'), 3)

    async def test_nothing_is_written_with_a_refused_value_and_defaults_keep_today(self):
        for bad in ({'date': '2026-13-01'}, {'date': '2026-11-17', 'precision': 'day', 'end_date': '2026-11-01'}):
            self.assertIn('error', await self.add('Bir olay', **bad))
        with self.assertRaises(ValueError):  # The page's longer title field is checked by the same code.
            await ev.add_event('alice', self.v2, 'x' * 200, ev.resolve_date('2026-11-17'), origin='user',
                               source={'by_user': True})
        with self.assertRaises(ValueError):  # No source, no event.
            await ev.add_event('alice', self.v2, 'Bir olay', ev.resolve_date('2026-11-17'), origin='user', source={})
        self.assertEqual(await sql('select count(*) from orion_mem_event'), 0)
        self.assertEqual(await sql("select count(*) from orion_memory where source_kind = 'event'"), 0)
        self.assertEqual(await sql('select count(*) from orion_mem_entity'), 0)  # A refused event made no entity either.
        added = await memory_tools.add_memory(memory_tools.MemoryAddInput.model_validate({'text': 'Pizza severim'}),
                                              self.context())
        self.assertEqual(added, {'added': 'Pizza severim'})
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.off)):
            refused = await self.add('Bir olay', date='2026-11-17')
            self.assertIn('error', refused)
            with self.assertRaises(ValueError):
                await ev.list_events('alice', self.off)
        self.assertEqual(await sql('select count(*) from orion_mem_event'), 0)

    async def test_the_same_event_twice_adds_a_source_and_links_not_a_second_event(self):
        first = await self.mother_day()
        again = await self.add('Annemin doğum günü', title='annemin doğum günü', date='2026-11-17', chat='c2',
                               participants=['Elif'])
        self.assertEqual(again['same_as'], first)
        self.assertEqual(await sql('select count(*) from orion_mem_event'), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_source where target_type = 'event' and target_id = $1",
                                   first), 2)
        self.assertEqual(await sql("select count(*) from orion_mem_link where from_id = $1 and role = 'katılan'", first), 3)
        other_day = await self.add('Annemin doğum günü', title='Annemin doğum günü', date='2026-11-18')
        self.assertNotEqual(other_day['added']['event_id'], first)

    async def test_two_entities_with_a_name_make_the_event_ask_which(self):
        await me.create_entity('alice', self.v2, 'Ayşe', relation='anne')
        two = await me.create_entity('alice', self.v2, 'Ayşe', relation='komşu')
        refused = await self.add('Çay içtik', date='2026-11-17', participants=['Ayşe'])
        self.assertIn('error', refused)
        self.assertEqual(len(refused['candidates']), 2)
        chosen = await self.add('Çay içtik', date='2026-11-17', participants=[f"#{two['id']}"])
        self.assertEqual(chosen['added']['with'], ['Ayşe'])

    # --- search: when --------------------------------------------------------------------------------------------

    async def test_when_filters_events_facts_and_linked_photos_by_their_dates(self):
        march = await self.add('Elif doğum günü', title='Elif doğum günü', date='2026-03-03')
        nov = await self.add('Annemin doğum günü', title='Annemin doğum günü', date='2026-11-17')
        trip = await self.add('Doğum günü gezisi', title='Gezi', date='2026-06-05', end_date='2026-06-08')
        year = await self.add('Doğum günü yılı', title='Yıl', date='2024', precision='year')
        row, group = await self.photo()
        await self.add('x', event=march['added']['event_id'], items=[row])
        fact = await memory_tools.add_memory(memory_tools.MemoryAddInput.model_validate({
            'text': 'Doğum günü partisi hep Mart ayında.', 'type': 'fact', 'about': 'Elif', 'since': '2020-01-01'}),
            self.context())
        note = await self.note('doğum günü notu, tarihsiz')

        async def seen(**when):
            found = await self.find(when=when)
            return (sorted(r['event']['title'] for r in found['results'] if 'event' in r),
                    sum(1 for r in found['results'] if 'fact' in r))
        # The fact (true since 2020, never closed) is in every range that reaches 2020 or later.
        self.assertEqual(await seen(**{'from': '2026-03', 'to': '2026-03'}), (['Elif doğum günü'], 1))
        self.assertEqual(await seen(**{'from': '2026-06-07', 'to': '2026-06-07'}), (['Gezi'], 1))  # Inside the trip.
        self.assertEqual(await seen(**{'from': '2026-06-09', 'to': '2026-06-30'}), ([], 1))
        self.assertEqual(await seen(**{'from': '2026-11-01'}), (['Annemin doğum günü'], 1))
        self.assertEqual(await seen(**{'to': '2023-12'}), ([], 1))  # The year 2024 starts after it.
        self.assertEqual(await seen(**{'to': '2024-01'}), (['Yıl'], 1))
        self.assertEqual(await seen(**{'from': '2024', 'to': '2024'}), (['Yıl'], 1))
        self.assertEqual(await seen(**{'to': '2019-12'}), ([], 0))
        # The photo is found by its event's date, without any query words.
        photos = await self.find('', when={'from': '2026-03-01', 'to': '2026-03-31'}, kind='image')
        self.assertEqual([r['id'] for r in photos['results']], [row])
        self.assertEqual((await self.find('', when={'from': '2026-07', 'to': '2026-07'}, kind='image'))['results'], [])
        # Without `when` the undated note is there as before; with it, never.
        every = [await sql('select group_id from orion_memory where id = $1', r['id'])
                 for r in (await self.find())['results']]
        self.assertIn(note, every)
        dated = await self.find(when={'from': '2000', 'to': '2100'})
        self.assertNotIn(note, [await sql('select group_id from orion_memory where id = $1', r['id'])
                                for r in dated['results']])
        # A bad range is an answer, not a crash; the keyword path and `in` take it too.
        self.assertIn('error', await self.find(when={'from': '2026-05', 'to': '2026-04'}))
        inside = await self.find('', when={'from': '2026-11', 'to': '2026-11'}, **{'in': ['experiences']})
        self.assertEqual(len(inside['results']), 1)
        kw = await memory_tools.search_memory(memory_tools.MemorySearchInput.model_validate({
            'queries': [{'keyword': 'günü'}], 'when': {'from': '2026-11', 'to': '2026-11'}}), self.context())
        self.assertEqual([r['event']['id'] for r in kw['results'] if 'event' in r], [nov['added']['event_id']])
        self.assertTrue(trip and year and fact)

    async def test_when_is_ignored_for_a_user_without_collections(self):
        await self.add('Annemin doğum günü', date='2026-11-17')
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.off)):
            found = await self.find(when={'from': '2020', 'to': '2020'})
        self.assertEqual(len(found['results']), 1)  # The old behaviour: the whole memory.

    # --- photos ---------------------------------------------------------------------------------------------------

    async def test_photos_are_linked_listed_greyed_in_the_trash_and_unlinked_for_good(self):
        event_id = (await self.add('Kapadokya', title='Kapadokya gezisi', date='2026-06-05', end_date='2026-06-08'))['added'][
            'event_id']
        row, group = await self.photo()
        linked = await self.add('x', event=event_id, items=[row])
        self.assertEqual(linked, {'linked': 1, 'event_id': event_id})
        again = await self.add('x', event=event_id, items=[row])  # Linking twice is one link.
        self.assertEqual(again['linked'], 1)
        self.assertEqual(await sql("select count(*) from orion_mem_link where to_type = 'item'"), 1)
        card = await ev.get_event('alice', self.v2, event_id)
        self.assertEqual([(i['group_id'], i['kind'], i['removed']) for i in card['items']], [(group, 'image', False)])
        context = await memory_tools.memory_context(memory_tools.MemoryContextInput(id=row), self.context())
        self.assertEqual([e['id'] for e in context['events']], [event_id])
        # An item that is not the user's, or does not exist, links nothing.
        other_row, _ = await self.photo('bob', 'bob.jpg')
        self.assertIn('error', await self.add('x', event=event_id, items=[other_row]))
        self.assertIn('error', await self.add('x', event=event_id, items=[99999]))
        self.assertEqual(await sql("select count(*) from orion_mem_link where to_type = 'item'"), 1)
        # Removed to the trash: still linked, shown greyed; restored: back.
        removed = await memory_trash.trash_groups('alice', [group])
        card = await ev.get_event('alice', self.v2, event_id)
        self.assertEqual([i['removed'] for i in card['items']], [True])
        await memory_trash.restore('alice', [removed['trash_id']])
        self.assertEqual([i['removed'] for i in (await ev.get_event('alice', self.v2, event_id))['items']], [False])
        # Deleted for good: the link goes, the event stays.
        removed = await memory_trash.trash_groups('alice', [group])
        await memory_trash.purge('alice', [removed['trash_id']], self.v2)
        self.assertEqual(await sql("select count(*) from orion_mem_link where to_type = 'item'"), 0)
        self.assertEqual((await ev.get_event('alice', self.v2, event_id))['items'], [])
        # The event can be created with its photos at once, and unlinked by the user.
        row2, group2 = await self.photo(name='b.jpg')
        with_photo = await self.add('Piknik', title='Piknik', date='2026-07-01', items=[row2])
        self.assertEqual(with_photo['added']['photos'], 1)
        done = await ev.set_items('alice', self.v2, with_photo['added']['event_id'], remove=[group2])
        self.assertEqual(done, {'linked': 0, 'unlinked': 1})
        self.assertEqual(await sql("select count(*) from orion_memory where group_id = $1", group2), 1)  # Never deleted.

    # --- versions, the trash --------------------------------------------------------------------------------------

    async def test_the_user_corrects_an_event_as_a_new_version_and_links_follow(self):
        event_id = await self.mother_day()
        row, group = await self.photo()
        await self.add('x', event=event_id, items=[row])
        old_group = await sql('select group_id from orion_mem_event where id = $1', event_id)
        changed = await ev.replace_event('alice', self.v2, event_id, title='Annemin 60. doğum günü',
                                         when=ev.resolve_date('2026-11-16'))
        new_id = changed['event']['id']
        self.assertNotEqual(new_id, event_id)
        self.assertEqual((changed['event']['date'], changed['event']['title']), ('2026-11-16', 'Annemin 60. doğum günü'))
        self.assertEqual([p['name'] for p in changed['event']['participants']], ['Ayşe', 'Ben'])  # Kept.
        self.assertEqual(changed['event']['place']['name'], 'Kadıköy')
        self.assertEqual([i['group_id'] for i in changed['event']['items']], [group])
        old = (await sql('select * from orion_mem_event where id = $1', event_id, fetch=True))[0]
        self.assertEqual((old['superseded_by'], old['lineage']), (new_id, event_id))
        self.assertIsNotNone(old['expired_at'])
        self.assertEqual(await sql('select count(*) from orion_memory where group_id = $1', old_group), 0)
        card = await ev.get_event('alice', self.v2, event_id)  # Any version's id reads the current one.
        self.assertEqual((card['id'], [p['date'] for p in card['past_versions']]), (new_id, ['2026-11-17']))
        self.assertEqual(await sql('select count(*) from orion_mem_link where from_id = $1', event_id), 0)
        found = await self.find(**{'in': ['experiences']})
        self.assertEqual([r['event']['id'] for r in found['results']], [new_id])
        with self.assertRaises(LookupError):
            await ev.replace_event('alice', self.v2, event_id, title='x')  # The old version is closed.
        cleared = await ev.replace_event('alice', self.v2, new_id, participants=[], place='')
        self.assertEqual((cleared['event']['participants'], cleared['event']['place']), ([], None))
        listing = await ev.list_events('alice', self.v2)
        self.assertEqual([e['id'] for e in listing['events']], [cleared['event']['id']])

    async def test_remove_restore_and_purge_of_an_event_with_its_history(self):
        event_id = await self.mother_day()
        newer = (await ev.replace_event('alice', self.v2, event_id, title='Doğum günü pastası'))['event']['id']
        removed = await ev.remove_event('alice', self.v2, newer)
        self.assertEqual((await ev.list_events('alice', self.v2))['events'], [])
        self.assertEqual((await self.find())['results'], [])
        with self.assertRaises(LookupError):
            await ev.get_event('alice', self.v2, newer)
        listing = await memory_trash.list_trash('alice', self.v2)  # The trash page shows it as a memory.
        self.assertEqual(len(listing['entries']), 1)
        await memory_trash.restore('alice', [removed['trash_id']])
        self.assertEqual(len((await ev.list_events('alice', self.v2))['events']), 1)
        removed = await ev.remove_event('alice', self.v2, event_id)  # By any version's id.
        await memory_trash.purge('alice', [removed['trash_id']], self.v2)
        for table in ('orion_mem_event', 'orion_mem_link'):
            self.assertEqual(await sql(f'select count(*) from {table}'), 0, table)
        self.assertEqual(await sql("select count(*) from orion_mem_source where target_type = 'event'"), 0)

    # --- the timeline ---------------------------------------------------------------------------------------------

    async def test_the_timeline_lists_newest_first_with_years_pages_and_an_entity_filter(self):
        for title, day in (('A', '2026-11-17'), ('B', '2026-03-02'), ('C', '2025-12-31'), ('D', '2025-01')):
            await self.add(title, title=title, date=day, participants=['Ayşe'] if title in 'AC' else [])
        page = await ev.list_events('alice', self.v2, limit=3)
        self.assertEqual([e['title'] for e in page['events']], ['A', 'B', 'C'])
        self.assertTrue(page['more'])
        self.assertEqual(page['years'], [{'year': 2026, 'count': 2}, {'year': 2025, 'count': 2}])
        rest = await ev.list_events('alice', self.v2, limit=3, offset=3)
        self.assertEqual(([e['title'] for e in rest['events']], rest['more'], rest['years']), (['D'], False, []))
        self.assertEqual(rest['events'][0]['precision'], 'month')
        ayse = await sql("select id from orion_mem_entity where name = 'Ayşe'")
        by_person = await ev.list_events('alice', self.v2, entity_id=ayse)
        self.assertEqual([e['title'] for e in by_person['events']], ['A', 'C'])
        in_2025 = await ev.list_events('alice', self.v2, '2025', '2025')
        self.assertEqual([e['title'] for e in in_2025['events']], ['C', 'D'])
        with self.assertRaises(ValueError):
            await ev.list_events('alice', self.v2, '2026-05', '2026-04')

    # --- albums ---------------------------------------------------------------------------------------------------

    async def test_albums_group_memories_without_changing_them(self):
        group = await self.note('kedi')
        event_id = await self.mother_day()
        event_group = await sql('select group_id from orion_mem_event where id = $1', event_id)
        album = await al.create_album('alice', self.v2, 'Kapadokya 2026')
        self.assertEqual(album['name'], 'Kapadokya 2026')
        for taken in ('kapadokya 2026', 'Ben', 'documents', ''):  # Unique after folding; not a built-in or an `in` word.
            with self.assertRaises(ValueError):
                await al.create_album('alice', self.v2, taken)
        done = await al.set_members('alice', self.v2, album['id'], add=[group, event_group])
        self.assertEqual(done, {'added': 2, 'removed': 0})
        self.assertEqual((await al.set_members('alice', self.v2, album['id'], add=[group]))['added'], 0)
        self.assertEqual(await self.counts(), {'Kapadokya 2026': 2})
        # Search by the album's name; the event is still in Yaşadıklarım too.
        inside = await self.find('', **{'in': ['Kapadokya 2026']})
        self.assertEqual(len(inside['results']), 2)
        self.assertEqual(len((await self.find(**{'in': ['experiences']}))['results']), 1)
        # Taking a memory out never deletes it or touches its main collection.
        out = await al.set_members('alice', self.v2, album['id'], remove=[group, event_group])
        self.assertEqual(out, {'added': 0, 'removed': 2})
        self.assertEqual(await sql("select collection_id from orion_mem_collection_item where group_id = $1 and main",
                                   event_group), 'yasadiklarim')  # The event's main collection never changed.
        self.assertEqual(await sql('select count(*) from orion_memory where group_id = $1', group), 1)
        with self.assertRaises(LookupError):
            await al.set_members('alice', self.v2, album['id'], add=['nonesuch'])
        # The model files a new note or event into an album that exists, never makes one.
        added = await memory_tools.add_memory(memory_tools.MemoryAddInput.model_validate({
            'text': 'Balon turu notu', 'collection': 'kapadokya 2026'}), self.context())
        self.assertEqual(added['album'], 'kapadokya 2026')  # As the model named it.
        event = await self.add('Balon', title='Balon turu', date='2026-06-06', collection='Kapadokya 2026')
        self.assertEqual(event['added']['title'], 'Balon turu')
        self.assertEqual(await self.counts(), {'Kapadokya 2026': 2})
        missing = await self.add('x', date='2026-06-06', collection='Yok böyle')
        self.assertIn('Kapadokya 2026', missing['error'])
        for built_in in ('Ben', 'experiences'):
            self.assertIn('error', await self.add('x', date='2026-06-06', collection=built_in))
        # Rename; remove softly (its members stay) and bring it back whole.
        renamed = await al.rename_album('alice', self.v2, album['id'], 'Kapadokya')
        self.assertEqual(renamed['name'], 'Kapadokya')
        await al.remove_album('alice', self.v2, album['id'])
        self.assertEqual(await self.counts(), {})
        self.assertIn('error', await self.find('', **{'in': ['Kapadokya']}))  # A removed album is unknown to search.
        self.assertEqual(await sql('select count(*) from orion_mem_collection_item where collection_id = $1', album['id']), 2)
        other = await al.create_album('alice', self.v2, 'Kapadokya')  # Its name is free again...
        with self.assertRaises(ValueError):
            await al.restore_album('alice', self.v2, album['id'])  # ...so it cannot come back under it.
        await al.rename_album('alice', self.v2, other['id'], 'Başka')
        back = await al.restore_album('alice', self.v2, album['id'])
        self.assertEqual(back['name'], 'Kapadokya')
        self.assertEqual(await self.counts(), {'Başka': 0, 'Kapadokya': 2})
        albums = await al.albums_of('alice', self.v2, group)
        self.assertEqual({a['name']: a['member'] for a in albums if a['name'] != mc.OLD_NAME},
                         {'Başka': False, 'Kapadokya': False})

    async def test_an_album_forgets_memories_deleted_for_good(self):
        group = await self.note('kedi')
        album = await al.create_album('alice', self.v2, 'Kediler')
        await al.set_members('alice', self.v2, album['id'], add=[group])
        removed = await memory_trash.trash_groups('alice', [group])
        self.assertEqual(await self.counts(), {'Kediler': 0})  # In the trash: not counted.
        await memory_trash.purge('alice', [removed['trash_id']], self.v2)
        self.assertEqual(await sql('select count(*) from orion_mem_collection_item where collection_id = $1', album['id']), 0)

    # --- recurrences ----------------------------------------------------------------------------------------------

    async def test_yearly_facts_list_what_is_coming_and_survive_a_new_version(self):
        async def fact(text, **kw):
            args = memory_tools.MemoryAddInput.model_validate({'text': text, 'type': 'fact', **kw})
            return await memory_tools.add_memory(args, self.context())
        mother = await fact('Annemin doğum günü 17 Kasım.', about='Ayşe', relation='anne', category='date', repeats='yearly',
                            date='2026-11-17')
        self.assertEqual(mother['added']['repeats'], 'yearly:11-17')
        await fact('Evlilik yıldönümü 12 Haziran.', about='me', category='date', repeats='yearly', date='06-12')
        await fact('Doğum günüm 2 Aralık.', about='me', category='date', repeats='yearly', date='12-02')
        await fact('Kedisi Pamuk.', about='me')  # Not recurring.
        self.assertIn('error', await fact('Bir gün.', about='me', repeats='yearly'))
        listed = await ev.upcoming('alice', self.v2, days=30, today=TODAY)
        self.assertEqual([(u['statement'], u['next'], u['in_days']) for u in listed],
                         [('Doğum günüm 2 Aralık.', '2026-12-02', 14)])
        wide = await ev.upcoming('alice', self.v2, days=366, today=TODAY)
        self.assertEqual([u['in_days'] for u in wide], [14, 206, 364])
        # The mother's birthday passed yesterday: the next one is next year, and a new version keeps repeating.
        newer = await me.supersede('alice', self.v2, mother['added']['fact_id'], 'Annemin doğum günü 18 Kasım.',
                                   origin='user', source={'by_user': True})
        self.assertEqual(newer['fact']['recurring'], 'yearly:11-17')
        stopped = await me.supersede('alice', self.v2, newer['fact']['id'], 'Annemin doğum günü bilinmiyor.', origin='user',
                                     source={'by_user': True}, recurring='')
        self.assertIsNone(stopped['fact']['recurring'])
        self.assertEqual(len(await ev.upcoming('alice', self.v2, days=366, today=TODAY)), 2)
        # A locked fact never shows in the list.
        conn = await registry._connect()
        try:
            entity = await me._self_entity(conn, 'alice')
        finally:
            await conn.close()
        await me.add_fact('alice', self.v2, entity, 'sağlık', 'Doktor kontrolü her 5 Aralık.', origin='user',
                          source={'by_user': True}, recurring='12-05')
        self.assertEqual([u['in_days'] for u in await ev.upcoming('alice', self.v2, days=30, today=TODAY)], [14])

    # --- entity merge ---------------------------------------------------------------------------------------------

    async def test_merging_entities_moves_event_links_and_undo_puts_them_back(self):
        a = await me.create_entity('alice', self.v2, 'Ayşe', relation='anne')
        b = await me.create_entity('alice', self.v2, 'Ayşe Hanım')
        only_b = (await self.add('Çay', date='2026-11-01', participants=[f"#{b['id']}"]))['added']['event_id']
        both = (await self.add('Yemek', date='2026-11-02', participants=[f"#{a['id']}", f"#{b['id']}"]))['added']['event_id']
        merged = await me.merge_entities('alice', self.v2, b['id'], a['id'])
        self.assertEqual(merged['moved_links'], 2)
        for event_id in (only_b, both):
            people = (await ev.get_event('alice', self.v2, event_id))['participants']
            self.assertEqual([p['id'] for p in people], [a['id']])
        undone = await me.undo_merge('alice', self.v2, merged['merge_id'])
        self.assertEqual(undone['restored_links'], 2)
        self.assertEqual([p['id'] for p in (await ev.get_event('alice', self.v2, only_b))['participants']], [b['id']])
        self.assertEqual(sorted(p['id'] for p in (await ev.get_event('alice', self.v2, both))['participants']),
                         sorted([a['id'], b['id']]))

    # --- the page routes ------------------------------------------------------------------------------------------

    async def test_the_page_routes_share_the_same_code_and_answer_errors_as_http(self):
        from fastapi import HTTPException
        from orion.api import routes
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.v2)):
            made = await routes.memory_event_create(routes.EventBody(
                title='Kadıköy buluşması', date='2026-11-10', participants=['Ayşe'], place='Kadıköy'), request, 'alice')
            event_id = made['event']['id']
            listing = await routes.memory_events_list(request, '2026', '2026', None, 50, 0, 'alice')
            self.assertEqual([e['title'] for e in listing['events']], ['Kadıköy buluşması'])
            self.assertEqual(listing['years'], [{'year': 2026, 'count': 1}])
            changed = await routes.memory_event_update(event_id, routes.EventBody(date='2026-11-11', precision='day'), request,
                                                        'alice')
            self.assertEqual((changed['event']['date'], changed['replaced']), ('2026-11-11', event_id))
            card = await routes.memory_event_card(event_id, request, 'alice')
            self.assertEqual((card['title'], len(card['past_versions']), card['sources'][0]['by_user']),
                             ('Kadıköy buluşması', 1, True))
            for call, code in ((lambda: routes.memory_event_card(event_id, request, 'bob'), 404),
                               (lambda: routes.memory_event_remove(event_id, request, 'bob'), 404),
                               (lambda: routes.memory_event_create(routes.EventBody(title='x'), request, 'alice'), 400),
                               (lambda: routes.memory_events_list(request, '2026-05', '2026-04', None, 50, 0, 'alice'), 400)):
                with self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, code)
            made_album = await routes.memory_album_create(routes.AlbumBody(name='Ofis'), request, 'alice')
            group = changed['event']['group_id']
            await routes.memory_album_items(made_album['id'], routes.MembersBody(add=[group]), request, 'alice')
            self.assertEqual({a['name']: a['count'] for a in (await routes.memory_albums_list(request, 'alice'))['albums']
                              if a['name'] != mc.OLD_NAME}, {'Ofis': 1})
            self.assertEqual({a['name']: a['member'] for a in (await routes.memory_group_albums(group, request, 'alice'))['albums']
                              if a['name'] != mc.OLD_NAME}, {'Ofis': True})
            shown = await routes.list_memory(request, '', 20, 0, 'all', 'all', [], [made_album['id']], '', '', 'alice')
            self.assertEqual(len(shown['groups']), 1)
            dated = await routes.list_memory(request, '', 20, 0, 'all', 'all', [], [], '2026-11', '2026-11', 'alice')
            self.assertEqual(len(dated['groups']), 1)
            self.assertEqual(dated['groups'][0]['event']['title'], 'Kadıköy buluşması')
            for call, code in ((lambda: routes.memory_album_rename(made_album['id'], routes.AlbumBody(name='Ben'), request,
                                                                   'alice'), 400),
                               (lambda: routes.memory_album_remove(made_album['id'], request, 'bob'), 404),
                               (lambda: routes.memory_album_items(made_album['id'], routes.MembersBody(add=[group]), request,
                                                                  'bob'), 404)):
                with self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, code)
            upcoming = await routes.memory_events_upcoming(request, 30, 'alice')
            self.assertEqual(upcoming, {'upcoming': []})
            removed = await routes.memory_event_remove(changed['event']['id'], request, 'alice')
            self.assertIn('trash_id', removed)
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.off)):
            with self.assertRaises(HTTPException) as caught:  # memory_v2 off: nothing here runs.
                await routes.memory_events_list(request, '', '', None, 50, 0, 'alice')
            self.assertEqual(caught.exception.status_code, 400)

    # --- schema ---------------------------------------------------------------------------------------------------

    async def test_schema_five_moves_to_six_without_touching_what_is_there(self):
        fact = await memory_tools.add_memory(memory_tools.MemoryAddInput.model_validate({
            'text': 'Annemin doğum günü 17 Kasım.', 'type': 'fact', 'about': 'Ayşe', 'category': 'date'}), self.context())
        conn = await registry._connect()
        try:
            await conn.execute("drop table orion_mem_event, orion_mem_link")
            await conn.execute("alter table orion_mem_fact drop column recurring")
            await conn.execute("update orion_memory_meta set value = '5' where key = 'schema_version'")
            await memory._migrate(conn)
            self.assertEqual(await conn.fetchval("select value from orion_memory_meta where key = 'schema_version'"), '6')
            self.assertEqual(await conn.fetchval('select count(*) from orion_mem_fact'), 1)
            self.assertEqual(await conn.fetchval('select count(*) from orion_mem_event'), 0)
            self.assertIsNone(await conn.fetchval('select recurring from orion_mem_fact'))
        finally:
            await conn.close()
        self.assertEqual(len((await self.find(**{'in': ['me']}))['results']), 1)
        self.assertTrue(fact['added'])

    # --- users ----------------------------------------------------------------------------------------------------

    async def test_another_user_sees_and_changes_nothing_in_any_new_table(self):
        event_id = await self.mother_day()
        row, group = await self.photo()
        await self.add('x', event=event_id, items=[row])
        album = await al.create_album('alice', self.v2, 'Aile')
        await al.set_members('alice', self.v2, album['id'], add=[group])
        mother = await memory_tools.add_memory(memory_tools.MemoryAddInput.model_validate({
            'text': 'Annemin doğum günü 17 Kasım.', 'type': 'fact', 'about': 'Ayşe', 'category': 'date',
            'repeats': 'yearly', 'date': '11-17'}), self.context())
        self.assertTrue(mother['added'])
        # Bob has his own events and sees none of alice's by any road.
        self.assertEqual((await ev.list_events('bob', self.v2))['events'], [])
        self.assertEqual(await self.counts('bob'), {})
        self.assertEqual(await ev.upcoming('bob', self.v2, days=366, today=TODAY), [])
        bobs = await self.add('Bobun günü', title='Bobun günü', date='2026-11-17', user='bob', chat='b1')
        self.assertNotEqual(bobs['added']['event_id'], event_id)
        self.assertEqual([e['title'] for e in (await ev.list_events('bob', self.v2))['events']], ['Bobun günü'])
        for call in (lambda: ev.get_event('bob', self.v2, event_id), lambda: ev.remove_event('bob', self.v2, event_id),
                     lambda: ev.replace_event('bob', self.v2, event_id, title='x'),
                     lambda: ev.set_items('bob', self.v2, event_id, add=[group]),
                     lambda: ev.set_items('bob', self.v2, bobs['added']['event_id'], add=[group]),  # His event, her photo.
                     lambda: al.rename_album('bob', self.v2, album['id'], 'Bob'),
                     lambda: al.remove_album('bob', self.v2, album['id']),
                     lambda: al.set_members('bob', self.v2, album['id'], add=[group]),
                     lambda: al.restore_album('bob', self.v2, album['id'])):
            with self.assertRaises(LookupError):
                await call()
        refused = await self.add('x', event=event_id, items=[row], user='bob', chat='b1')
        self.assertIn('error', refused)
        self.assertEqual(await sql("select count(*) from orion_mem_link where user_id = 'bob'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_collection_item where user_id = 'bob' and not main"), 0)
        # His searches: only his event, and `when` reaches only his own dated memories.
        found = await self.find(user='bob', when={'from': '2026-11', 'to': '2026-11'})
        self.assertEqual([r['event']['title'] for r in found['results']], ['Bobun günü'])
        self.assertIn('error', await self.find(user='bob', **{'in': ['Aile']}))  # Alice's album is unknown to him.
        # Purging one user's event leaves the other's rows, links and sources.
        removed = await ev.remove_event('bob', self.v2, bobs['added']['event_id'])
        await memory_trash.purge('bob', [removed['trash_id']], self.v2)
        self.assertEqual(await sql("select count(*) from orion_mem_event where user_id = 'alice'"), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_link where user_id = 'alice'"), 4)
        self.assertEqual(await sql("select count(*) from orion_mem_source where user_id = 'alice' and target_type = 'event'"), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_event where user_id = 'bob'"), 0)
