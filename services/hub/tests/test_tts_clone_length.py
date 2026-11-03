"""A cloned voice's sample is processed with every sentence, so its length is checked when it is added
(services/tts/engines.py: rvq_seconds, check_clone_seconds, OmniVoiceGGUFEngine.encode_voice)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tts'))
import engines  # noqa: E402


def fake_engine(tmp_path, monkeypatch, frames):
    """An OmniVoice engine whose codec writes `frames` frames of codes (8 codebooks x 11 bits, no header)."""
    codec = tmp_path / ('omnivoice-codec.exe' if os.name == 'nt' else 'omnivoice-codec')
    codec.write_bytes(b'')
    engine = engines.OmniVoiceGGUFEngine.__new__(engines.OmniVoiceGGUFEngine)
    engine.codec_bin, engine.codec_path, engine.server_bin = str(codec), 'tokenizer.gguf', str(codec)

    def run(cmd, **kwargs):
        wav = Path(cmd[cmd.index('-i') + 1])
        wav.with_suffix('.rvq').write_bytes(b'\0' * (frames * 88 // 8))
    monkeypatch.setattr(engines.subprocess, 'run', run)
    sample = tmp_path / 'sample.wav'
    sample.write_bytes(b'RIFF')
    return engine, str(sample)


def test_the_length_comes_from_the_codes():
    assert engines.rvq_seconds(b'\0' * 4697) == 17.08  # zephyr2: 427 frames, 25 a second.
    assert engines.rvq_seconds(b'') == 0


def test_a_short_sample_is_kept_and_says_nothing(tmp_path, monkeypatch):
    engine, sample = fake_engine(tmp_path, monkeypatch, frames=125)
    voice = engine.encode_voice(sample, 'Merhaba.')
    assert voice['ref_seconds'] == 5.0 and voice['ref_text'] == 'Merhaba.' and len(voice['rvq_bytes']) == 1375
    assert engines.clone_advice(voice['ref_seconds']) == ''


def test_a_longer_sample_is_kept_with_advice(tmp_path, monkeypatch):
    engine, sample = fake_engine(tmp_path, monkeypatch, frames=427)
    voice = engine.encode_voice(sample, '')
    advice = engines.clone_advice(voice['ref_seconds'])
    assert voice['ref_seconds'] == 17.08 and '17.1 saniye' in advice and 'yaklaşık 5 saniyelik' in advice


def test_a_sample_past_the_limit_is_refused_with_the_reason(tmp_path, monkeypatch):
    engine, sample = fake_engine(tmp_path, monkeypatch, frames=engines.CLONE_MAX_SECONDS * 25 + 25)
    with pytest.raises(ValueError) as refused:
        engine.encode_voice(sample, '')
    assert 'en çok 20 saniye' in str(refused.value) and 'yavaşlatır' in str(refused.value)
    engines.check_clone_seconds(engines.CLONE_MAX_SECONDS)  # The limit itself is allowed.
