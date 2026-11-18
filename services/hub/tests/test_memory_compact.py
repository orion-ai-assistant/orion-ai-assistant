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
