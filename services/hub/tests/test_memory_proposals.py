"""Memory step 6 (docs/hafiza-tasarimi.md §16): proposal cards for every kind of change that waits for the user (the delete
the model asks for included: one card, one answer route), "Son eklenenler" (what the assistant wrote itself) and Geri al,
and user separation of everything read here. On a throwaway real PostgreSQL (test_memory_search); embeddings are hand-made."""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.kernel import memory, memory_entities as me, memory_events as ev, memory_pending, memory_proposals as mp
from orion.kernel import memory_recent, memory_trash, registry
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


class ProposalTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_proposal, orion_mem_fact, orion_mem_entity_merge, orion_mem_entity, orion_mem_event, '
                  'orion_mem_link, orion_mem_source, orion_mem_trash cascade')
        await sql("insert into orion_chats(id, user_id, title) values ('c1', 'alice', 'Aile sohbeti'), "
                  "('b1', 'bob', 'Bob sohbeti') on conflict (id) do update set title = excluded.title")
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_trash_days=30)
        self.off = SimpleNamespace(router_model_group='chat', memory_v2=False, memory_trash_days=30)
        self.settings = self.v2
        self.embedded = []

        async def embed(text, settings, kind, local):
            self.embedded.append(text)
            return [vec(0.9, 5) if 'doğum' in text.lower() else vec(0.1, 6 + len(self.embedded) % 700)]

        async def target(settings, space=None):
            return settings, SPACE, False
        for module, name, value in ((memory, '_embed', embed), (memory, 'embedding_target', target)):
            guard = patch.object(module, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        from orion.kernel import memory_backup
        guard = patch.object(memory_backup, 'backup', AsyncMock(return_value=None))
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch('orion.kernel.config.get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.settings))
        guard.start()
        self.addCleanup(guard.stop)

    def context(self, user='alice', chat='c1'):
        return ToolContext(user, chat, 't', None)

    async def tool(self, text, user='alice', chat='c1', **kw):
        args = legacy_add.MemoryAddInput.model_validate({'text': text, **kw})
        return await legacy_add.add_memory(args, self.context(user, chat))

    async def note_by_model(self, text, user='alice'):
        await self.tool(text, user=user, title='Not')
        return await sql("select coalesce(group_id, 'r' || id::text) from orion_memory where user_id = $1 and text = $2",
                         user, text)

    # --- the delete card ---------------------------------------------------------------------------------------------

    async def test_delete_memory_opens_a_trash_card_and_nothing_moves_until_the_user_presses_sil(self):
        group = await self.note_by_model('Pizza severim')
        row = await sql('select id from orion_memory where group_id = $1', group)
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        self.assertEqual(asked['status'], 'awaiting_user_confirmation')
        self.assertNotIn('confirmation_id', asked)  # One card in the chat, not two.
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)
        again = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        self.assertEqual(again['proposal_id'], asked['proposal_id'])  # The same open question: one card.
        card = (await mp.proposals('alice', self.v2))[0]
        self.assertEqual((card['action'], card['payload']['ids'], card['payload']['preview'][0]['label']),
                         ('trash', [row], 'Pizza severim'))
        waiting = await memory_tools.delete_memory(
            memory_tools.MemoryDeleteInput(confirmation_id=str(asked['proposal_id'])), self.context())
        self.assertEqual(waiting['state'], 'pending')
        done = (await mp.decide('alice', self.v2, accept=[asked['proposal_id']]))[0]
        self.assertEqual((done['state'], done['deleted']), ('accepted', [row]))
        self.assertEqual(await sql('select count(*) from orion_mem_trash where memory_id = $1', row), 1)
        shown = (await mp.proposals('alice', self.v2, None, [asked['proposal_id']]))[0]
        self.assertEqual((shown['state'], shown['trash'], shown['payload'].get('preview')), ('accepted', 'in', None))
        self.assertEqual(shown['payload']['result']['trash_id'], done['trash_id'])  # Geri al works from the card.
        await memory_trash.restore('alice', [done['trash_id']])
        self.assertEqual((await mp.proposals('alice', self.v2, None, [asked['proposal_id']]))[0]['trash'], 'restored')
        outcome = await memory_tools.delete_memory(
            memory_tools.MemoryDeleteInput(confirmation_id=str(asked['proposal_id'])), self.context())
        self.assertEqual((outcome['state'], outcome['deleted_ids']), ('accepted', [row]))

    async def test_declining_keeps_everything_and_an_answered_card_is_not_answered_twice(self):
        group = await self.note_by_model('Pizza severim')
        row = await sql('select id from orion_memory where group_id = $1', group)
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        self.assertEqual((await mp.decide('alice', self.v2, reject=[asked['proposal_id']]))[0]['state'], 'rejected')
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)
        self.assertEqual((await mp.decide('alice', self.v2, accept=[asked['proposal_id']]))[0]['state'], 'already')
        card = (await mp.proposals('alice', self.v2, 'rejected'))[0]
        self.assertNotIn('preview', card['payload'])  # What the card showed is not kept once decided.
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)

    async def test_whole_memory_and_gone_memories(self):
        group = await self.note_by_model('Pizza severim')
        row = await sql('select id from orion_memory where group_id = $1', group)
        whole = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row], whole_memory=True), self.context())
        done = (await mp.decide('alice', self.v2, accept=[whole['proposal_id']]))[0]
        self.assertEqual(done['state'], 'accepted')
        other = await self.note_by_model('Kahve severim')
        other_row = await sql('select id from orion_memory where group_id = $1', other)
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[other_row]), self.context())
        await memory_trash.trash_items('alice', [other_row])  # The user removed it meanwhile.
        failed = (await mp.decide('alice', self.v2, accept=[asked['proposal_id']]))[0]
        self.assertEqual(failed['state'], 'pending')  # Nothing to move: the card says why and stays.
        self.assertIn('error', failed)
        missing = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[999999]), self.context())
        self.assertIn('note', missing)

    async def test_a_user_without_memory_v2_keeps_todays_confirmation(self):
        self.settings = self.off
        await self.tool('Pizza severim')
        row = await sql("select id from orion_memory where text = 'Pizza severim'")
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        self.assertIn('confirmation_id', asked)
        self.assertNotIn('proposal_id', asked)
        self.assertEqual(await sql('select count(*) from orion_mem_proposal'), 0)
        self.assertEqual((await memory_pending.status('alice', asked['confirmation_id']))['state'], 'pending')

    # --- one card, every action --------------------------------------------------------------------------------------

    async def test_every_action_is_in_the_same_list_and_answered_by_one_route(self):
        fact = (await self.tool('Doğum günü 17 Kasım.', type='fact', about='Ayşe', relation='anne',
                                category='date'))['added']['fact_id']
        replace = await self.tool('Doğum günü 18 Kasım.', type='fact', about='#' + str(
            await sql("select id from orion_mem_entity where name = 'Ayşe'")), category='date', replaces=fact)
        locked = await self.tool('Şeker hastası.', type='fact', about='Ayşe', category='health')
        group = await self.note_by_model('Pizza severim')
        row = await sql('select id from orion_memory where group_id = $1', group)
        trash = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        ids = [replace['proposal_id'], locked['proposal_id'], trash['proposal_id']]
        cards = await mp.proposals('alice', self.v2)
        self.assertEqual(sorted(c['action'] for c in cards), ['add', 'supersede', 'trash'])
        self.assertEqual(await mp.count_pending('alice', self.v2), 3)
        answered = await mp.decide('alice', self.v2, accept=[ids[0], ids[2]], reject=[ids[1]])
        self.assertEqual(sorted(a['state'] for a in answered), ['accepted', 'accepted', 'rejected'])
        self.assertEqual(await mp.count_pending('alice', self.v2), 0)
        self.assertEqual(await sql("select statement from orion_mem_fact where superseded_by is null and "
                                   "lineage = $1", fact), 'Doğum günü 18 Kasım.')

    async def test_a_card_answered_in_two_places_at_once_is_carried_out_once(self):
        import asyncio
        group = await self.note_by_model('Pizza severim')
        row = await sql('select id from orion_memory where group_id = $1', group)
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        answers = await asyncio.gather(*[mp.decide('alice', self.v2, accept=[asked['proposal_id']]) for _ in range(3)])
        self.assertEqual(sorted(a[0]['state'] for a in answers), ['accepted', 'already', 'already'])
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 1)

    # --- recently added ----------------------------------------------------------------------------------------------

    async def written(self):
        """A model fact, a model event and a model note for alice, a user's own fact and note for her too."""
        fact = (await self.tool('Doğum günü 17 Kasım.', type='fact', about='Ayşe', relation='anne',
                                category='date'))['added']['fact_id']
        event = (await self.tool('Doğum günü kutlandı.', type='event', title='Annemin doğum günü', date='2026-11-17',
                                 precision='day'))['added']['event_id']
        note = await self.note_by_model('Pizza severim')
        mine = await me.write_by_user('alice', self.v2, 'Ayşe', 'diğer', 'Çay sever.', kind='person')
        await memory.add_note_to('alice', 'Kendi notum', self.v2, 'manual')
        return fact, event, note, mine['fact']['id']

    async def test_recent_lists_what_the_assistant_wrote_with_its_source_and_never_the_users_own(self):
        fact, event, note, mine = await self.written()
        listing = await memory_recent.recent('alice', self.v2)
        by_type = {i['type']: i for i in listing['items']}
        self.assertEqual(sorted(by_type), ['event', 'fact', 'note'])
        self.assertEqual(len(listing['items']), 3)  # The user's own fact and note are not here.
        self.assertEqual((by_type['fact']['id'], by_type['fact']['title'], by_type['fact']['text']),
                         (fact, 'Anne (Ayşe)', 'Doğum günü 17 Kasım.'))
        self.assertEqual(by_type['fact']['chat'], {'id': 'c1', 'title': 'Aile sohbeti'})
        self.assertEqual((by_type['event']['id'], by_type['event']['date']), (event, '2026-11-17'))
        self.assertEqual((by_type['note']['id'], by_type['note']['text']), (note, 'Pizza severim'))
        self.assertEqual([i['added_at'] for i in listing['items']], sorted((i['added_at'] for i in listing['items']),
                                                                           reverse=True))
        self.assertEqual(await memory_recent.count('alice', self.v2), 3)
        self.assertEqual(await memory_recent.review('alice', self.v2), {'pending': 0, 'recent': 3})
        page = await memory_recent.recent('alice', self.v2, limit=2)
        self.assertEqual((len(page['items']), page['more']), (2, True))
        rest = await memory_recent.recent('alice', self.v2, limit=2, offset=2)
        self.assertEqual((len(rest['items']), rest['more']), (1, False))
        old = await memory_recent.recent('alice', self.v2, days=1)
        self.assertEqual(len(old['items']), 3)
        await sql("update orion_mem_fact set recorded_at = now() - interval '40 days' where id = $1", fact)
        self.assertEqual(len((await memory_recent.recent('alice', self.v2))['items']), 2)

    async def test_a_new_version_accepted_on_a_card_and_a_locked_fact_are_handled(self):
        fact = (await self.tool('Doğum günü 17 Kasım.', type='fact', about='Ayşe', category='date'))['added']['fact_id']
        asked = await self.tool('Doğum günü 18 Kasım.', type='fact', about='Ayşe', category='date', replaces=fact)
        await mp.decide('alice', self.v2, accept=[asked['proposal_id']])
        keys = [i['key'] for i in (await memory_recent.recent('alice', self.v2))['items']]
        # The head is a version the user accepted and the first version is closed: nothing automatic is left to take back.
        self.assertEqual(keys, [])
        locked = await self.tool('Şeker hastası.', type='fact', about='Ayşe', category='health')
        await mp.decide('alice', self.v2, accept=[locked['proposal_id']])
        item = (await memory_recent.recent('alice', self.v2))['items'][0]
        self.assertEqual((item['locked'], item['text']), (True, None))  # A locked fact is listed without its words.
        undone = await memory_recent.undo('alice', self.v2, 'fact', str(item['id']))
        self.assertEqual(undone, {'restore': {'fact': item['id']}})
        self.assertEqual((await memory_recent.recent('alice', self.v2))['items'], [])
        await me.restore_fact('alice', self.v2, item['id'])
        self.assertEqual(len((await memory_recent.recent('alice', self.v2))['items']), 1)

    async def test_geri_al_removes_to_the_trash_and_the_trash_brings_it_back(self):
        fact, event, note, mine = await self.written()
        for kind, ident in (('fact', fact), ('event', event), ('note', note)):
            undone = await memory_recent.undo('alice', self.v2, kind, str(ident))
            self.assertTrue(undone['trash_id'])
            self.assertNotIn(f'{kind}:{ident}', [i['key'] for i in (await memory_recent.recent('alice', self.v2))['items']])
            with self.assertRaises(LookupError):  # Already gone.
                await memory_recent.undo('alice', self.v2, kind, str(ident))
            await memory_trash.restore('alice', [undone['trash_id']])
            self.assertIn(f'{kind}:{ident}', [i['key'] for i in (await memory_recent.recent('alice', self.v2))['items']])
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)
        with self.assertRaises(LookupError):  # The user's own writing is theirs to remove on the page, not an addition.
            await memory_recent.undo('alice', self.v2, 'fact', str(mine))
        with self.assertRaises(ValueError):
            await memory_recent.undo('alice', self.v2, 'planet', '1')

    async def test_a_removed_addition_is_not_listed_and_off_means_off(self):
        fact, event, note, mine = await self.written()
        await me.remove_fact('alice', self.v2, fact)
        await memory_trash.trash_groups('alice', [note])
        self.assertEqual([i['type'] for i in (await memory_recent.recent('alice', self.v2))['items']], ['event'])
        for call in (lambda: memory_recent.recent('alice', self.off), lambda: memory_recent.review('alice', self.off),
                     lambda: mp.proposals('alice', self.off), lambda: mp.count_pending('alice', self.off)):
            with self.assertRaises(ValueError):
                await call()

    # --- HTTP ----------------------------------------------------------------------------------------------------------

    async def test_the_page_routes_answer_as_http(self):
        from fastapi import HTTPException
        from orion.api import routes
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        fact, event, note, mine = await self.written()
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.v2)):
            self.assertEqual(await routes.memory_review(request, 'alice'), {'pending': 0, 'recent': 3})
            listing = await routes.memory_recent_list(request, 30, 50, 0, 'alice')
            self.assertEqual(len(listing['items']), 3)
            undone = await routes.memory_recent_undo(routes.RecentUndo(type='fact', id=str(fact)), request, 'alice')
            self.assertTrue(undone['trash_id'])
            for body, code in ((routes.RecentUndo(type='fact', id=str(fact)), 404),
                               (routes.RecentUndo(type='fact', id=str(mine)), 404),
                               (routes.RecentUndo(type='planet', id='1'), 400),
                               (routes.RecentUndo(type='fact', id=str(fact)), 404)):
                with self.assertRaises(HTTPException) as caught:
                    await routes.memory_recent_undo(body, request, 'alice')
                self.assertEqual(caught.exception.status_code, code)
            with self.assertRaises(HTTPException) as caught:  # Bob cannot undo alice's note.
                await routes.memory_recent_undo(routes.RecentUndo(type='note', id=note), request, 'bob')
            self.assertEqual(caught.exception.status_code, 404)
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.off)):
            for call in (lambda: routes.memory_review(request, 'alice'),
                         lambda: routes.memory_recent_list(request, 30, 50, 0, 'alice')):
                with self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, 400)

    # --- user separation -----------------------------------------------------------------------------------------------

    async def test_nothing_of_another_user_is_listed_answered_or_undone(self):
        fact, event, note, mine = await self.written()
        bob_fact = (await self.tool('Doğum günü 1 Mart.', type='fact', about='Ayşe', category='date', user='bob',
                                    chat='b1'))['added']['fact_id']
        bob_note = await self.note_by_model('Bob notu', user='bob')
        row = await sql('select id from orion_memory where group_id = $1', note)
        asked = await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]), self.context())
        self.assertEqual({i['key'] for i in (await memory_recent.recent('bob', self.v2))['items']},
                         {f'fact:{bob_fact}', f'note:{bob_note}'})
        self.assertEqual(await mp.proposals('bob', self.v2), [])
        self.assertEqual(await mp.count_pending('bob', self.v2), 0)
        self.assertEqual((await mp.decide('bob', self.v2, accept=[asked['proposal_id']]))[0]['state'], 'unknown')
        self.assertEqual(await sql('select count(*) from orion_mem_trash'), 0)
        # Bob asking to delete alice's row: no such memory for him; and her card names only her rows.
        self.assertIn('note', await memory_tools.delete_memory(memory_tools.MemoryDeleteInput(ids=[row]),
                                                               self.context('bob', 'b1')))
        self.assertEqual(await sql("select count(*) from orion_mem_proposal where user_id = 'bob'"), 0)
        for kind, ident in (('fact', fact), ('event', event), ('note', note)):
            with self.assertRaises(LookupError):
                await memory_recent.undo('bob', self.v2, kind, str(ident))
        self.assertEqual(await memory_recent.count('alice', self.v2), 3)
        self.assertEqual(len(await mp.proposals('alice', self.v2)), 1)

    # --- cost ----------------------------------------------------------------------------------------------------------

    async def test_the_list_and_the_badges_are_cheap(self):
        await self.written()
        started = time.perf_counter()
        for _ in range(5):
            await memory_recent.review('alice', self.v2)
        self.assertLess((time.perf_counter() - started) / 5, 0.5)
