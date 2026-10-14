import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

spec = importlib.util.spec_from_file_location(
    "whisper_prompts_under_test", Path(__file__).resolve().parents[1] / "prompts.py"
)
prompts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompts)


class PromptTests(unittest.TestCase):
    def test_aliases_and_region_codes_are_owned_by_stt(self):
        model = Mock()
        for language, code in (("nb-NO", "no"), ("fil-PH", "tl"), ("TR_tr", "tr")):
            self.assertEqual(prompts.select_transcription_prompt(model, None, language),
                             (code, prompts.LANGUAGE_PROMPTS[code]))
        model.detect_language.assert_not_called()

    def test_requested_language_selects_hint_without_detection(self):
        model = Mock()
        for language in ("tr", "en"):
            actual = prompts.select_transcription_prompt(model, None, language.upper())
            self.assertEqual(actual, (language, prompts.LANGUAGE_PROMPTS[language]))
        model.detect_language.assert_not_called()

    def test_auto_buffers_can_switch_language_without_carrying_old_context(self):
        model = Mock()
        model.detect_language.side_effect = [
            ("tr", .99, []), ("en", .99, []),
        ]
        audio = np.ones(16000, dtype=np.float32)
        language, hint = prompts.select_transcription_prompt(model, audio)
        self.assertEqual(hint, prompts.LANGUAGE_PROMPTS["tr"])
        language, hint = prompts.select_transcription_prompt(
            model, audio, "auto", context="Türkçe önceki konuşma", context_language=language,
        )
        self.assertEqual(language, "en")
        self.assertEqual(hint, prompts.LANGUAGE_PROMPTS["en"])
        self.assertEqual(model.detect_language.call_count, 2)

    def test_none_and_empty_language_detect_before_selecting_hint(self):
        audio = np.ones(16000, dtype=np.float32)
        for language in (None, "", "none", "auto"):
            model = Mock()
            model.detect_language.return_value = ("en", .99, [])
            self.assertEqual(prompts.select_transcription_prompt(model, audio, language),
                             ("en", prompts.LANGUAGE_PROMPTS["en"]))

    def test_custom_prompt_overrides_default_and_preserves_auto(self):
        model = Mock()
        self.assertEqual(prompts.select_transcription_prompt(model, None, "tr", " Özel isimler "),
                         ("tr", "Özel isimler"))
        self.assertEqual(prompts.select_transcription_prompt(model, None, None, "Custom terms"),
                         (None, "Custom terms"))
        model.detect_language.assert_not_called()

    def test_all_whisper_languages_have_a_localized_hint(self):
        from faster_whisper.tokenizer import _LANGUAGE_CODES
        self.assertEqual(set(prompts.LANGUAGE_PROMPTS), set(_LANGUAGE_CODES))
        self.assertEqual(len(prompts.LANGUAGE_PROMPTS), 100)
        for language, hint in prompts.LANGUAGE_PROMPTS.items():
            self.assertTrue(hint.strip())
            self.assertIn("Spotify", hint)
            self.assertEqual(prompts.select_transcription_prompt(Mock(), None, language), (language, hint))
        self.assertEqual(len(set(prompts.LANGUAGE_PROMPTS.values())), 100)

    def test_unknown_language_has_no_default_hint(self):
        self.assertEqual(prompts.select_transcription_prompt(Mock(), None, "xx"), ("xx", None))

    def test_rest_file_is_decoded_for_language_detection(self):
        audio = np.ones(16000, dtype=np.float32)
        model = Mock()
        model.detect_language.return_value = ("tr", .99, [])
        with patch.object(prompts, "decode_audio", return_value=audio) as decode:
            language, hint = prompts.select_transcription_prompt(model, "upload.wav")
        decode.assert_called_once_with("upload.wav", sampling_rate=16000)
        model.detect_language.assert_called_once_with(audio=audio)
        self.assertEqual(language, "tr")
        self.assertEqual(hint, prompts.LANGUAGE_PROMPTS["tr"])
