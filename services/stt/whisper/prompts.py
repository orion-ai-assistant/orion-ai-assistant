"""Language-specific transcription hints shared by REST and live STT."""
import json
import os
from pathlib import Path
from faster_whisper.audio import decode_audio


CATALOG_PATH = Path(os.getenv(
    "WHISPER_LANGUAGE_CONFIG",
    str(Path(__file__).resolve().parents[2] / "hub/src/orion/api/ui/js/stt_languages.json"),
))
LANGUAGE_CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
LANGUAGE_PROMPTS = {code: entry["prompt"] for code, entry in LANGUAGE_CATALOG["languages"].items()}
WHISPER_LANGUAGES = {code: entry["name"] for code, entry in LANGUAGE_CATALOG["languages"].items()}


def select_transcription_prompt(model, audio, language=None, prompt=None,
                                context="", context_language=None):
    """Detect auto language before choosing a hint; explicit hints take priority.

    Re-detect each audio buffer so auto sessions can switch languages. Supply
    the detected language to transcribe to avoid a second language detection.
    Never carry context from a different language into the current hint.
    """
    language = (language or "").strip().lower()
    if language in ("", "auto", "none"):
        language = None
    custom_prompt = (prompt or "").strip()
    if language is None and not custom_prompt:
        if isinstance(audio, str):
            audio = decode_audio(audio, sampling_rate=16000)
        language, _, _ = model.detect_language(audio=audio)
    selected = custom_prompt or LANGUAGE_PROMPTS.get(language, "")
    if context and context_language == language:
        selected = f"{selected} {context[-200:]}".strip()
    return language, selected or None
