import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pydantic import ValidationError
from orion.contracts.settings import RuntimeSettings


class SttLanguageSettingsTests(unittest.TestCase):
    def test_unset_and_seeded_empty_language_keep_browser_default(self):
        self.assertIsNone(RuntimeSettings().stt_language)
        self.assertIsNone(RuntimeSettings(stt_language="").stt_language)
        self.assertIsNone(RuntimeSettings(stt_language=None).stt_language)

    def test_saved_auto_is_distinct_from_unset(self):
        for value in ("auto", "none", "AUTO"):
            self.assertEqual(RuntimeSettings(stt_language=value).stt_language, "auto")

    def test_language_codes_and_browser_aliases_are_normalized(self):
        for value, expected in (("tr-TR", "tr"), ("EN_us", "en"), ("fil-PH", "tl"), ("nb", "no")):
            self.assertEqual(RuntimeSettings(stt_language=value).stt_language, expected)

    def test_unknown_language_is_rejected(self):
        with self.assertRaises(ValidationError):
            RuntimeSettings(stt_language="xx")

    def test_settings_accept_every_language_in_the_single_json(self):
        path = Path(__file__).resolve().parents[1] / "src/orion/api/ui/js/stt_languages.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        for code in catalog["languages"]:
            self.assertEqual(RuntimeSettings(stt_language=code).stt_language, code)
