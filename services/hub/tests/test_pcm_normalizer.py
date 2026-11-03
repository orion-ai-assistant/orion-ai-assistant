"""Speech at any rate becomes clean 24 kHz mono (pcm_normalizer.py): the voice kept, nothing above the lower
Nyquist folded in or imaged out (the hiss the old straight-line interpolation made), no click at a chunk
boundary or at the start and end of a sentence, 24 kHz untouched; a whole WAV the same as a stream."""
import io
import wave

import numpy as np
import pytest

from orion.worker.services.pcm_normalizer import PcmNormalizer
from orion.worker.services.tts_stream import _batch_wav_pcm

RATES = (8000, 16000, 22050, 32000, 44100, 48000)


def tone(freq, rate, seconds=1.0, level=10000):
    t = np.arange(int(seconds * rate)) / rate
    return (level * np.sin(2 * np.pi * freq * t)).astype('<i2')


def convert(x, rate, pieces=None, channels=1):
    n = PcmNormalizer(rate, channels)
    data = x.tobytes()
    if not pieces:
        return np.frombuffer(n.feed(data, final=True), dtype='<i2')
    out, at = [], 0
    for size in pieces:
        out.append(n.feed(data[at:at + size]))
        at += size
    out.append(n.feed(data[at:], final=True))
    return np.frombuffer(b''.join(out), dtype='<i2')


def spectrum_db(y, freq):
    """Level of the strongest component near freq, in dB against full scale 10000."""
    y = y[2400:-2400].astype(np.float64)
    s = np.abs(np.fft.rfft(y * np.hanning(len(y)))) / (len(y) / 4)
    f = np.fft.rfftfreq(len(y), 1 / 24000)
    band = (f > freq - 150) & (f < freq + 150)
    return 20 * np.log10(s[band].max() / 10000 + 1e-12)


@pytest.mark.parametrize('rate', RATES)
def test_the_voice_comes_through_unchanged(rate):
    for freq in (200, 1000, 3000, min(7000, int(rate * 0.35))):
        y = convert(tone(freq, rate), rate)
        assert len(y) == rate * 24000 // rate
        expected = tone(freq, 24000).astype(np.float64)
        error = (y[480:-480] - expected[480:-480])
        snr = 10 * np.log10(np.mean(expected[480:-480] ** 2) / np.mean(error ** 2))
        assert snr > 60, (rate, freq, snr)  # Same frequency, level and phase; noise 60 dB under the voice.


@pytest.mark.parametrize('rate', (32000, 44100, 48000))
def test_going_down_nothing_above_12k_folds_back_as_hiss(rate):
    for freq in (12500, 14000, 18000, rate // 2 - 500):
        y = convert(tone(freq, rate), rate)
        alias = abs(rate - freq) if freq > 12000 else freq  # Where a plain interpolation puts it.
        alias = alias if alias < 12000 else 24000 - alias
        level = 20 * np.log10(np.sqrt(np.mean(y[480:-480].astype(np.float64) ** 2)) / (10000 / np.sqrt(2)) + 1e-12)
        assert level < -60, (rate, freq, alias, level)


@pytest.mark.parametrize('rate', (8000, 16000, 22050))
def test_going_up_no_mirror_images_are_added(rate):
    y = convert(tone(3000, rate), rate)
    image = rate - 3000  # A straight-line interpolation leaves an echo of 3 kHz here,
    image = image if image < 12000 else 24000 - image  # folded back below 12 kHz.
    assert spectrum_db(y, image) < -60, (rate, spectrum_db(y, image))
    assert spectrum_db(y, 3000) > -1


@pytest.mark.parametrize('rate', RATES)
def test_chunk_boundaries_change_nothing(rate):
    rng = np.random.default_rng(1)
    x = (tone(440, rate) // 2 + tone(2500, rate) // 3 + rng.integers(-500, 500, rate)).astype('<i2')
    whole = convert(x, rate)
    assert np.array_equal(convert(x, rate, [1, 3, 4096, 7, 333, 64, 9999, 2]), whole)
    assert np.array_equal(convert(x, rate, [2] * 2000), whole)


@pytest.mark.parametrize('rate', RATES)
def test_no_click_at_the_start_or_end_of_a_sentence(rate):
    for value in (0, 12000, -20000):
        y = convert(np.full(rate // 10, value, dtype='<i2'), rate)
        assert len(y) == (rate // 10) * 24000 // rate and np.all(y == value)


def test_24k_mono_is_untouched_and_stereo_is_averaged():
    x = np.random.default_rng(2).integers(-30000, 30000, 4801).astype('<i2')
    assert np.array_equal(convert(x, 24000), x)
    stereo = np.stack([x, np.zeros_like(x)], axis=1).reshape(-1)
    assert np.array_equal(convert(stereo, 24000, channels=2), np.round(x / 2).astype('<i2'))
    y = convert(np.stack([tone(1000, 48000), tone(1000, 48000)], axis=1).reshape(-1), 48000, channels=2)
    assert np.array_equal(y, convert(tone(1000, 48000), 48000))


@pytest.mark.parametrize('rate', (16000, 44100, 48000))
def test_a_whole_wav_is_the_same_as_the_stream(rate):
    x = tone(700, rate)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(x.tobytes())
    assert _batch_wav_pcm(buffer.getvalue()) == convert(x, rate).tobytes()


def test_a_long_sentence_is_fast_enough():
    import time
    x = tone(440, 48000, seconds=30)
    t = time.perf_counter()
    convert(x, 48000, [9600] * 150)
    assert time.perf_counter() - t < 3  # 30 s of 48 kHz audio; playback must never wait on this.
