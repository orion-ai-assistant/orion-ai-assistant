"""Memory step 5 (docs/hafiza-tasarimi.md §16): events. Date resolution and precision, add_memory type=event
(sources, entities, photos), the one date filter `when` of the page and of search_memory, versions, the trash and purge of
events, the clean text of an event, the Ben collection (the user's events), yearly recurrences and user separation of every new table. On a throwaway
real PostgreSQL (test_memory_search); embeddings are hand-made vectors (a text with "doğum" is near the query)."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from orion.kernel import (memory, memory_collections as mc, memory_groups, memory_entities as me, memory_events as ev,
                          memory_trash, registry)
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools
import legacy_add

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

    def test_the_hub_does_not_read_relative_words_the_writer_model_gives_the_dates(self):
        # "dün" etc. are metadata only: with no date the event is refused, with dates they are stored as given.
        for words in ('bugün', 'dün', 'geçen cuma', 'geçen hafta'):
            with self.assertRaises(ValueError, msg=words):
                self.resolve(None, said_as=words)
        week = self.resolve('2026-11-09', '2026-11-15', 'approx', said_as='geçen hafta')  # The model resolved it.
        self.assertEqual((week['precision'], week['on'], week['end'], week['said_as']),
                         ('approx', date(2026, 11, 9), date(2026, 11, 15), 'geçen hafta'))
        yesterday = self.resolve('2026-11-17', said_as='dün')
        self.assertEqual((yesterday['on'], yesterday['precision'], yesterday['note']), (date(2026, 11, 17), 'day', None))
        with self.assertRaises(ValueError):  # Only the format and the range are checked.
            self.resolve('1500-01-01')
        with self.assertRaises(ValueError):
            self.resolve('17.11.2026')

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
        args = legacy_add.MemoryAddInput.model_validate({'text': text, 'type': 'event', **kw})
        return await legacy_add.add_memory(args, self.context(user, chat))

    async def find(self, sorgu='doğum günü', user='alice', **kw):
        queries = [{'text': sorgu}] if sorgu else []
        args = memory_tools.MemorySearchInput.model_validate({'queries': queries, **kw})
        return await memory_tools.search_memory(args, self.context(user))

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
        self.assertEqual(vector[0]['text'], 'Annemin doğum günü. Annemin doğum günüydü, pasta yedik. '
                                            'Yer: Kadıköy. Katılanlar: Ayşe.')  # Title, text, place, people (not "Ben"); no date.
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
            'body': 'Annemin doğum günüydü, pasta yedik.', 'said_as': None,
            'with': ['Ayşe', 'Ben'], 'place': 'Kadıköy', 'photos': 0})
        self.assertEqual(len((await self.find())['results']), 1)  # The default search is the whole memory.
        self.assertEqual(len((await self.find(**{'in': ['me']}))['results']), 1)  # Ben: the events the user took part in.

    async def test_dates_come_from_the_writer_and_said_as_is_kept_beside_them(self):
        today = await self.add('Parkta yürüdüm.', title='Parkta yürüyüş', date='2026-11-18', said_as='bugün')
        self.assertEqual((today['added']['date'], today['added']['precision'], today['added']['said_as']),
                         ('2026-11-18', 'day', 'bugün'))
        summer = await self.add('Geçen yaz Bodrum', title='Bodrum tatili', date='2025-07', precision='approx',
                                said_as='geçen yaz')
        self.assertEqual((summer['added']['date'], summer['added']['precision']), ('2025-07-01 to 2025-07-31', 'approx'))
        row = (await sql("select occurred_on, occurred_end, said_as from orion_mem_event where title = 'Bodrum tatili'",
                         fetch=True))[0]
        self.assertEqual((row['occurred_end'], row['said_as']), (date(2025, 7, 31), 'geçen yaz'))
        undated = await self.add('Bir şey oldu.', said_as='dün')  # The Hub does not guess from the words.
        self.assertIn('error', undated)
        self.assertEqual(await sql('select count(*) from orion_mem_event'), 2)

    async def test_an_event_reads_cleanly_the_date_once_and_no_double_period(self):
        when = ev.resolve_date('2026-11-17')
        self.assertEqual(ev.embedded_text('Parkta kahve içtik.', None, when, ['Mehmet'], 'park'),
                         'Parkta kahve içtik. Yer: park. Katılanlar: Mehmet.')  # No date, no time words, no "Ben".
        self.assertEqual(ev.embedded_text('Gezi', 'Güzeldi...', ev.resolve_date('2026-06-05', '2026-06-08', 'approx'), [], None),
                         'Gezi. Güzeldi.')
        self.assertEqual(ev.range_text(date(2026, 11, 1), date(2026, 11, 30), 'month'), 'Kasım 2026')
        self.assertEqual(ev.range_text(date(2024, 1, 1), date(2024, 12, 31), 'year'), '2024')
        said = "Geçen parkta Mehmet'le karşılaştık ve kahve içtik."
        done = await self.add(said, title=said, date='2026-11', precision='approx', participants=['Mehmet', 'me'],
                              place='park')
        text = await sql("select text from orion_memory where source_kind = 'event'")
        self.assertEqual(text, "Geçen parkta Mehmet'le karşılaştık ve kahve içtik. Yer: park. Katılanlar: Mehmet.")
        self.assertEqual(done['added']['title'], "Geçen parkta Mehmet'le karşılaştık ve kahve içtik")

    async def test_albums_projects_and_the_old_bucket_are_gone(self):
        self.assertNotIn('collection', legacy_add.MemoryAddInput.model_json_schema()['properties'])
        conn = await registry._connect()
        try:
            await mc.ensure_user(conn, 'alice')
            names = [c['name'] for c in await mc.list_collections(conn, 'alice')]
        finally:
            await conn.close()
        self.assertEqual(names, ['Ben', 'Yaşadıklarım', 'Belgeler'])
        self.assertIn('error', await self.find('', **{'in': ['Eski hafıza']}))

    async def test_schema_nine_drops_albums_projects_and_the_ben_memberships_but_no_memory(self):
        note = await self.note('kedi')
        await legacy_add.add_memory(legacy_add.MemoryAddInput.model_validate(
            {'type': 'fact', 'text': 'Kahveyi sever.', 'about': 'me'}), self.context())
        await sql("insert into orion_mem_collection (user_id, id, kind, name) values ('alice', 'a_1', 'album', 'Eski'), "
                  "('alice', 'p_1', 'project', 'Eski hafıza')")
        await sql("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) values "
                  "('alice', 'a_1', $1, false), ('alice', 'p_1', $1, true), ('alice', 'ben', 'x', true)", note)
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory_meta set value = '8' where key = 'schema_version'")
            await memory._migrate(conn)
        finally:
            await conn.close()
        self.assertEqual(await sql("select count(*) from orion_mem_collection where kind <> 'builtin'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_collection_item where collection_id <> 'yasadiklarim' "
                                   "and collection_id <> 'belgeler'"), 0)
        self.assertEqual(await sql('select count(*) from orion_memory'), 2)  # Never a memory.
        self.assertEqual(len((await self.find('', **{'in': ['me']}))['results']), 1)

    async def test_the_users_time_words_are_metadata_only_never_in_the_text(self):
        done = await self.add('Dün Mehmet ile parkta kahve içtik, dün güzeldi.', title='Dün Mehmet ile kahve', date='2026-11-17',
                              said_as='dün', participants=['Mehmet', 'me'], place='park')
        self.assertEqual(done['added']['said_as'], 'dün')
        row = (await sql('select title, body from orion_mem_event', fetch=True))[0]
        self.assertNotIn('ün ', ' ' + row['title'].lower())
        self.assertNotIn('dün', (row['title'] + ' ' + (row['body'] or '')).lower())
        text = await sql("select text from orion_memory where source_kind = 'event'")
        self.assertNotIn('dün', text.lower())
        self.assertNotIn('2026', text)  # No date in the embedded text.
        self.assertNotIn('Ben', text)  # The user is not listed among the participants.
        self.assertIn('Mehmet', text)
        self.assertIn('park', text)
        # Schema 10 cleans what is stored from before.
        await sql("update orion_mem_event set title = 'Dün kahve içtik', body = null, said_as = 'dün'")
        await sql("update orion_memory set text = '17 Kasım 2026 Salı (dün): Dün kahve içtik. Katılanlar: Ben.' "
                  "where source_kind = 'event'")
        conn = await registry._connect()
        try:
            await ev.clean_existing(conn)
        finally:
            await conn.close()
        self.assertEqual(await sql('select title from orion_mem_event'), 'Kahve içtik')
        self.assertEqual(await sql("select text from orion_memory where source_kind = 'event'"),
                         'Kahve içtik. Yer: park. Katılanlar: Mehmet.')  # The links stay; the user is not listed.

    async def test_schema_ten_drops_the_chats_index_and_keeps_the_rest(self):
        note = await self.note('kedi')
        await sql("insert into orion_mem_collection (user_id, id, kind, name) values ('alice', 'sohbetler', 'builtin', 'Sohbetler') "
                  "on conflict do nothing")
        await sql("insert into orion_memory (user_id, text, vector_space, embedding, source_kind, group_id) "
                  "select 'alice', 'Kullanıcı: merhaba', vector_space, embedding, 'chat_index', 'wchat' from orion_memory limit 1")
        await sql("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) values "
                  "('alice', 'sohbetler', 'wchat', true)")
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory_meta set value = '9' where key = 'schema_version'")
            await memory._migrate(conn)
        finally:
            await conn.close()
        self.assertEqual(await sql("select count(*) from orion_memory where source_kind = 'chat_index'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_collection where id = 'sohbetler'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_collection_item where collection_id = 'sohbetler'"), 0)
        self.assertIsNone(await sql("select to_regclass('orion_mem_chat_index_jobs')"))
        self.assertEqual(await sql('select count(*) from orion_memory where group_id = $1', note), 1)  # Never a real memory.

    # --- recurrences ----------------------------------------------------------------------------------------------

    async def test_yearly_facts_list_what_is_coming_and_survive_a_new_version(self):
        async def fact(text, **kw):
            args = legacy_add.MemoryAddInput.model_validate({'text': text, 'type': 'fact', **kw})
            return await legacy_add.add_memory(args, self.context())
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
            group = changed['event']['group_id']
            dated = await routes.list_memory(request, '', 20, 0, 'all', 'all', [], [], '2026-11', '2026-11', current_user='alice')
            self.assertEqual(len(dated['groups']), 1)
            self.assertEqual(dated['groups'][0]['event']['title'], 'Kadıköy buluşması')
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
        fact = await legacy_add.add_memory(legacy_add.MemoryAddInput.model_validate({
            'text': 'Annemin doğum günü 17 Kasım.', 'type': 'fact', 'about': 'Ayşe', 'relation': 'anne',
            'category': 'date'}), self.context())
        conn = await registry._connect()
        try:
            await conn.execute("drop table orion_mem_event, orion_mem_link")
            await conn.execute("alter table orion_mem_fact drop column recurring")
            await conn.execute("update orion_memory_meta set value = '5' where key = 'schema_version'")
            await memory._migrate(conn)
            self.assertEqual(await conn.fetchval("select value from orion_memory_meta where key = 'schema_version'"),
                             str(memory.SCHEMA_VERSION))
            self.assertEqual(await conn.fetchval('select count(*) from orion_mem_fact'), 1)
            self.assertEqual(await conn.fetchval('select count(*) from orion_mem_event'), 0)
            self.assertIsNone(await conn.fetchval('select recurring from orion_mem_fact'))
        finally:
            await conn.close()
        self.assertEqual(len((await self.find())['results']), 1)  # (A close person's fact is not in Ben, so: the whole memory.)
        self.assertTrue(fact['added'])

    # --- users ----------------------------------------------------------------------------------------------------

    async def test_another_user_sees_and_changes_nothing_in_any_new_table(self):
        event_id = await self.mother_day()
        row, group = await self.photo()
        await self.add('x', event=event_id, items=[row])
        mother = await legacy_add.add_memory(legacy_add.MemoryAddInput.model_validate({
            'text': 'Annemin doğum günü 17 Kasım.', 'type': 'fact', 'about': 'Ayşe', 'category': 'date',
            'repeats': 'yearly', 'date': '11-17'}), self.context())
        self.assertTrue(mother['added'])
        # Bob has his own events and sees none of alice's by any road.
        self.assertEqual((await ev.list_events('bob', self.v2))['events'], [])
        self.assertEqual(await ev.upcoming('bob', self.v2, days=366, today=TODAY), [])
        bobs = await self.add('Bobun günü', title='Bobun günü', date='2026-11-17', user='bob', chat='b1')
        self.assertNotEqual(bobs['added']['event_id'], event_id)
        self.assertEqual([e['title'] for e in (await ev.list_events('bob', self.v2))['events']], ['Bobun günü'])
        for call in (lambda: ev.get_event('bob', self.v2, event_id), lambda: ev.remove_event('bob', self.v2, event_id),
                     lambda: ev.replace_event('bob', self.v2, event_id, title='x'),
                     lambda: ev.set_items('bob', self.v2, event_id, add=[group]),
                     lambda: ev.set_items('bob', self.v2, bobs['added']['event_id'], add=[group])):  # His event, her photo.
            with self.assertRaises(LookupError):
                await call()
        refused = await self.add('x', event=event_id, items=[row], user='bob', chat='b1')
        self.assertIn('error', refused)
        self.assertEqual(await sql("select count(*) from orion_mem_link where user_id = 'bob'"), 0)
        # His searches: only his event, and `when` reaches only his own dated memories.
        found = await self.find(user='bob', when={'from': '2026-11', 'to': '2026-11'})
        self.assertEqual([r['event']['title'] for r in found['results']], ['Bobun günü'])
        # Purging one user's event leaves the other's rows, links and sources.
        removed = await ev.remove_event('bob', self.v2, bobs['added']['event_id'])
        await memory_trash.purge('bob', [removed['trash_id']], self.v2)
        self.assertEqual(await sql("select count(*) from orion_mem_event where user_id = 'alice'"), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_link where user_id = 'alice'"), 4)
        self.assertEqual(await sql("select count(*) from orion_mem_source where user_id = 'alice' and target_type = 'event'"), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_event where user_id = 'bob'"), 0)
