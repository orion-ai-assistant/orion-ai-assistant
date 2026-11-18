"""Memory search speed (docs/memory-v2-durum.md, "Hız"): the changes that cut work per call must not change an answer.
No database: the connections are fakes that record what was sent."""
import random
import re
import unittest
from unittest.mock import patch

from orion.kernel import config, memory, registry


def reference_trigrams(text):
    grams = set()
    for word in re.findall(r"\w+", text):
        padded = f"  {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def reference_word_similarity(query, text):
    """memory.word_similarity as it was before its words' trigrams were remembered."""
    wanted = reference_trigrams(query)
    if not wanted:
        return 0.0
    words = re.findall(r"\w+", text)
    size = max(1, len(re.findall(r"\w+", query)))
    best = 0.0
    for start in range(max(1, len(words) - size + 1)):
        best = max(best, len(wanted & reference_trigrams(" ".join(words[start:start + size]))) / len(wanted))
        if best == 1.0:
            break
    return best


class WordSimilarityTests(unittest.TestCase):
    def test_same_answer_as_before(self):
        rng = random.Random(5)
        vocab = ["kahve", "toplantı", "kahvalti", "toplanti", "ev", "araba", "kedi", "çay", "doğum", "günü", "a", "bb", "ccc"]
        for _ in range(400):
            query = " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 3)))
            text = " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 12)))
            self.assertEqual(memory.word_similarity(query, text), reference_word_similarity(query, text), (query, text))


class FakeConn:
    def __init__(self, refuse=None):
        self.sent, self.refuse = [], refuse

    def transaction(self):
        conn = self

        class Scope:
            async def __aenter__(self):
                conn.sent.append("begin")

            async def __aexit__(self, *exc):
                conn.sent.append("end")
        return Scope()

    async def execute(self, sql, *args):
        self.sent.append(sql)
        if self.refuse and "iterative_scan" in sql:
            error = RuntimeError("unrecognized configuration parameter")
            error.sqlstate = self.refuse
            raise error

    async def fetch(self, sql, *args):
        self.sent.append("fetch")
        return []

    async def fetchval(self, sql, *args):
        return None


class AnnFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_found_out_once_then_one_statement(self):
        with patch.object(memory, "_iterative_scan", None):
            first, second = FakeConn(), FakeConn()
            await memory.ann_fetch(first, "select 1")
            await memory.ann_fetch(second, "select 1")
            self.assertTrue(any("iterative_scan" in s for s in first.sent))
            self.assertEqual(len([s for s in first.sent if s == "begin"]), 2)  # transaction + the savepoint
            self.assertEqual(len([s for s in second.sent if s == "begin"]), 1)  # no savepoint any more
            statements = [s for s in second.sent if s.startswith("select set_config")]
            self.assertEqual(len(statements), 1)
            self.assertIn("hnsw.ef_search", statements[0])
            self.assertIn("hnsw.iterative_scan", statements[0])
            self.assertIn("hnsw.max_scan_tuples", statements[0])
            self.assertEqual(second.sent[-2:], ["fetch", "end"])

    async def test_old_pgvector_never_asks_again(self):
        with patch.object(memory, "_iterative_scan", None):
            await memory.ann_fetch(FakeConn(refuse="42704"), "select 1")
            later = FakeConn(refuse="42704")
            await memory.ann_fetch(later, "select 1")
            self.assertFalse(any("iterative_scan" in s for s in later.sent))
            self.assertIs(memory._iterative_scan, False)

    async def test_other_failure_asks_again_next_time(self):
        with patch.object(memory, "_iterative_scan", None):
            await memory.ann_fetch(FakeConn(refuse="57014"), "select 1")  # a cancelled statement: says nothing about pgvector
            self.assertIsNone(memory._iterative_scan)


class SettingsReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_in_one_query_user_wins(self):
        asked = []

        class Conn(FakeConn):
            async def fetch(self, sql, *args):
                asked.append(args)
                return [{"user_id": config.SETTINGS_DEFAULT_USER, "key": "memory_v2", "value": "false"},
                        {"user_id": config.SETTINGS_DEFAULT_USER, "key": "memory_profile", "value": "false"},
                        {"user_id": "alice", "key": "memory_v2", "value": "true"}]

            async def close(self):
                asked.append("closed")

        async def connect(*a, **k):
            return Conn()
        with patch.object(registry, "_connect", connect), patch.object(registry, "_ensure_tables", lambda c: _noop()):
            found = await registry.fetch_setting_overrides_for([config.SETTINGS_DEFAULT_USER, "alice"])
            self.assertEqual(found, {config.SETTINGS_DEFAULT_USER: {"memory_v2": "false", "memory_profile": "false"},
                                     "alice": {"memory_v2": "true"}})
            self.assertEqual(len([a for a in asked if a != "closed"]), 1)
            with patch.object(config, "fetch_setting_overrides_for", registry.fetch_setting_overrides_for):
                merged = await config._get_effective_overrides("alice")
            self.assertIn(merged.get("memory_v2"), ("true", True))  # the user's value wins over the global one
            self.assertIn(merged.get("memory_profile"), ("false", False))  # a global one the user did not set stays

    async def test_no_database_is_empty_overrides(self):
        async def connect(*a, **k):
            return None
        with patch.object(registry, "_connect", connect):
            self.assertEqual(await registry.fetch_setting_overrides_for(["default", "bob"]), {"default": {}, "bob": {}})


async def _noop():
    return None


if __name__ == "__main__":
    unittest.main()
