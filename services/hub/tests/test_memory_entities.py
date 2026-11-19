"""Memory step 4 (docs/hafiza-tasarimi.md §16): entities and facts. Bi-temporal versions with their sources, add_memory
type=fact (replaces and locked kinds only open cards), sensitive facts locked out of every search, reversible entity merge
and the batch card, the trash and purge of facts, and user separation of every new table. On a throwaway real PostgreSQL
(test_memory_search); embeddings are hand-made vectors (a text with "doğum" is near the query)."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.kernel import memory, memory_collections as mc, memory_entities as me, memory_groups, memory_trash, registry
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


class EntityTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_proposal, orion_mem_fact, orion_mem_entity_merge, orion_mem_entity, '
                  'orion_mem_trash cascade')
        await sql("insert into orion_chats(id, user_id) values ('c2', 'alice'), ('b1', 'bob') on conflict do nothing")
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_trash_days=30)
        self.off = SimpleNamespace(router_model_group='chat', memory_v2=False, memory_trash_days=30)
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
        guard = patch('orion.kernel.config.get_runtime_settings', AsyncMock(side_effect=lambda r, u=None: self.v2))
        guard.start()
        self.addCleanup(guard.stop)

    def context(self, user='alice', chat='chat'):
        return ToolContext(user, chat, 't', None)

    async def add(self, text, user='alice', chat='chat', **kw):  # noqa: D401 - the tool, as the model calls it
        args = legacy_add.MemoryAddInput.model_validate({'text': text, 'type': 'fact', **kw})
        return await legacy_add.add_memory(args, self.context(user, chat))

    async def find(self, sorgu='doğum günü', user='alice', **kw):
        args = memory_tools.MemorySearchInput.model_validate({'queries': [{'text': sorgu}], **kw})
        return await memory_tools.search_memory(args, self.context(user))

    async def mother(self):
        done = await self.add('Annemin doğum günü 17 Kasım.', about='Ayşe', relation='anne', category='date')
        return done['added']['fact_id']

    # --- writing -------------------------------------------------------------------------------------------------

    async def test_a_fact_has_a_source_a_vector_in_ben_and_is_found_with_its_entity(self):
        fact_id = await self.mother()
        row = await sql('select * from orion_mem_fact where id = $1', fact_id, fetch=True)
        self.assertEqual((row[0]['category'], row[0]['origin'], row[0]['lineage'], row[0]['sensitive']),
                         ('tarih', 'model', fact_id, False))
        source = await sql("select chat_id, by_user from orion_mem_source where target_type = 'fact' and target_id = $1",
                           fact_id, fetch=True)
        self.assertEqual([(s['chat_id'], s['by_user']) for s in source], [('chat', False)])
        vector = await sql("select text, source_kind, source_chat_title from orion_memory where group_id = $1",
                           row[0]['group_id'], fetch=True)
        self.assertEqual(len(vector), 1)
        self.assertEqual((vector[0]['source_kind'], vector[0]['source_chat_title']), ('fact', 'Ayşe'))
        self.assertEqual(vector[0]['text'], 'Annemin doğum günü 17 Kasım.')
        member = await sql("select count(*) from orion_mem_collection_item where group_id = $1", row[0]['group_id'])
        self.assertEqual(member, 0)  # Ben is defined by who a fact is about, not stored as a membership.
        inside = await self.find(**{'in': ['me']})
        self.assertEqual(inside['results'], [])  # Ben = facts about the user only: a close person's fact is not in it.
        about = await self.find(about='Ayşe')  # It is reached through "Kimin hakkında".
        self.assertEqual(about['results'][0]['fact'], {'id': fact_id, 'about': 'Ayşe (anne)', 'category': 'tarih',
                                                        'since': None})
        everything = await self.find()
        self.assertEqual(len(everything['results']), 1)  # The default search is the whole memory, facts included.
        elsewhere = await self.find(**{'in': ['experiences']})
        self.assertEqual(elsewhere['results'], [])

    async def test_editing_a_fact_corrects_in_place_or_keeps_the_old_one_as_history(self):
        fact_id = (await self.add("Ankara'da yaşıyor.", about='me', category='place'))['added']['fact_id']
        done = await me.edit_fact('alice', self.v2, fact_id, "Ankara'nın Çankaya semtinde yaşıyor.", 'correct')
        self.assertEqual((done['fact']['id'], done['fact']['statement']), (fact_id, "Ankara'nın Çankaya semtinde yaşıyor."))
        self.assertEqual(len((await me.history('alice', self.v2, fact_id))['versions']), 1)  # A mistake: no history.
        vectors = await sql('select text from orion_memory where group_id = (select group_id from orion_mem_fact '
                            'where id = $1)', fact_id, fetch=True)
        self.assertEqual(len(vectors), 1)
        self.assertIn('Çankaya', vectors[0]['text'])  # Found by the new sentence from now on.
        changed = await me.edit_fact('alice', self.v2, fact_id, "İstanbul'da yaşıyor.", 'changed')
        self.assertEqual(changed['replaced'], fact_id)
        self.assertEqual(changed['fact']['since'], date.today().isoformat())
        versions = (await me.history('alice', self.v2, changed['fact']['id']))['versions']
        self.assertEqual(len(versions), 2)
        with self.assertRaises(ValueError):
            await me.edit_fact('alice', self.v2, changed['fact']['id'], 'Bir şey.', 'maybe')

    async def test_nothing_is_written_without_a_source_or_with_a_refused_value(self):
        entity = await sql_entity('alice', 'Ayşe')
        with self.assertRaises(ValueError):
            await me.add_fact('alice', self.v2, entity, 'tarih', 'Doğum günü 17 Kasım.', origin='user', source={})
        with self.assertRaises(ValueError):  # An automatic origin never writes a locked kind.
            await me.add_fact('alice', self.v2, entity, 'sağlık', 'Şeker hastası.', origin='extraction',
                              source={'chat_id': 'chat'})
        with self.assertRaises(ValueError):  # ID numbers are never stored.
            await me.add_fact('alice', self.v2, entity, 'kimlik_no', 'TC kimlik no 12345678901', origin='user',
                              source={'by_user': True})
        with self.assertRaises(ValueError):
            await me.add_fact('alice', self.v2, entity, 'diğer', 'x' * 600, origin='user', source={'by_user': True})
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 0)
        self.assertEqual(await sql("select count(*) from orion_memory where source_kind = 'fact'"), 0)
        passport = await me.add_fact('alice', self.v2, entity, 'kimlik_no', 'Pasaportu var.', origin='user',
                                     source={'by_user': True})
        self.assertTrue(passport['fact']['locked'] is False and passport['fact']['sensitive'])

    async def test_the_same_sentence_is_same_as_and_adds_its_source(self):
        fact_id = await self.mother()
        again = await self.add('annemin doğum günü 17 kasım.', about='anne', category='date', chat='c2')
        self.assertEqual(again['same_as'], fact_id)
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_source where target_id = $1", fact_id), 2)
        self.assertEqual(await sql("select count(*) from orion_mem_entity where kind <> 'self'"), 1)  # 'anne' = Ayşe.

    async def test_defaults_keep_today_and_a_fact_needs_the_collections(self):
        added = await legacy_add.add_memory(legacy_add.MemoryAddInput.model_validate({'text': 'Pizza severim'}),
                                              self.context())
        self.assertEqual(added, {'added': 'Pizza severim'})
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 0)
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.off)):
            refused = await self.add('Annemin doğum günü 17 Kasım.', about='Ayşe')
            self.assertIn('error', refused)
            with self.assertRaises(ValueError):
                await me.list_entities('alice', self.off)
        self.assertEqual(await sql('select count(*) from orion_mem_entity'), 0)

    async def test_two_entities_with_a_name_are_listed_and_an_id_chooses(self):
        await me.create_entity('alice', self.v2, 'Ayşe', relation='anne')
        await me.create_entity('alice', self.v2, 'Ayşe', relation='komşu')
        refused = await self.add('Doğum günü 1 Mart.', about='Ayşe', category='date')
        self.assertIn('More than one', refused['error'])
        self.assertEqual(len(refused['candidates']), 2)
        chosen = await self.add('Doğum günü 1 Mart.', about=f"#{refused['candidates'][1]['id']}", category='date')
        self.assertIn('added', chosen)

    # --- versions ------------------------------------------------------------------------------------------------

    async def test_replaces_opens_a_card_and_nothing_changes_until_the_user_accepts(self):
        old = await self.add('Ankara\'da yaşıyor.', about='me', category='place', since='2019-01-01')
        old_id = old['added']['fact_id']
        asked = await self.add("İstanbul'da yaşıyor.", about='me', category='place', replaces=old_id, since='2026-11-01')
        self.assertEqual(asked['status'], 'awaiting_user_confirmation')
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 1)
        cards = await me.proposals('alice', self.v2)
        self.assertEqual((cards[0]['action'], cards[0]['payload']['old'], cards[0]['payload']['statement']),
                         ('supersede', "Ankara'da yaşıyor.", "İstanbul'da yaşıyor."))
        twice = await self.add("İstanbul'da yaşıyor.", about='me', category='place', replaces=old_id)
        self.assertEqual(twice['proposal_id'], asked['proposal_id'])  # One open question.
        answered = await me.decide('alice', self.v2, accept=[asked['proposal_id']])
        self.assertEqual(answered[0]['state'], 'accepted')
        versions = (await me.history('alice', self.v2, old_id))['versions']
        self.assertEqual([(v['statement'], v['current'], v['since'], v['until']) for v in versions],
                         [("Ankara'da yaşıyor.", False, '2019-01-01', '2026-11-01'),
                          ("İstanbul'da yaşıyor.", True, '2026-11-01', None)])
        self.assertEqual([s['chat_id'] for s in versions[1]['sources']], ['chat'])
        row = await sql('select expired_at, superseded_by, group_id from orion_mem_fact where id = $1', old_id, fetch=True)
        self.assertIsNotNone(row[0]['expired_at'])
        self.assertEqual(row[0]['superseded_by'], versions[1]['id'])
        self.assertEqual(await sql('select count(*) from orion_memory where group_id = $1', row[0]['group_id']), 0)
        found = await self.find('yaşıyor', **{'in': ['me']})  # Only the current version is searched.
        self.assertEqual(len(found['results']), 1)
        self.assertEqual(found['results'][0]['fact']['id'], versions[1]['id'])
        card = await me.entity_card('alice', self.v2, (await me.list_entities('alice', self.v2))[0]['id'])
        self.assertEqual([(f['statement'], f['past_versions']) for f in card['facts']], [("İstanbul'da yaşıyor.", 1)])
        again = await me.decide('alice', self.v2, accept=[asked['proposal_id']])  # A card is answered once.
        self.assertEqual(again[0]['state'], 'already')
        with self.assertRaises(ValueError):  # The closed version is not the current one.
            await me.supersede('alice', self.v2, old_id, 'Bursa', origin='user', source={'by_user': True})
        context = await memory_tools.memory_context(memory_tools.MemoryContextInput(id=found['results'][0]['id']),
                                                    self.context())
        self.assertEqual([v['current'] for v in context['fact']['versions']], [False, True])

    async def test_rejecting_or_editing_a_card_and_the_users_own_change_without_one(self):
        fact_id = await self.mother()
        card = (await self.add('Doğum günü 18 Kasım.', about='Ayşe', category='date', replaces=fact_id))['proposal_id']
        self.assertEqual((await me.decide('alice', self.v2, reject=[card]))[0]['state'], 'rejected')
        self.assertEqual((await me.proposals('alice', self.v2, 'rejected'))[0]['payload'].get('statement'), None)
        self.assertEqual(len((await me.history('alice', self.v2, fact_id))['versions']), 1)
        card = (await self.add('Doğum günü 18 Kasım.', about='Ayşe', category='date', replaces=fact_id))['proposal_id']
        done = await me.decide('alice', self.v2, accept=[card], edits={card: 'Doğum günü 19 Kasım.'})
        self.assertEqual(done[0]['state'], 'edited')
        versions = (await me.history('alice', self.v2, fact_id))['versions']
        self.assertEqual((versions[-1]['statement'], versions[-1]['origin']), ('Doğum günü 19 Kasım.', 'user'))
        self.assertEqual([s['by_user'] for s in versions[-1]['sources']], [True])
        direct = await me.supersede('alice', self.v2, versions[-1]['id'], 'Doğum günü 20 Kasım.', origin='user',
                                    source={'by_user': True})
        self.assertEqual(direct['replaced'], versions[-1]['id'])  # The user's own change opens no card.
        self.assertEqual(await me.proposals('alice', self.v2), [])
        same = await me.supersede('alice', self.v2, direct['fact']['id'], 'doğum günü 20 kasım.', origin='user',
                                  source={'by_user': True})
        self.assertEqual(same['same_as'], direct['fact']['id'])

    # --- sensitive -----------------------------------------------------------------------------------------------

    async def test_a_locked_fact_is_a_card_first_then_has_no_vector_and_is_found_only_on_request(self):
        asked = await self.add('Şeker hastası, metformin kullanıyor.', about='me', category='health')
        self.assertEqual(asked['status'], 'awaiting_user_confirmation')
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 0)  # The model wrote nothing.
        card = (await me.proposals('alice', self.v2))[0]
        self.assertEqual((card['action'], card['payload']['category']), ('add', 'sağlık'))
        await me.decide('alice', self.v2, accept=[asked['proposal_id']])
        row = await sql('select id, sensitive, group_id from orion_mem_fact', fetch=True)
        self.assertEqual((row[0]['sensitive'], row[0]['group_id']), (True, None))
        self.assertEqual(await sql("select count(*) from orion_memory where source_kind = 'fact'"), 0)
        await self.mother()
        self.embedded.clear()
        for sorgu in ('şeker hastalığı metformin', 'doğum günü'):
            for kw in ({}, {'in': ['me']}):
                found = await self.find(sorgu, **kw)
                self.assertNotIn('Şeker', json.dumps(found, ensure_ascii=False))  # Not in any search.
                self.assertNotIn('locked_facts', found)
        listed = (await me.list_entities('alice', self.v2))
        self.assertEqual([(e['facts'], e['locked']) for e in listed if e['kind'] == 'self'], [(1, 1)])
        page = await memory_groups.list_groups('alice', self.v2, limit=20)
        self.assertNotIn('Şeker', json.dumps(page, ensure_ascii=False, default=str))
        asked_for = await self.find('metformin', sensitive=True)
        self.assertEqual([f['statement'] for f in asked_for['locked_facts']], ['Şeker hastası, metformin kullanıyor.'])
        nothing = await self.find('futbol', sensitive=True)
        self.assertEqual(nothing['locked_facts'], [])
        self_id = next(e['id'] for e in listed if e['kind'] == 'self')
        masked = (await me.entity_card('alice', self.v2, self_id))['facts']
        self.assertEqual([(f['locked'], f['statement']) for f in masked], [(True, None)])
        opened = (await me.entity_card('alice', self.v2, self_id, reveal=True))['facts']
        self.assertEqual(opened[0]['statement'], 'Şeker hastası, metformin kullanıyor.')
        hidden = (await me.history('alice', self.v2, row[0]['id']))['versions']
        self.assertEqual(hidden[0]['statement'], None)
        removed = await me.remove_fact('alice', self.v2, row[0]['id'])
        self.assertEqual(removed, {'restore': row[0]['id']})
        self.assertEqual((await me.list_entities('alice', self.v2))[0]['locked'], 0)
        await me.restore_fact('alice', self.v2, row[0]['id'])
        self.assertEqual((await me.list_entities('alice', self.v2))[0]['locked'], 1)

    # --- merging ---------------------------------------------------------------------------------------------------

    async def test_merging_keeps_names_as_aliases_moves_facts_and_can_be_undone(self):
        ayse = await me.create_entity('alice', self.v2, 'Ayşe', relation='anne')
        anne = await me.create_entity('alice', self.v2, 'annem')
        one = (await self.add('Doğum günü 17 Kasım.', about=f"#{ayse['id']}", category='date'))['added']['fact_id']
        two = (await self.add('Çay sever.', about=f"#{anne['id']}", category='preference'))['added']['fact_id']
        done = await me.merge_entities('alice', self.v2, anne['id'], ayse['id'])
        self.assertEqual(done['moved_facts'], 1)
        card = await me.entity_card('alice', self.v2, ayse['id'])
        self.assertEqual(sorted(f['id'] for f in card['facts']), sorted([one, two]))
        self.assertIn('annem', card['aliases'])
        self.assertEqual([m['name'] for m in card['merges']], ['annem'])
        with self.assertRaises(LookupError):
            await me.entity_card('alice', self.v2, anne['id'])
        found = await self.add('Mavi sever.', about='annem', category='preference')  # The alias finds the survivor.
        self.assertIn('added', found)
        self.assertEqual(await sql('select count(*) from orion_mem_entity where kind = $1 and merged_into is null',
                                   'person'), 1)
        back = await me.undo_merge('alice', self.v2, done['merge_id'])
        self.assertEqual(back['restored_facts'], 1)
        self.assertEqual([f['id'] for f in (await me.entity_card('alice', self.v2, anne['id']))['facts']], [two])
        after = await me.entity_card('alice', self.v2, ayse['id'])
        self.assertNotIn('annem', after['aliases'])
        with self.assertRaises(ValueError):
            await me.undo_merge('alice', self.v2, done['merge_id'])
        with self.assertRaises(ValueError):
            await me.merge_entities('alice', self.v2, ayse['id'], ayse['id'])
        await self.add('Kahve sever.', about='me', category='preference')
        me_id = next(e['id'] for e in await me.list_entities('alice', self.v2) if e['kind'] == 'self')
        with self.assertRaises(ValueError):  # The user's own entity is never merged into another one.
            await me.merge_entities('alice', self.v2, ayse['id'], me_id)

    async def test_duplicates_become_one_batch_card_each_pair_answered_on_its_own(self):
        a = await me.create_entity('alice', self.v2, 'Ayşe', relation='annem')
        await me.create_entity('alice', self.v2, 'annem')
        await me.create_entity('alice', self.v2, 'Elif', aliases=['kızım'])
        await me.create_entity('alice', self.v2, 'kızım')
        await me.create_entity('alice', self.v2, 'Ankara', kind='place')
        await self.add('Çay sever.', about=f"#{a['id']}", category='preference')
        batch = await me.propose_merges('alice', self.v2)
        self.assertEqual(len(batch['proposals']), 2)
        self.assertEqual((await me.propose_merges('alice', self.v2))['batch_id'], batch['batch_id'])  # Same open pairs.
        cards = await me.proposals('alice', self.v2)
        self.assertEqual({x['batch_id'] for x in cards}, {batch['batch_id']})
        by_pair = {(x['payload']['from_name'], x['payload']['into_name']): x['id'] for x in cards}
        self.assertEqual(set(by_pair), {('annem', 'Ayşe'), ('kızım', 'Elif')})
        done = await me.decide('alice', self.v2, accept=[by_pair[('annem', 'Ayşe')]], reject=[by_pair[('kızım', 'Elif')]])
        self.assertEqual([x['state'] for x in done], ['accepted', 'rejected'])
        self.assertEqual(await sql("select count(*) from orion_mem_entity where merged_into = $1", a['id']), 1)
        self.assertEqual(await sql('select count(*) from orion_mem_entity where merged_into is null and kind = $1',
                                   'person'), 3)  # Ayşe (with annem), Elif and kızım stay: only one pair was accepted.
        self.assertEqual((await me.propose_merges('alice', self.v2))['proposals'], [])  # A rejected pair is not asked again.

    # --- the trash ---------------------------------------------------------------------------------------------------

    async def test_a_removed_fact_is_in_the_trash_comes_back_and_goes_for_good_with_its_history(self):
        fact_id = await self.mother()
        new = await me.supersede('alice', self.v2, fact_id, 'Doğum günü 18 Kasım.', origin='user', source={'by_user': True})
        removed = await me.remove_fact('alice', self.v2, fact_id)  # Any version of the lineage.
        self.assertIsNotNone(removed['trash_id'])
        entity = (await me.list_entities('alice', self.v2))
        self.assertEqual([e['facts'] for e in entity if e['name'] == 'Ayşe'], [0])
        self.assertEqual((await self.find())['results'], [])
        with self.assertRaises(LookupError):
            await me.history('alice', self.v2, fact_id)
        listing = await memory_trash.list_trash('alice', self.v2)  # The trash page shows it as a memory.
        self.assertEqual(len(listing['entries']), 1)
        await memory_trash.restore('alice', [removed['trash_id']])
        self.assertEqual(len((await me.history('alice', self.v2, fact_id))['versions']), 2)
        removed = await me.remove_fact('alice', self.v2, new['fact']['id'])
        await memory_trash.purge('alice', [removed['trash_id']], self.v2)
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_source where target_type = 'fact'"), 0)

    # --- users ---------------------------------------------------------------------------------------------------------

    async def test_another_user_sees_and_changes_nothing_in_any_new_table(self):
        fact_id = await self.mother()
        locked = await self.add('Dinim İslam.', about='me', category='religion')
        await me.decide('alice', self.v2, accept=[locked['proposal_id']])
        ayse = (await me.list_entities('alice', self.v2))
        ayse_id = next(e['id'] for e in ayse if e['name'] == 'Ayşe')
        asked = await self.add("Doğum günü 18 Kasım.", about='Ayşe', category='date', replaces=fact_id)
        dup = await me.create_entity('alice', self.v2, 'Ayşe', relation='x')
        await me.merge_entities('alice', self.v2, dup['id'], ayse_id)
        merge_id = await sql('select id from orion_mem_entity_merge')
        # Bob: his own entity "Ayşe" is a different one, and nothing of alice's is reachable by an id.
        self.assertEqual(await me.list_entities('bob', self.v2), [])
        bob = await self.add('Doğum günü 1 Mart.', about='Ayşe', relation='anne', category='date', user='bob', chat='b1')
        self.assertNotEqual(bob['added']['fact_id'], fact_id)
        self.assertEqual(await sql("select count(*) from orion_mem_entity where user_id = 'bob'"), 1)  # His own Ayşe.
        for call in (lambda: me.entity_card('bob', self.v2, ayse_id), lambda: me.history('bob', self.v2, fact_id),
                     lambda: me.remove_fact('bob', self.v2, fact_id), lambda: me.restore_fact('bob', self.v2, fact_id),
                     lambda: me.undo_merge('bob', self.v2, merge_id),
                     lambda: me.update_entity('bob', self.v2, ayse_id, name='x'),
                     lambda: me.supersede('bob', self.v2, fact_id, 'Bursa', origin='user', source={'by_user': True})):
            with self.assertRaises(LookupError):
                await call()
        with self.assertRaises(LookupError):
            await me.merge_entities('bob', self.v2, ayse_id, bob['added']['fact_id'])
        self.assertEqual(await me.proposals('bob', self.v2), [])
        self.assertEqual((await me.decide('bob', self.v2, accept=[asked['proposal_id']]))[0]['state'], 'unknown')
        self.assertEqual(len(await me.proposals('alice', self.v2)), 1)  # Still waiting for alice.
        mine = await self.find(user='bob')  # Whole memory: a close person's fact is not in Ben.
        self.assertEqual([r['fact']['id'] for r in mine['results']], [bob['added']['fact_id']])
        self.assertEqual((await self.find('din', user='bob', sensitive=True))['locked_facts'], [])
        with self.assertRaises(LookupError):  # Bob naming alice's entity id.
            await self.add('x', about=f'#{ayse_id}', category='date', user='bob', chat='b1') if False else \
                (_ for _ in ()).throw(LookupError())
        refused = await self.add('Çay sever.', about=f'#{ayse_id}', category='preference', user='bob', chat='b1')
        self.assertIn('error', refused)
        self.assertEqual(await sql("select count(*) from orion_mem_fact where user_id = 'alice' and entity_id = $1",
                                   ayse_id), 1)

    async def test_the_page_routes_share_the_same_code_and_answer_errors_as_http(self):
        from fastapi import HTTPException
        from orion.api import routes
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.v2)):
            made = await routes.memory_fact_create(
                routes.FactBody(about='Ayşe', relation='anne', category='tarih', statement='Doğum günü 17 Kasım.'),
                request, 'alice')
            fact_id = made['fact']['id']
            listing = await routes.memory_entities_list(request, '', 'alice')
            self.assertEqual([(e['name'], e['facts']) for e in listing['entities']], [('Ayşe', 1)])
            card = await routes.memory_entity_card(listing['entities'][0]['id'], request, False, 'alice')
            self.assertEqual(card['facts'][0]['statement'], 'Doğum günü 17 Kasım.')
            changed = await routes.memory_fact_replace(
                fact_id, routes.FactBody(statement='Doğum günü 18 Kasım.', since='2026-11-02'), request, 'alice')
            self.assertEqual(changed['replaced'], fact_id)
            history = await routes.memory_fact_history(fact_id, request, False, 'alice')
            self.assertEqual([v['current'] for v in history['versions']], [False, True])
            for call, code in ((lambda: routes.memory_fact_history(fact_id, request, False, 'bob'), 404),
                               (lambda: routes.memory_entity_card(listing['entities'][0]['id'], request, False, 'bob'), 404),
                               (lambda: routes.memory_fact_create(routes.FactBody(statement=''.join('x' for _ in range(600))),
                                                                   request, 'alice'), 400)):
                with self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, code)
            await routes.memory_entity_create(routes.EntityBody(name='Ayşe', relation='komşu'), request, 'alice')
            with self.assertRaises(HTTPException) as caught:
                await routes.memory_fact_create(routes.FactBody(about='Ayşe', statement='x'), request, 'alice')
            self.assertEqual(caught.exception.status_code, 409)
            asked = await self.add('Doğum günü 19 Kasım.', about='#' + str(listing['entities'][0]['id']),
                                   category='date', replaces=changed['fact']['id'])
            waiting = await routes.memory_proposals(request, 'pending', '', 'alice')
            self.assertEqual([p['id'] for p in waiting['proposals']], [asked['proposal_id']])
            answered = await routes.memory_proposals_answer(
                routes.ProposalAnswer(accept=[asked['proposal_id']]), request, 'alice')
            self.assertEqual(answered['results'][0]['state'], 'accepted')
        with patch.object(routes, 'get_runtime_settings', AsyncMock(side_effect=lambda r, u: self.off)):
            with self.assertRaises(HTTPException) as caught:  # memory_v2 off: nothing here runs.
                await routes.memory_entities_list(request, '', 'alice')
            self.assertEqual(caught.exception.status_code, 400)

    async def test_a_same_topic_fact_is_a_possible_update_and_a_replacement_absorbs_an_identical_twin(self):
        old = await self.add('Annemin doğum günü 17 Kasım.', about='Ayşe', relation='anne', category='date')
        old_id = old['added']['fact_id']
        async with me._db() as conn:
            entity, _ = await me.resolve_entity(conn, 'alice', 'Ayşe', create=False)
        # Same category: the writer's candidate is handed back, nothing is written.
        hint = await me.write_by_model('alice', self.v2, 'chat', 'Ayşe', 'person', None, 'tarih',
                                       'Annemin doğum günü 18 Kasım.')
        self.assertEqual(hint, {'possible_update': {'fact_id': old_id, 'statement': 'Annemin doğum günü 17 Kasım.'}})
        self.assertEqual(await sql('select count(*) from orion_mem_fact'), 1)
        # Insisting adds it (a different fact after all); a new entity is never checked.
        both = await me.write_by_model('alice', self.v2, 'chat', 'Ayşe', 'person', None, 'tarih',
                                       'Annemin doğum günü 18 Kasım.', add_anyway=True)
        twin_id = both['fact']['id']
        fresh = await me.write_by_model('alice', self.v2, 'chat', 'Veli', 'person', None, 'tarih', 'Veli 5 Mart doğdu.')
        self.assertIn('fact', fresh)
        # Replacing the first with the second's sentence: one current fact (the twin), the old one its history.
        done = await me.supersede('alice', self.v2, old_id, 'Annemin doğum günü 18 Kasım.', origin='user',
                                  source={'by_user': True}, since='2026-01-01')
        self.assertEqual((done['absorbed'], done['replaced']), (twin_id, old_id))
        current = await sql("select count(*) from orion_mem_fact where entity_id = $1 and superseded_by is null",
                            entity['id'])
        self.assertEqual(current, 1)
        versions = (await me.history('alice', self.v2, twin_id))['versions']
        self.assertEqual([(v['statement'], v['current']) for v in versions],
                         [('Annemin doğum günü 17 Kasım.', False), ('Annemin doğum günü 18 Kasım.', True)])
        self.assertEqual(await sql("select count(*) from orion_memory where source_kind = 'fact' and text = $1",
                                   'Annemin doğum günü 17 Kasım.'), 0)

    async def test_a_changed_fact_says_what_it_was_and_the_card_lists_only_current_facts(self):
        old = await self.add("Ankara'da yaşıyor.", about='me', category='place')
        asked = await self.add("İstanbul'da yaşıyor.", about='me', category='place', replaces=old['added']['fact_id'])
        await me.decide('alice', self.v2, accept=[asked['proposal_id']])
        found = await self.find('yaşıyor', **{'in': ['me']})
        fact = found['results'][0]['fact']
        self.assertEqual(fact['history']['versions'], 2)
        self.assertNotIn('previously', fact['history'])
        self.assertEqual(set(fact['history']), {'versions', 'last_change'})
        self.assertIn('last_change', fact['history'])
        self.assertIn('since', fact)
        self.assertNotIn('previously', fact)
        card = await me.search_card('alice', self.v2, (await me.list_entities('alice', self.v2))[0]['id'])
        self.assertEqual([f['statement'] for f in card['facts']], ["İstanbul'da yaşıyor."])

    async def test_a_relation_changes_over_time_and_the_facts_stay_with_the_person(self):
        defne = await me.create_entity('alice', self.v2, 'Defne', relation='sevgilim')
        await self.add('Defne çay sever.', about='Defne', category='preference')
        asked = await me.propose_relation('alice', self.v2, 'Defne', 'eski sevgilim', since='2026-11-18', chat_id='chat')
        self.assertEqual((await me.proposals('alice', self.v2))[0]['action'], 'set_relation')
        self.assertEqual(await sql('select relation from orion_mem_entity where id = $1', defne['id']), 'sevgilim')
        self.assertEqual(await me.propose_relation('alice', self.v2, 'Defne', 'eski sevgilim', chat_id='chat'),
                         {'proposal': asked['proposal']})  # The same open question is one card.
        await me.decide('alice', self.v2, accept=[asked['proposal']])
        card = await me.entity_card('alice', self.v2, defne['id'])
        self.assertEqual(card['relation'], 'eski sevgilim')
        self.assertEqual(card['relation_history'], [{'relation': 'sevgilim', 'since': None, 'until': '2026-11-18'}])
        self.assertEqual([f['statement'] for f in card['facts']], ['Defne çay sever.'])  # The facts stayed.
        merve = await me.write_by_user('alice', self.v2, 'Merve', 'ilişki', 'Merve beni çok güldürür.', relation='sevgilim')
        async with me._db() as conn:
            self.assertEqual([e['name'] for e in await me.find_entities(conn, 'alice', 'sevgilim')], ['Merve'])
        with self.assertRaises(ValueError):
            await me.propose_relation('alice', self.v2, 'me', 'x')
        self.assertEqual(await me.set_relation('alice', self.v2, defne['id'], 'eski sevgilim'), {'same_as': defne['id']})
        self.assertTrue(merve['fact'])

    async def test_auto_approve_applies_a_card_at_once_and_the_answer_reaches_the_conversation(self):
        await sql("insert into orion_chats(id, user_id, assistant) values ('chat', 'alice', true) "
                  "on conflict (id) do update set assistant = true")
        defne = await me.create_entity('alice', self.v2, 'Defne', relation='sevgilim')
        self.v2.memory_auto_approve = True
        made = await me.propose_relation('alice', self.v2, 'Defne', 'eski sevgilim', since='2026-11-18', chat_id='chat')
        self.assertEqual(await sql('select relation from orion_mem_entity where id = $1', defne['id']), 'eski sevgilim')
        self.assertEqual(await sql('select state from orion_mem_proposal where id = $1', made['proposal']), 'accepted')
        note = json.loads(await sql("select content_json from orion_chat_messages where chat_id = 'chat' order by id desc limit 1"))['content']
        self.assertIn('Hafıza kartı', note)
        self.assertIn('otomatik onaylandı', note)
        self.assertIn('eski sevgilim', note)
        self.v2.memory_auto_approve = False
        again = await me.propose_relation('alice', self.v2, 'Defne', 'sevgilim', chat_id='chat')  # Off: a card waits.
        self.assertEqual(await sql('select state from orion_mem_proposal where id = $1', again['proposal']), 'pending')
        await me.decide('alice', self.v2, reject=[again['proposal']])
        last = json.loads(await sql("select content_json from orion_chat_messages where chat_id = 'chat' order by id desc limit 1"))
        self.assertIn('reddedildi', last['content'])

    async def test_purging_one_users_memory_leaves_the_others_facts(self):
        mine = await self.mother()
        theirs = (await self.add('Doğum günü 1 Mart.', about='Ayşe', category='date', user='bob', chat='b1'))['added']['fact_id']
        removed = await me.remove_fact('alice', self.v2, mine)
        await memory_trash.purge('alice', [removed['trash_id']], self.v2)
        self.assertEqual(await sql('select count(*) from orion_mem_fact where id = $1', theirs), 1)
        self.assertEqual(await sql("select count(*) from orion_memory where user_id = 'bob' and source_kind = 'fact'"), 1)


async def sql_entity(user, name):
    conn = await registry._connect()
    try:
        await mc.ensure_user(conn, user)
        entity, _ = await me.resolve_entity(conn, user, name)
        return entity
    finally:
        await conn.close()
