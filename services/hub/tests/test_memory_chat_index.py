"""Memory step 3 (docs/hafiza-tasarimi.md §16): the chats index. Windows on message boundaries with their message ids,
the Sohbetler collection, search across chats and in one chat, incremental re-indexing, deleting a chat, the backlog
job, the per-chat switch and user isolation of every new table. On a throwaway real PostgreSQL (test_memory_search);
embeddings are hand-made vectors (a text that says "pizza" is near the query)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.kernel import memory, memory_chat_index as ci, memory_collections as mc, memory_groups, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import SPACE, PostgresMemoryCase, vec


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


def say(role, text, **extra):
    return {'role': role, 'content': text, **extra}


def turn(question, answer):
    return [say('user', question), say('assistant', answer)]


class ChatIndexTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_chat_messages, orion_mem_chat_state, orion_mem_chat_index_jobs, orion_mem_trash')
        await sql("insert into orion_chats(id, user_id, title) values ('c1','alice','Pizza sohbeti'),"
                  "('c2','alice','Başka'),('b1','bob','Bobun sohbeti')")
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_index_chats=True)
        self.embedded = []

        async def embed(text, settings, kind, local):
            self.embedded.append(text)
            return [vec(0.9, 5) if 'pizza' in text.lower() else vec(0.1, 6 + len(self.embedded) % 700)]

        async def target(settings, space=None):
            return settings, SPACE, False
        for module, name, value in ((memory, '_embed', embed), (memory, 'embedding_target', target)):
            guard = patch.object(module, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.backup = AsyncMock(return_value=None)
        from orion.kernel import memory_backup
        guard = patch.object(memory_backup, 'backup', self.backup)
        guard.start()
        self.addCleanup(guard.stop)

    async def put(self, chat, *messages):
        await registry.insert_messages(chat, list(messages))

    async def index(self, chat='c1', user='alice'):
        conn = await registry._connect()
        try:
            await mc.ensure_user(conn, user)
            return await ci.index_chat(conn, user, chat, self.v2)
        finally:
            await conn.close()

    async def windows(self, chat='c1', user='alice'):
        return await sql("select id, text, source_message_id as first, source_message_last as last, group_id, "
                         "source_kind, kind from orion_memory where user_id = $1 and source_chat_id = $2 and not whole "
                         "order by item_order, id", user, chat, fetch=True)

    async def test_windows_follow_messages_with_prefixes_ids_and_no_tool_output(self):
        await self.put('c1', say('user', 'pizzayı çok severim'), say('assistant', 'Hangi pizza?', tool_calls=[{'id': 'x'}]),
                       {'role': 'tool', 'content': 'GİZLİ ARAÇ ÇIKTISI', 'tool_call_id': 'x'},
                       say('assistant', 'Margarita güzel.'),
                       say('user', 'hata', failed_turn=True), say('assistant', 'Hata: boom', failed_turn=True))
        done = await self.index()
        self.assertEqual(done['state'], 'ok')
        rows = await self.windows()
        self.assertEqual(len(rows), 1)
        text = rows[0]['text']
        self.assertIn('Kullanıcı: pizzayı çok severim', text)
        self.assertIn('Asistan: Margarita güzel.', text)
        self.assertNotIn('GİZLİ', text)
        self.assertNotIn('boom', text)
        ids = await sql("select id from orion_chat_messages where chat_id = 'c1' order by id", fetch=True)
        self.assertEqual((rows[0]['first'], rows[0]['last']), (ids[0]['id'], ids[3]['id']))
        self.assertEqual((rows[0]['source_kind'], rows[0]['kind']), ('chat_index', 'text'))
        member = await sql("select collection_id, main from orion_mem_collection_item where user_id = 'alice' "
                           "and group_id = $1", rows[0]['group_id'], fetch=True)
        self.assertEqual([(m['collection_id'], m['main']) for m in member], [('sohbetler', True)])
        state = await sql("select indexed_upto, windows from orion_mem_chat_state where chat_id = 'c1'", fetch=True)
        self.assertEqual((state[0]['indexed_upto'], state[0]['windows']), (ids[-1]['id'], 1))

    async def test_a_long_message_is_split_alone_and_turns_pack_to_a_core(self):
        long = ' '.join(f'cümle{k} pizza hakkında uzun bir açıklama yapıyor.' for k in range(200))
        await self.put('c1', *turn('merhaba', 'selam'), *turn('uzun anlat', long), *turn('tamam', 'peki'))
        await self.index()
        rows = await self.windows()
        self.assertGreater(len(rows), 3)
        starts = [r['text'].startswith('Kullanıcı: merhaba') for r in rows]
        self.assertTrue(starts[0])
        big = [r for r in rows if r['first'] == r['last']]  # The long answer's chunks: each inside one message.
        self.assertGreaterEqual(len(big), 2)
        self.assertEqual({r['first'] for r in big if 'cümle' in r['text']}, {big[0]['first']})

    async def test_default_searches_do_not_see_chats_but_in_chats_and_one_chat_do(self):
        await self.put('c1', *turn('pizzayı seviyorum', 'ne güzel'))
        await self.put('c2', *turn('hava nasıl', 'güneşli'))
        await self.put('b1', *turn('bobun pizzası', 'tamam'))
        await self.index('c1'), await self.index('c2'), await self.index('b1', 'bob')
        note = await self.add('pizza notu', score=0.5, axis=9, group='n')
        page = await memory_groups.list_groups('alice', self.v2, limit=20)
        self.assertEqual([c['group_id'] for c in page['groups']], ['n'])  # The page's "Tümü": no chat windows.
        total = await sql("select n from orion_memory_sum_total where user_id = 'alice'")
        self.assertEqual(total, 1)  # Chats are not memories of the count.
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.v2)):
            context = ToolContext('alice', 'chat', 't', None)

            async def ask(**kw):
                args = memory_tools.MemorySearchInput.model_validate({'queries': [{'text': 'pizza'}], **kw})
                return await memory_tools.search_memory(args, context)
            everything = await ask()
            self.assertEqual([r['id'] for r in everything['results']], [note])
            chats = await ask(**{'in': ['chats']})
            self.assertEqual(len(chats['results']), 2)  # Alice's two chats, never bob's.
            best = chats['results'][0]
            self.assertEqual(best['chat']['id'], 'c1')
            self.assertEqual(best['chat']['title'], 'Pizza sohbeti')
            first = await sql("select min(id) from orion_chat_messages where chat_id = 'c1'")
            self.assertEqual(best['chat']['message_id'], first)
            inside = await ask(**{'in': ['chat:c2']})
            self.assertEqual([r['chat']['id'] for r in inside['results']], ['c2'])
            theirs = await ask(**{'in': ['chat:b1']})  # Another user's chat id finds nothing.
            self.assertEqual(theirs['results'], [])
        page = await memory_groups.list_groups('alice', self.v2, limit=20, collections=['chats'])
        self.assertEqual(len(page['groups']), 2)
        self.assertTrue(all(c['source_kind'] == 'chat_index' and c['source_chat_id'] for c in page['groups']))

    async def test_a_new_turn_embeds_only_the_last_windows_and_keeps_the_rest(self):
        filler = ' '.join(['dolgu'] * 140)
        for k in range(6):
            await self.put('c1', *turn(f'soru {k} {filler}', f'cevap {k} {filler}'))
        await self.index()
        before = await self.windows()
        self.assertGreater(len(before), 4)
        self.embedded.clear()
        await self.put('c1', *turn('yeni pizza sorusu', 'yeni cevap'))
        again = await self.index()
        after = await self.windows()
        self.assertLessEqual(again['embedded'], 2)  # The last window and the one before it (its context changed).
        self.assertLessEqual(len(self.embedded), 2)
        keep = len(before) - 2
        self.assertEqual([r['id'] for r in before[:keep]], [r['id'] for r in after[:keep]])  # Untouched rows.
        self.assertIn('yeni pizza sorusu', ' '.join(r['text'] for r in after))
        self.embedded.clear()
        nothing = await self.index()
        self.assertEqual((nothing['embedded'], self.embedded), (0, []))  # Nothing changed: nothing embedded.

    async def test_a_deleted_message_leaves_no_trace_and_unchanged_windows_are_not_embedded_again(self):
        filler = ' '.join(['dolgu'] * 140)
        await self.put('c1', *turn('gizli kelime kedi ' + filler, 'ok ' + filler))
        for k in range(5):
            await self.put('c1', *turn(f'soru {k} {filler}', f'cevap {k} {filler}'))
        await self.index()
        self.assertTrue(any('gizli kelime' in r['text'] for r in await self.windows()))
        rows = await sql("select id from orion_chat_messages where chat_id = 'c1' order by id", fetch=True)
        await sql('delete from orion_chat_messages where chat_id = $1 and id = any($2::bigint[])', 'c1',
                  [rows[0]['id'], rows[1]['id']])
        self.embedded.clear()
        await self.index()
        after = await self.windows()
        self.assertFalse(any('gizli kelime' in r['text'] for r in after))
        self.assertFalse(any(r['first'] in (rows[0]['id'], rows[1]['id']) for r in after))
        count = await sql("select count(*) from orion_memory where source_chat_id = 'c1' and text like '%gizli kelime%'")
        self.assertEqual(count, 0)
        # Windows far from the change kept their vectors (the first one's text changed, the later ones are the same).
        self.assertLess(len(self.embedded), len(after))

    async def test_deleting_the_chat_removes_the_windows_of_that_chat_only(self):
        await self.put('c1', *turn('pizza', 'evet'))
        await self.put('c2', *turn('hava', 'güneş'))
        await self.put('b1', *turn('pizza', 'evet'))
        await self.index('c1'), await self.index('c2'), await self.index('b1', 'bob')
        removed = await ci.forget_chat('c1', 'alice')
        self.assertEqual(removed, 1)
        self.assertEqual(await sql("select count(*) from orion_memory where source_chat_id = 'c1'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where chat_id = 'c1'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_collection_item where user_id = 'alice' "
                                   "and collection_id = 'sohbetler'"), 1)
        # Another user's call with the wrong owner touches nothing of bob's.
        self.assertEqual(await ci.forget_chat('b1', 'alice'), 0)
        self.assertEqual(await sql("select count(*) from orion_memory where user_id = 'bob'"), 1)
        # Without the owner (the admin path) the rows of that chat id go, whoever's they were.
        self.assertEqual(await ci.forget_chat('b1'), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where user_id = 'bob'"), 0)

    async def test_users_never_see_each_other_s_state_jobs_or_windows(self):
        await self.put('c1', *turn('pizza', 'evet'))
        await self.put('b1', *turn('pizza', 'evet'))
        await self.index('c1')
        self.assertEqual((await self.index('c1', 'bob'))['state'], 'gone')  # Bob cannot index alice's chat.
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where user_id = 'bob'"), 0)
        self.assertEqual((await ci.status('bob'))['indexed'], 0)
        self.assertEqual((await ci.status('bob'))['pending'], 1)  # His own chat only.
        self.assertEqual((await ci.status('alice'))['indexed'], 1)
        with self.assertRaises(LookupError):
            await ci.set_chat_enabled('bob', 'c1', False, self.v2)
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where user_id = 'alice' "
                                   "and not memory_enabled"), 0)
        self.assertEqual((await ci.chat_state('bob', 'c1'))['windows'], 0)

    async def test_the_per_chat_switch_removes_and_restores(self):
        await self.put('c1', *turn('pizza', 'evet'))
        await self.index()
        self.assertEqual(len(await self.windows()), 1)
        await ci.set_chat_enabled('alice', 'c1', False, self.v2)
        self.assertEqual(await self.windows(), [])
        self.assertEqual((await self.index())['state'], 'off')  # Off: never indexed again.
        self.assertEqual(await self.windows(), [])
        await ci.set_chat_enabled('alice', 'c1', True, self.v2)
        await asyncio.sleep(0)
        for task in list(ci._tasks.values()):
            task.cancel()
        self.assertEqual((await self.index())['state'], 'ok')
        self.assertEqual(len(await self.windows()), 1)

    async def test_the_backlog_job_indexes_old_chats_reports_and_can_be_cancelled(self):
        for k in range(3):
            await sql("insert into orion_chats(id, user_id, title) values ($1, 'alice', 'eski')", f'old{k}')
            await self.put(f'old{k}', *turn('pizza', f'cevap {k}'))
        await self.put('c1', *turn('pizza', 'evet'))
        before = await ci.status('alice')
        self.assertEqual((before['chats'], before['pending'], before['indexed']), (6, 4, 0))
        with self.assertRaises(ValueError):
            await ci.start_backlog('alice', SimpleNamespace(router_model_group='chat', memory_v2=False))
        job = await ci.start_backlog('alice', self.v2)
        self.assertEqual((job['state'], job['total']), ('running', 4))
        with self.assertRaises(ValueError):  # One job at a time.
            await ci.start_backlog('alice', self.v2)
        await asyncio.gather(*ci._jobs)
        after = await ci.status('alice')
        self.assertEqual((after['job']['state'], after['job']['done'], after['job']['failed']), ('done', 4, 0))
        self.assertEqual((after['indexed'], after['pending'], after['windows']), (4, 0, 4))
        self.assertEqual((await ci.status('bob'))['job'], None)
        # A new message makes the chat pending again; cancelling stops the next run before it starts a chat.
        await self.put('c1', *turn('daha', 'fazla'))
        self.assertEqual((await ci.status('alice'))['pending'], 1)
        await ci.start_backlog('alice', self.v2)
        await ci.cancel_backlog('alice')
        await asyncio.gather(*ci._jobs)
        self.assertIn((await ci.status('alice'))['job']['state'], ('cancelled', 'done'))

    async def test_an_unreachable_model_stops_the_job_with_its_message(self):
        await self.put('c1', *turn('pizza', 'evet'))

        async def down(settings, space=None):
            raise memory.MemoryUnavailable('Router yok')
        with patch.object(memory, 'embedding_target', down):
            with self.assertRaises(memory.MemoryUnavailable):
                await ci.start_backlog('alice', self.v2)
        job = await ci.start_backlog('alice', self.v2)
        await asyncio.gather(*ci._jobs)
        self.assertEqual((await ci.status('alice'))['job']['state'], 'done')

    async def test_nothing_runs_with_the_setting_off_and_changes_are_debounced_to_one_run(self):
        await self.put('c1', *turn('pizza', 'evet'))
        ci.changed('c1', SimpleNamespace(memory_v2=False))
        self.assertEqual(ci._tasks, {})
        self.assertEqual((await ci.index_now('c1', SimpleNamespace(memory_v2=False)))['state'], 'off')
        self.assertEqual(await self.windows(), [])
        calls = []

        async def run(chat_id, settings, user_id=None):
            calls.append(chat_id)
            return {}
        with patch.object(ci, 'DEBOUNCE', 0.05), patch.object(ci, 'index_now', run):
            for _ in range(4):
                ci.changed('c1', self.v2)
            await asyncio.gather(*ci._tasks.values())
        self.assertLessEqual(len(calls), 2)  # A burst: one run, plus at most one for what came while it ran.
        self.assertGreaterEqual(len(calls), 1)

    async def test_reverting_the_collections_removes_the_chat_windows_of_that_user_only(self):
        await self.put('c1', *turn('pizza', 'evet'))
        await self.put('b1', *turn('pizza', 'evet'))
        await self.index('c1'), await self.index('b1', 'bob')
        await mc.revert('alice')
        self.assertEqual(await sql("select count(*) from orion_memory where user_id = 'alice'"), 0)
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where user_id = 'alice'"), 0)
        self.assertEqual(await sql("select count(*) from orion_memory where user_id = 'bob'"), 1)
        self.assertEqual(await sql("select count(*) from orion_mem_chat_state where user_id = 'bob'"), 1)

    async def test_a_removed_chat_memory_stays_removed_until_it_is_restored(self):
        await self.put('c1', *turn('pizza', 'evet'))
        await self.index()
        ids = [r['id'] for r in await self.windows()]
        await sql("insert into orion_mem_trash (memory_id, user_id, trash_id) select unnest($1::bigint[]), 'alice', 't1'",
                  ids)
        await self.put('c1', *turn('yine pizza', 'tamam'))
        self.assertEqual((await self.index())['state'], 'trash')
        self.assertEqual(len(await self.windows()), 1)
