import unittest
from datetime import date, datetime, timezone

from orion.kernel import memory_entities as me, memory_profile as mp


class ShortDateTests(unittest.TestCase):
    def test_turkish_short_dates_never_iso(self):
        self.assertEqual(mp.short_date(date(2026, 11, 17), None), "17 Kas 2026")
        self.assertEqual(mp.short_date(date(2026, 11, 9), date(2026, 11, 15), "approx"), "9–15 Kas 2026, yaklaşık")
        self.assertEqual(mp.short_date(date(2026, 11, 28), date(2026, 12, 2)), "28 Kas–2 Ara 2026")
        self.assertEqual(mp.short_date(date(2026, 11, 1), None, "month"), "Kas 2026")
        self.assertEqual(mp.short_date(date(2026, 11, 17), None, "day", year=False), "17 Kas")


class ComposeTests(unittest.TestCase):
    def line(self, text, pinned=False, ids=()):
        return {"id": text[:3], "text": text, "pinned": pinned, "fact_ids": list(ids), "added_at": "x", "edited": False}

    def test_pinned_lines_come_first_and_the_page_text_has_no_wrapper(self):
        block = mp.compose([self.line("Kahve.", ids=[1]), self.line("Kedim Tarçın", pinned=True)])
        self.assertEqual([l["text"] for l in block["lines"]], ["Kedim Tarçın", "Kahve."])
        self.assertEqual(block["text"], "- Kedim Tarçın\n- Kahve.")
        self.assertTrue(block["prompt"].startswith(mp.HEADER))
        self.assertNotIn("What you know", block["text"])
        self.assertEqual(block["fact_ids"], [1])
        self.assertFalse(block["needs_compaction"])

    def test_past_the_soft_limit_it_needs_compaction_and_past_the_hard_one_lines_are_left_out(self):
        lines = [self.line("a" * 400, ids=[1]), self.line("b" * 400, ids=[2]), self.line("Sabit.", pinned=True)]
        block = mp.compose(lines, limit=250, soft=150)
        self.assertTrue(block["needs_compaction"])
        self.assertTrue(block["lines"][0]["in_prompt"])  # the pinned one always
        self.assertEqual(block["left_out"], 1)
        self.assertEqual(mp.compose(lines, limit=5000, soft=5000)["left_out"], 0)

    def test_an_older_store_of_plain_strings_gets_ids(self):
        lines = mp._stored_lines({"sig": "x", "lines": ["Bir.", {"id": "z", "text": "İki."}]})
        self.assertEqual(lines, [{"id": "c0", "text": "Bir."}, {"id": "z", "text": "İki."}])


class SincePrecisionTests(unittest.TestCase):
    def test_month_and_year_starts(self):
        self.assertEqual(me._day("2023-04"), date(2023, 4, 1))
        self.assertEqual(me.precision_of("2023-04"), "month")
        self.assertEqual(me.precision_of("2023"), "year")
        self.assertEqual(me.precision_of("2023-04-05"), "day")
        self.assertEqual(me.since_text("2023-04"), "2023-04")
        self.assertEqual(me.since_text("2023-04-05"), "2023-04-05")

    def test_a_fact_without_since_counts_from_its_saved_day_not_always(self):
        row = {"valid_from": None, "valid_to": None, "expired_at": None,
               "recorded_at": datetime(2026, 11, 1, 10, tzinfo=timezone.utc)}
        self.assertFalse(me._true_within(row, (date(2023, 1, 1), date(2023, 12, 31))))
        self.assertTrue(me._true_within(row, (date(2026, 11, 1), None)))
        row["valid_from"] = date(2023, 4, 1)
        self.assertTrue(me._true_within(row, (date(2023, 1, 1), date(2023, 12, 31))))


