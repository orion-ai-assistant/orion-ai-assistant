"""VoxCPM2 on llama.cpp-omni's llama-tts-server (services/tts/engines.py VoxCPM2GGUFEngine): what a cloned voice
keeps and what each request sends; no server is started."""
import base64
import io
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tts'))
import engines  # noqa: E402


def sample(tmp_path, seconds, rate=16000):
    path = tmp_path / 'sample.wav'
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(b'\0\0' * int(seconds * rate))
    return str(path)


def engine():
    return engines.VoxCPM2GGUFEngine.__new__(engines.VoxCPM2GGUFEngine)  # Its server is not started.


def test_a_clone_keeps_its_wav_and_words_and_both_go_with_every_request(tmp_path):
    voice = engine().encode_voice(sample(tmp_path, 4.5), ' Merhaba, bu bir deneme. ')
    assert voice['ref_seconds'] == 4.5 and voice['ref_text'] == 'Merhaba, bu bir deneme.'
    payload = engine()._payload('Günaydın.', voice, '', 2.0, 42)
    assert base64.b64decode(payload['reference_audio']) == voice['ref_wav']
    assert payload['prompt_text'] == 'Merhaba, bu bir deneme.' and payload['input'] == 'Günaydın.'
    assert payload['seed'] == 42 and payload['cfg_value'] == 2.0 and payload['model'] == 'voxcpm'
    with wave.open(io.BytesIO(voice['ref_wav'])) as w:
        assert w.getframerate() == 16000  # Sent as it was given: the server takes any sample rate.


def test_no_clone_no_reference_and_a_description_designs_the_voice(tmp_path):
    payload = engine()._payload(' Günaydın. ', None, 'A calm, deep male voice', None, -1)
    assert payload['input'] == '(A calm, deep male voice)Günaydın.' and 'reference_audio' not in payload
    assert 'seed' not in payload and 'cfg_value' not in payload
    assert engine()._payload('Merhaba.', None, 'voxcpm2-gguf', 2.0, -1)['input'] == 'Merhaba.'  # A model name, not a voice.


def test_a_clone_follows_the_same_length_rule_and_wants_a_wav(tmp_path):
    with pytest.raises(ValueError) as refused:
        engine().encode_voice(sample(tmp_path, 21), '')
    assert 'en çok 20 saniye' in str(refused.value)
    mp3 = tmp_path / 'sample.mp3'
    mp3.write_bytes(b'ID3not a wav')
    with pytest.raises(ValueError) as refused:
        engine().encode_voice(str(mp3), '')
    assert 'WAV' in str(refused.value)
