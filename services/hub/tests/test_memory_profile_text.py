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
    def test_pinning_never_switches_the_compacted_list_for_the_raw_one(self):
        facts = [{"id": 1, "statement": "Kahveyi sever.", "category": "x"}, {"id": 2, "statement": "Ankara'da yaşar.", "category": "x"}]
        short = [{"id": "a1", "text": "Kahve ve Ankara."}]
        before = mp.compose([], facts, compacted=short)  # Under the limit, yet the stored short list is the profile.
        self.assertEqual([i["text"] for i in before["lines"]], ["Kahve ve Ankara."])
        self.assertEqual(before["lines"][0]["line_id"], "a1")
        after = mp.compose([{"text": "boşboşboş", "fact_id": 1, "state": "ok"}], facts, compacted=short)
        self.assertEqual([i["text"] for i in after["lines"]], ["boşboşboş", "Kahve ve Ankara."])
        self.assertFalse(after["needs_compaction"])
        self.assertEqual(mp._signature(facts), mp._signature(list(facts)))  # The set of facts alone, pins do not enter it.
        self.assertEqual(mp.compose([], facts, compacted=[])["lines"], [])  # All lines removed: still the (empty) list.

    def test_an_older_store_of_plain_strings_gets_ids(self):
        lines = mp._stored_lines({"sig": "x", "lines": ["Bir.", {"id": "z", "text": "İki."}]})
        self.assertEqual(lines, [{"id": "c0", "text": "Bir."}, {"id": "z", "text": "İki."}])

    def test_page_text_has_only_the_lines_and_the_prompt_has_the_wrapper(self):
        pinned = [{"text": "Kedim Tarçın", "fact_id": None, "state": "ok"}]
        block = mp.compose(pinned, [])
        self.assertEqual(block["text"], "- Kedim Tarçın")
        self.assertTrue(block["prompt"].startswith(mp.HEADER))
        self.assertNotIn("What you know", block["text"])
        self.assertTrue(all("why" not in line for line in block["lines"]))


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

    async def test_compacted_lines_can_be_edited_pinned_and_removed_and_pinning_keeps_the_list(self):
        import json
        await me.write_by_user('alice', self.v2, '', 'diğer', 'Kahveyi sever.')
        await me.write_by_user('alice', self.v2, '', 'diğer', "Ankara'da yaşar.")
        facts = await sql('select id from orion_mem_fact where user_id = $1 order by id', 'alice', fetch=True)
        lines = [{'id': 'a1', 'text': 'Kahve.'}, {'id': 'b2', 'text': 'Ankara.'}, {'id': 'c3', 'text': 'Üçüncü.'}]
        await sql("insert into orion_mem_profile (user_id, compacted) values ('alice', $1::jsonb) on conflict (user_id) do update "
                  "set compacted = excluded.compacted",
                  json.dumps({'sig': mp._signature([{'id': r['id']} for r in facts]), 'lines': lines}))
        got = await mp.get('alice', self.v2)
        self.assertEqual([(g['line_id'], g['text']) for g in got['generated']],
                         [('a1', 'Kahve.'), ('b2', 'Ankara.'), ('c3', 'Üçüncü.')])
        await mp.pin_fact('alice', self.v2, facts[0]['id'], 'boşboşboş')  # Pinning a fact: the short list stays.
        got = await mp.get('alice', self.v2)
        self.assertEqual([g['text'] for g in got['generated']], ['Kahve.', 'Ankara.', 'Üçüncü.'])
        await mp.edit_compacted('alice', self.v2, 'a1', 'Kahve çok.')
        got = await mp.remove_compacted('alice', self.v2, 'b2')
        self.assertEqual([g['text'] for g in got['generated']], ['Kahve çok.', 'Üçüncü.'])
        got = await mp.pin_compacted('alice', self.v2, 'c3')
        self.assertEqual([g['text'] for g in got['generated']], ['Kahve çok.'])
        self.assertIn('Üçüncü.', [p['text'] for p in got['pinned']])
        with self.assertRaises(LookupError):
            await mp.remove_compacted('alice', self.v2, 'zzz')
        self.assertEqual(await sql("select count(*) from orion_mem_fact where user_id = 'alice'"), 2)  # The facts stay.

    async def test_the_profile_compact_route_answers_202_started(self):
        import inspect
        from orion.api import routes
        route = next(r for r in routes.router.routes if getattr(r, 'path', '') == '/api/v1/memory/profile/compact')
        self.assertEqual(route.status_code, 202)
        self.assertIn('memory_profile.start_compact', inspect.getsource(routes.memory_profile_compact))


if __name__ == "__main__":
    unittest.main()
