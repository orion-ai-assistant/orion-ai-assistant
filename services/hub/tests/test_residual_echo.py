import unittest

import numpy as np
from scipy.signal import butter, sosfilt

from orion.audio.echo_cancel.stream import DeviceStream, HOP
from orion.audio.residual_echo import ResidualEchoSuppressor

RATE = 16000


def speechlike(seconds, seed, low=200, high=3500):
    """Band-limited noise with a syllable-rate envelope, like a video's voice."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    noise = sosfilt(butter(4, [low, high], btype="band", fs=RATE, output="sos"), rng.normal(0, 1, n))
    envelope = .2 + np.abs(np.sin(np.arange(n) * 2 * np.pi * 3 / RATE + rng.uniform(0, 3)))
    x = noise * envelope
    return (x / np.sqrt(np.mean(x ** 2)) * .1).astype(np.float32)


def delayed(x, samples):
    return np.concatenate([np.zeros(samples, np.float32), x[:len(x) - samples]])


def band_db(x, low, high):
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    return 10 * np.log10(np.sum(spectrum[(f >= low) & (f < high)]) + 1e-20)


class ResidualEchoTests(unittest.TestCase):
    def test_finds_the_echo_delay(self):
        ref = speechlike(6, 1)
        mic = .3 * delayed(ref, 2900) + np.random.default_rng(2).normal(0, 1e-4, len(ref)).astype(np.float32)
        s = ResidualEchoSuppressor()
        s.process(mic * .01, mic, ref)  # the canceller left 1% of the echo
        self.assertIsNotNone(s.delay)
        self.assertLess(abs(s.delay - 2900), 64)
        self.assertIn("delay 18", s.summary())  # ~181 ms

    def test_follows_a_moved_delay(self):
        ref = speechlike(14, 3)
        mic = np.concatenate([.3 * delayed(ref, 1600)[:6 * RATE], .3 * delayed(ref, 4000)[6 * RATE:]])
        s = ResidualEchoSuppressor()
        s.process(mic * .01, mic, ref)
        self.assertLess(abs(s.delay - 4000), 64)

    def test_silent_reference_passes_the_output_unchanged(self):
        out = np.random.default_rng(4).normal(0, .1, 4 * RATE).astype(np.float32)
        s = ResidualEchoSuppressor()
        result = s.process(out, out, np.zeros_like(out))
        np.testing.assert_allclose(result[s.latency:], out[:-s.latency], atol=1e-6)

    def test_suppresses_echo_bands_and_keeps_the_users_band(self):
        ref = speechlike(8, 5)
        echo = .3 * delayed(ref, 2400)
        user = np.zeros_like(ref)
        t = np.arange(2 * RATE) / RATE
        user[6 * RATE:] = .02 * np.sin(2 * np.pi * 6000 * t) * (1 + .5 * np.sin(2 * np.pi * 4 * t))
        mic = echo + user
        residual = sosfilt(butter(2, 1500, fs=RATE, output="sos"), echo * .02).astype(np.float32)
        cleaned = residual + user  # the canceller passes the user, leaves 2% (low-passed) of the echo
        s = ResidualEchoSuppressor()
        out = s.process(cleaned, mic, ref)[s.latency:]
        tail = slice(6 * RATE + 4000, 8 * RATE - 1000)
        cut = band_db(out[tail], 200, 1500) - band_db(cleaned[tail][:len(out[tail])], 200, 1500)
        kept = band_db(out[tail], 5500, 6500) - band_db(cleaned[tail][:len(out[tail])], 5500, 6500)
        self.assertLess(cut, -6)
        self.assertGreater(kept, -1)

    def test_device_stream_stays_aligned_with_a_suppressor(self):
        class Halving:
            latency = 640

            def __init__(self):
                self.line = np.zeros(self.latency, np.float32)

            def process(self, mic, ref):
                x = np.concatenate([self.line, mic])
                self.line = x[len(mic):]
                return .5 * x[:len(mic)]

            def close(self):
                pass

        stream = DeviceStream(Halving(), suppressor=ResidualEchoSuppressor())
        self.assertEqual(stream.latency, 640 + HOP)
        mic = np.random.default_rng(6).normal(0, .1, HOP * 40).astype(np.float32)
        out = stream.process(mic, np.zeros_like(mic))
        np.testing.assert_allclose(out[stream.latency:], .5 * mic[:-stream.latency], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
