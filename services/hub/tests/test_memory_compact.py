import json
import unittest

from orion.kernel import memory_compact


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
        self.assertEqual(await self.resolve([], setting=0), 0)


class CatalogContextLengthTest(unittest.TestCase):
    def test_group_context_is_its_smallest_and_unknown_if_any_unknown(self):
        from orion.kernel.router_models import _group_context
        sizes = {"a": 1000, "b": 400, "c": None}
        group = lambda *names: {"items": [{"name": n, "capability": "chat"} for n in names]}
        self.assertEqual(_group_context(group("a", "b"), sizes), 400)
        self.assertIsNone(_group_context(group("a", "c"), sizes))
        self.assertIsNone(_group_context(group(), sizes))