import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.kernel import memory, memory_extract as mx, registry
from test_memory_search import SPACE, PostgresMemoryCase, vec


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class ProfileLineAndCompactTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_fact, orion_mem_entity, orion_mem_profile, orion_mem_source cascade')
        self.v2 = SimpleNamespace(router_model_group='chat', memory_v2=True, memory_trash_days=30, memory_profile=True)

        async def embed(text, settings, kind, local):
            return [vec(0.1, 6 + len(text) % 700)]

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

    async def test_satir_ekle_gets_its_vector_row_at_once_and_returns_its_group_id(self):
        done = await mp.add_line('alice', self.v2, 'Kedim Tarçın.')
        group = done['group_id']
        self.assertTrue(group)
        self.assertEqual(done['fact']['group_id'], group)
        self.assertEqual(await sql("select count(*) from orion_memory where user_id = 'alice' and group_id = $1", group), 1)
        self.assertIn(group, done['profile']['group_ids'].values())  # The map can link the profile line to its memory.
        again = await mp.add_line('alice', self.v2, 'Kedim Tarçın.')  # The same sentence: its memory, not a new one.
        self.assertEqual(again['group_id'], group)

    async def test_profile_compact_runs_in_the_background_and_its_failure_stays_inside(self):
        await mp.add_line('alice', self.v2, 'Kedim Tarçın.')  # Pinned: nothing left to shorten.
        with self.assertRaises(ValueError):
            await mp.start_compact('alice', self.v2)
        await me.write_by_user('alice', self.v2, '', 'diğer', 'Kahveyi sever.')  # Not pinned.
        with patch.object(mp, 'compact_profile', AsyncMock(side_effect=mx.BadOutput('the model gave no lines'))) as run:
            self.assertEqual(await mp.start_compact('alice', self.v2), {'state': 'started'})
            for _ in range(50):
                if 'alice' not in mp._compacting:
                    break
                await asyncio.sleep(0.02)
            run.assert_awaited_once()  # The model's failure ended in a log line, not in the HTTP answer.
        self.assertNotIn('alice', mp._compacting)
        self.assertEqual(mp._compact_errors.get('alice'), 'the model gave no lines')

    async def lines(self):
        return (await mp.get('alice', self.v2))['lines']

    async def write(self, *texts):
        for text in texts:
            await me.write_by_user('alice', self.v2, '', 'diğer', text)

    async def test_pin_and_unpin_keep_the_line_where_it_is(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.")
        before = await self.lines()
        self.assertEqual([l['text'] for l in before], ["Ankara'da yaşar.", 'Kahveyi sever.'])  # newest on top
        coffee = next(l for l in before if l['text'].startswith('Kahve'))
        got = await mp.set_line_pinned('alice', self.v2, coffee['id'], True)
        self.assertEqual([(l['text'], l['pinned']) for l in got['lines']],
                         [('Kahveyi sever.', True), ("Ankara'da yaşar.", False)])
        got = await mp.set_line_pinned('alice', self.v2, coffee['id'], False)
        self.assertEqual([(l['text'], l['pinned']) for l in got['lines']],
                         [("Ankara'da yaşar.", False), ('Kahveyi sever.', False)])
        got = await mp.unpin_fact('alice', self.v2, coffee['fact_ids'][0])  # the fact-id way: still no loss
        self.assertEqual(len(got['lines']), 2)

    async def test_unpinning_a_compacted_line_keeps_it_and_a_new_fact_adds_one_line_only(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.", 'Kedisi var.')
        old = await self.lines()
        answer = {'lines': [{'text': 'Kahve, Ankara, kedi.', 'from': [0, 1, 2]}]}
        with patch.object(mx, '_call_model', AsyncMock(return_value=(json.dumps(answer), {}))):
            await mp.compact_profile('alice', self.v2)
        got = await self.lines()
        self.assertEqual([l['text'] for l in got], ['Kahve, Ankara, kedi.'])
        self.assertEqual(sorted(got[0]['fact_ids']), sorted(i for l in old for i in l['fact_ids']))  # the union
        line_id = got[0]['id']
        await mp.set_line_pinned('alice', self.v2, line_id, True)
        got = await mp.set_line_pinned('alice', self.v2, line_id, False)  # unpinning a compacted line: it stays
        self.assertEqual([l['text'] for l in got['lines']], ['Kahve, Ankara, kedi.'])
        await self.write('Çay içmez.')  # one new fact: ONE new line, none of the old ones back
        self.assertEqual([l['text'] for l in await self.lines()], ['Çay içmez.', 'Kahve, Ankara, kedi.'])

    async def test_a_removed_or_compacted_away_line_never_comes_back_by_itself(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.", 'Kedisi var.')
        by_text = {l['text']: l for l in await self.lines()}
        await mp.remove_line('alice', self.v2, by_text['Kedisi var.']['id'])  # x: the fact stays in memory ...
        self.assertEqual(await sql("select count(*) from orion_mem_fact where user_id = 'alice'"), 3)
        self.assertEqual(sorted(l['text'] for l in await self.lines()), ["Ankara'da yaşar.", 'Kahveyi sever.'])  # ... not here
        answer = {'lines': [{'text': 'Ankara.', 'from': [0]}]}  # the model drops the coffee line
        with patch.object(mx, '_call_model', AsyncMock(return_value=(json.dumps(answer), {}))):
            await mp.compact_profile('alice', self.v2)
        self.assertEqual([l['text'] for l in await self.lines()], ['Ankara.'])
        await self.write('Çay içmez.')
        self.assertEqual([l['text'] for l in await self.lines()], ['Çay içmez.', 'Ankara.'])

    async def test_pinned_lines_are_not_touched_by_compaction(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.")
        pin = next(l for l in await self.lines() if l['text'].startswith('Kahve'))
        await mp.set_line_pinned('alice', self.v2, pin['id'], True)
        seen = []

        async def fake(settings, request):
            seen.append(request[1]['content'])
            return json.dumps({'lines': [{'text': 'Ankara.', 'from': [0]}]}), {}
        with patch.object(mx, '_call_model', fake):
            await mp.compact_profile('alice', self.v2)
        self.assertNotIn('Kahve', seen[0])  # only the non-pinned lines go to the model
        self.assertEqual([(l['text'], l['pinned']) for l in await self.lines()],
                         [('Kahveyi sever.', True), ('Ankara.', False)])

    async def test_a_deleted_fact_leaves_its_line_unless_the_user_edited_it(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.", 'Kedisi var.')
        by_text = {l['text']: l for l in await self.lines()}
        await mp.edit_line('alice', self.v2, by_text['Kedisi var.']['id'], 'Kedisi Tarçın.')
        for text in ('Kahveyi sever.', 'Kedisi var.'):
            await me.remove_fact('alice', self.v2, by_text[text]['fact_ids'][0])
        got = await self.lines()
        self.assertEqual(sorted(l['text'] for l in got), ["Ankara'da yaşar.", 'Kedisi Tarçın.'])
        self.assertEqual(next(l for l in got if l['text'] == 'Kedisi Tarçın.')['fact_ids'], [])

    async def test_the_first_read_builds_the_list_from_the_old_state_once(self):
        await self.write('Kahveyi sever.', "Ankara'da yaşar.")
        await sql("insert into orion_mem_profile (user_id, pinned) values ('alice', $1::jsonb) on conflict (user_id) "
                  "do update set pinned = excluded.pinned, lines = null", json.dumps([{'text': 'Kedim Tarçın', 'fact_id': None}]))
        got = await self.lines()
        self.assertEqual([(l['text'], l['pinned']) for l in got],
                         [('Kedim Tarçın', True), ("Ankara'da yaşar.", False), ('Kahveyi sever.', False)])
        self.assertEqual([l['id'] for l in await self.lines()], [l['id'] for l in got])  # stable: not rebuilt

    async def test_the_profile_compact_route_answers_202_started(self):
        import inspect
        from orion.api import routes
        route = next(r for r in routes.router.routes if getattr(r, 'path', '') == '/api/v1/memory/profile/compact')
        self.assertEqual(route.status_code, 202)
        self.assertIn('memory_profile.start_compact', inspect.getsource(routes.memory_profile_compact))


if __name__ == "__main__":
    unittest.main()
