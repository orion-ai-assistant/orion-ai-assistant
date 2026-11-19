import json
import unittest

from orion.kernel import memory, memory_compact


class UsageOfTest(unittest.TestCase):
    def test_reads_the_usage_stored_on_an_assistant_message(self):
        raw = json.dumps({"role": "assistant", "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "at": "2026-11-19T10:00:00+00:00"}})
        self.assertEqual(memory_compact.usage_of(raw),
                         {"prompt_tokens": 1200, "completion_tokens": 80, "at": "2026-11-19T10:00:00+00:00"})

    def test_no_usage_or_bad_json_is_none(self):
        self.assertIsNone(memory_compact.usage_of(json.dumps({"role": "assistant"})))
        self.assertIsNone(memory_compact.usage_of("{broken"))
        self.assertIsNone(memory_compact.usage_of(None))


if __name__ == "__main__":
    unittest.main()


class ContextLengthTest(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, models, group="g", setting=0):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        settings = SimpleNamespace(router_model_group=group, chat_context_tokens=setting)
        with patch("orion.kernel.router_models.get_router_catalog", AsyncMock(return_value={"models": models})):
            return await memory_compact.context_length(settings)

    async def test_catalog_value_wins_over_the_setting(self):
        models = [{"name": "g", "capability": "chat", "context_length": 200000}]
        self.assertEqual(await self.resolve(models, setting=32000), 200000)

    async def test_unknown_in_the_catalog_falls_back_to_the_setting(self):
        models = [{"name": "g", "capability": "chat", "context_length": None}]
        self.assertEqual(await self.resolve(models, setting=32000), 32000)
        self.assertEqual(await self.resolve([], setting=0), 128000)  # Nothing known (or the Router is down): 128000.


class CatalogContextLengthTest(unittest.TestCase):
    def test_group_context_is_its_smallest_and_unknown_if_any_unknown(self):
        from orion.kernel.router_models import _group_context
        sizes = {"a": 1000, "b": 400, "c": None}
        group = lambda *names: {"items": [{"name": n, "capability": "chat"} for n in names]}
        self.assertEqual(_group_context(group("a", "b"), sizes), 400)
        self.assertIsNone(_group_context(group("a", "c"), sizes))
        self.assertIsNone(_group_context(group(), sizes))


class BreakdownTest(unittest.TestCase):
    def test_parts_are_scaled_to_the_real_prompt_tokens_and_add_up(self):
        got = memory_compact.scale_breakdown({"system": 100, "profile": 50, "tools": 300, "messages": 500, "tool_results": 50}, 3800)
        self.assertEqual(sum(got.values()), 3800)
        self.assertEqual(set(got), {"system", "profile", "tools", "messages", "tool_results"})
        self.assertGreater(got["messages"], got["tools"])
        self.assertIsNone(memory_compact.scale_breakdown({}, 3800))  # Nothing estimated.
        self.assertIsNone(memory_compact.scale_breakdown({"system": 10}, 0))  # No real count.

    def test_a_tool_results_count_stays_the_same_while_the_chat_grows(self):
        results = []
        for turn in range(1, 6):  # One tool call early on, then only talk: the real total grows faster than the estimate.
            parts = {"system": 100, "profile": 50, "tools": 300, "tool_results": 80, "messages": 200 * turn}
            got = memory_compact.scale_breakdown(parts, 700 + 330 * turn)
            self.assertEqual(sum(got.values()), 700 + 330 * turn)
            results.append(got["tool_results"])
        self.assertEqual(set(results), {80})

    def test_request_parts_split_system_profile_tools_messages_and_tool_results(self):
        messages = [{"role": "system", "content": "s" * 400},
                    {"role": "user", "content": "u" * 40},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "r" * 400},
                    {"role": "user", "content": [{"type": "text", "text": "t" * 40}, {"type": "image_url", "image_url": {}}]}]
        parts = memory_compact.request_parts(messages, "a" * 200, "b" * 100, 77)
        self.assertEqual((parts["tools"], parts["system"], parts["profile"]),
                         (77, memory.estimate_tokens("a" * 200), memory.estimate_tokens("b" * 100)))
        self.assertEqual(parts["tool_results"], memory.estimate_tokens("r" * 400))
        self.assertGreater(parts["messages"], memory.estimate_tokens("u" * 40) + memory.estimate_tokens("t" * 40) - 1)

    def test_a_stored_breakdown_comes_back_with_the_usage(self):
        raw = json.dumps({"role": "assistant", "usage": {"prompt_tokens": 10, "completion_tokens": 1, "breakdown": {"system": 10}}})
        self.assertEqual(memory_compact.usage_of(raw)["breakdown"], {"system": 10})


class UsedOfTest(unittest.TestCase):
    def test_the_last_answers_real_count_is_the_size(self):
        last = {"prompt_tokens": 3800, "completion_tokens": 187, "breakdown": {"system": 1}}
        self.assertEqual(memory_compact.used_of(last, 50, None, 0, 295), (3987, False, {"system": 1}))
        self.assertEqual(memory_compact.used_of({**last, "estimated": True}, 50, None, 0, 295)[1], True)

    def test_after_a_compaction_it_is_an_estimate_until_the_next_answer(self):
        last = {"prompt_tokens": 90000, "completion_tokens": 100}
        self.assertEqual(memory_compact.used_of(last, 50, 60, 700, 120), (820, True, None))  # Answer older than the summary.
        self.assertEqual(memory_compact.used_of(last, 61, 60, 700, 120)[0], 90100)  # A newer answer: real again.
        self.assertEqual(memory_compact.used_of(None, None, None, 0, 295), (295, True, None))


from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.kernel import registry
from test_memory_search import PostgresMemoryCase


async def sql(query, *args, fetch=False):
    conn = await registry._connect()
    try:
        return await (conn.fetch if fetch else conn.fetchval)(query, *args)
    finally:
        await conn.close()


class ContextMeterTests(PostgresMemoryCase):
    """state(), the idle check and the force check on a real PostgreSQL."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await sql('truncate orion_mem_chat_state, orion_chat_messages cascade')
        await sql("insert into orion_chats(id, user_id, title, assistant) values ('c1', 'alice', 'A', true), "
                  "('c3', 'alice', 'Normal', false) on conflict (id) do update set assistant = excluded.assistant")
        self.settings = SimpleNamespace(memory_v2=True, router_model_group='g', chat_context_tokens=1000,
                                        chat_compact_idle_percent=40, chat_compact_idle_minutes=30, chat_compact_force_percent=90)
        for target, name, value in ((memory_compact, 'context_length', AsyncMock(return_value=1000)),
                                    (memory_compact, '_idle_checked', {}),
                                    ('orion.kernel.config.get_runtime_settings', None, AsyncMock(return_value=self.settings))):
            guard = patch.object(target, name, value) if name else patch(target, value)
            guard.start()
            self.addCleanup(guard.stop)

    async def answer(self, chat='c1', prompt=3800, completion=187, **extra):
        await registry.insert_messages(chat, [{'role': 'user', 'content': 'merhaba'},
                                              {'role': 'assistant', 'content': 'selam', 'usage': {
                                                  'prompt_tokens': prompt, 'completion_tokens': completion, 'at': 'x', **extra}}])
        return await sql('select max(id) from orion_chat_messages where chat_id = $1', chat)

    async def test_used_tokens_is_the_last_answers_real_usage_not_the_estimate_of_the_messages(self):
        await self.answer(breakdown={'system': 300, 'profile': 0, 'tools': 1000, 'messages': 2500, 'tool_results': 187})
        got = await memory_compact.state('alice', 'c1')
        self.assertEqual((got['used_tokens'], got['used_estimated']), (3987, False))
        self.assertLess(got['tokens_since'], 50)  # The old meter showed this small number.
        self.assertEqual(got['breakdown']['tools'], 1000)
        self.assertEqual(got['last_usage']['prompt_tokens'], 3800)

    async def test_after_a_compaction_the_size_is_an_estimate_until_the_next_answer(self):
        last = await self.answer()
        await sql("insert into orion_mem_chat_state (user_id, chat_id, summary, compacted_upto, compacted_at) "
                  "values ('alice', 'c1', $1, $2, now())", 'özet ' * 100, last)
        got = await memory_compact.state('alice', 'c1')
        self.assertEqual((got['used_estimated'], got['breakdown']), (True, None))
        self.assertEqual(got['used_tokens'], got['summary_tokens'] + got['tokens_since'])
        self.assertLess(got['used_tokens'], 500)
        await self.answer(prompt=700, completion=30)
        got = await memory_compact.state('alice', 'c1')
        self.assertEqual((got['used_tokens'], got['used_estimated']), (730, False))

    async def test_idle_check_compacts_only_an_idle_chat_past_the_idle_percent(self):
        await self.answer(prompt=450, completion=10)  # 46 % of 1000.
        with patch.object(memory_compact, 'start') as start:
            self.assertEqual(await memory_compact.idle_check(), 0)  # Just spoken: not idle yet.
            await sql("update orion_chat_messages set created_at = now() - interval '40 minutes'")
            self.assertEqual(await memory_compact.idle_check(), 1)
            start.assert_called_once_with('alice', 'c1', self.settings)
            self.assertEqual(await memory_compact.idle_check(), 0)  # Judged once per last message.

    async def test_idle_check_leaves_a_small_chat_and_normal_chats_alone(self):
        await self.answer(prompt=100, completion=10)  # 11 %.
        await self.answer(chat='c3', prompt=900, completion=10)  # Not the assistant chat.
        await sql("update orion_chat_messages set created_at = now() - interval '2 hours'")
        with patch.object(memory_compact, 'start') as start:
            self.assertEqual(await memory_compact.idle_check(), 0)
            start.assert_not_called()

    async def test_force_before_send_compacts_at_the_force_percent_and_not_below(self):
        await self.answer(prompt=900, completion=20)  # 92 %.
        with patch.object(memory_compact, 'compact', AsyncMock(return_value={})) as compact:
            self.assertTrue(await memory_compact.force_before_send('alice', 'c1', self.settings))
            compact.assert_awaited_once()
        await self.answer(prompt=500, completion=20)  # 52 %: idle territory, not forced.
        with patch.object(memory_compact, 'compact', AsyncMock(return_value={})) as compact:
            self.assertFalse(await memory_compact.force_before_send('alice', 'c1', self.settings))
            compact.assert_not_awaited()
        with patch.object(memory_compact, 'compact', AsyncMock(side_effect=RuntimeError('model down'))):
            await self.answer(prompt=980, completion=1)
            self.assertFalse(await memory_compact.force_before_send('alice', 'c1', self.settings))  # Never breaks the chat.
