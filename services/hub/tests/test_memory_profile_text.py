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


if __name__ == "__main__":
    unittest.main()
