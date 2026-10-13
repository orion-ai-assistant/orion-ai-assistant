"""Language-specific transcription hints shared by REST and live STT."""
from faster_whisper.audio import decode_audio


LANGUAGE_PROMPTS = {
    "tr": (
        "Hmm... Naber? Şey, aslında şöyle desem... "
        "Bilgisayarı kapatalım, sonra da... "
        "Ama... Spotify'dan müzik açar mısın? "
        "Bir şey diyeceğim..."
    ),
    "en": (
        "Hmm... What's up? Well, actually, let me put it this way... "
        "Let's shut down the computer, and then... "
        "But... Could you play some music on Spotify? "
        "Let me say something..."
    ),
}


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
