"""VoxCPM2 on llama.cpp-omni's llama-tts-server (services/tts/engines.py VoxCPM2GGUFEngine): what a cloned voice
keeps and what each request sends; no server is started."""
import base64
import io
import sys
import wave
from pathlib import Path

import numpy as np
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


def test_a_clone_keeps_its_wav_and_it_alone_goes_with_every_request(tmp_path):
    voice = engine().encode_voice(sample(tmp_path, 4.5), ' Merhaba, bu bir deneme. ')
    assert voice['ref_seconds'] == 4.5 and 'ref_text' not in voice  # The transcript is not used.
    payload = engine()._payload('Günaydın.', voice, '', 2.0, 42)
    assert base64.b64decode(payload['reference_audio']) == voice['ref_wav']
    assert 'prompt_text' not in payload and payload['input'] == 'Günaydın.'
    assert payload['seed'] == 42 and payload['cfg_value'] == 2.0 and payload['model'] == 'voxcpm'
    with wave.open(io.BytesIO(voice['ref_wav'])) as w:
        assert w.getframerate() == 16000  # Sent as it was given: the server takes any sample rate.


def test_no_clone_no_reference_and_a_description_designs_the_voice(tmp_path):
    payload = engine()._payload(' Günaydın. ', None, 'A calm, deep male voice', None, -1)
    assert payload['input'] == '(A calm, deep male voice)Günaydın.' and 'reference_audio' not in payload
    assert payload['seed'] == engines.DEFAULT_SEED  # A description without a seed: the default seed, as any voice.
    # A model name is not a voice description (here, without a server, the default voice's own design stands in).
    assert 'voxcpm2-gguf' not in engine()._payload('Merhaba.', None, 'voxcpm2-gguf', 2.0, -1)['input']


def test_what_a_request_leaves_out_comes_from_the_one_place_the_defaults_are_written(tmp_path):
    voice = engine().encode_voice(sample(tmp_path, 4.5), '')
    for seed in (None, -1):  # The Router sends -1 when the request has no seed.
        payload = engine()._payload('Günaydın.', voice, '', None, seed)
        assert payload['seed'] == engines.DEFAULT_SEED == 32  # The same voice from request to request.
        assert payload['cfg_value'] == engines.DEFAULT_GUIDANCE == 3.0
        assert payload['inference_timesteps'] == engines.DEFAULT_STEPS == 15
        assert payload['temperature'] == engines.DEFAULT_TEMPERATURE == 0.85
    assert engines.VOXCPM2_DEFAULTS == {'creativity': 1.0, 'steps': 15, 'temperature': 0.85, 'seed': 32}
    asked = engine()._payload('Günaydın.', voice, '', 2.5, 7, steps=20, temperature=0.6)
    assert (asked['seed'], asked['cfg_value'], asked['inference_timesteps'], asked['temperature']) == (7, 2.5, 20, 0.6)


def test_a_clone_follows_the_same_length_rule_and_wants_a_wav(tmp_path):
    with pytest.raises(ValueError) as refused:
        engine().encode_voice(sample(tmp_path, 21), '')
    assert 'en çok 20 saniye' in str(refused.value)
    mp3 = tmp_path / 'sample.mp3'
    mp3.write_bytes(b'ID3not a wav')
    with pytest.raises(ValueError) as refused:
        engine().encode_voice(str(mp3), '')
    assert 'WAV' in str(refused.value)


def tone(freq, seconds=1.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    return (10000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def halved(x, pieces):
    """x through HalfRate in the given byte sizes (odd ones too), as a stream arrives."""
    half, data, out, at = engines.HalfRate(), x.tobytes(), [], 0
    for size in pieces:
        out.append(half.feed(data[at:at + size]))
        at += size
    out.append(half.feed(data[at:]))
    out.append(half.flush())
    return np.concatenate(out)


def level(y):
    return np.sqrt(np.mean(y[200:-200].astype(np.float64) ** 2))


def test_48k_is_halved_with_the_voice_kept_and_nothing_above_12k_folded_in():
    for freq in (440, 3000, 8000):
        y = halved(tone(freq), [])
        assert len(y) == 24000 and abs(level(y) / level(tone(freq)) - 1) < 0.02
    for freq in (14000, 18000, 23000):  # Unfiltered, these would come back as 10, 6 and 1 kHz at full level.
        assert level(halved(tone(freq), [])) < level(tone(freq)) * 0.001


def test_the_pieces_join_seamlessly_whatever_their_size():
    x = (tone(440) + tone(5000) // 2).astype(np.int16)
    whole = halved(x, [])
    assert np.array_equal(halved(x, [1, 3, 4096, 7, 333, 64, 9999]), whole)
    assert np.array_equal(halved(x, [2] * 3000), whole)
    # In line with the input (no delay): sample i of the output is input sample 2i, filtered.
    assert np.abs(whole[100:-100].astype(int) - x[200:-200:2].astype(int)).max() < 200


def test_a_stream_shorter_than_the_filter_still_halves():
    assert len(halved(tone(440, seconds=20 / 48000), [5])) == 10
    assert len(halved(np.array([], dtype=np.int16), [])) == 0
