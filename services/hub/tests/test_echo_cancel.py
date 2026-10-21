import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from orion.audio.echo_cancel import EchoCanceller, assets
from orion.audio.echo_cancel.resample import StreamResampler
from orion.audio.echo_cancel.ring import ReferenceRing
from orion.audio.echo_cancel.stream import HOP, DeviceStream, Turn, to_float, to_pcm16

RATE = 16000
LOUD = 0.3


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class HalvingEngine:
    """Stands in for DTLN+LocalVQE: output = half the mic, `latency` samples late."""
    latency = 640

    def __init__(self):
        self.line = np.zeros(self.latency, np.float32)
        self.calls = 0
        self.closed = False

    def process(self, mic, ref):
        assert len(mic) == len(ref) and len(mic) % HOP == 0
        self.calls += 1
        x = np.concatenate([self.line, mic])
        self.line = x[len(mic):]
        return 0.5 * x[:len(mic)]

    def close(self):
        self.closed = True


def stream_frames(turn):
    return turn.stream.processed_frames


def filled_ring(clock, ref, rate=RATE):
    ring = ReferenceRing(seconds=10, clock=clock)
    clock.now += len(ref) / rate
    ring.write(ref, clock.now)
    return ring


class ResamplerTests(unittest.TestCase):
    def test_chunked_equals_whole(self):
        x = np.random.default_rng(1).standard_normal(48000).astype(np.float32)
        whole = StreamResampler(48000, 16000).process(x)
        r = StreamResampler(48000, 16000)
        cuts = [0, 7, 480, 481, 9000, 23999, 48000]
        parts = np.concatenate([r.process(x[a:b]) for a, b in zip(cuts, cuts[1:])])
        np.testing.assert_allclose(parts, whole, atol=1e-6)
        self.assertEqual(len(whole), 16000)

    def test_sine_keeps_frequency_and_level(self):
        for src in (48000, 44100):
            t = np.arange(src) / src
            y = StreamResampler(src, 16000).process(0.5 * np.sin(2 * np.pi * 1000 * t))
            self.assertAlmostEqual(len(y), 16000, delta=1)
            spectrum = np.abs(np.fft.rfft(y[2000:14000] * np.hanning(12000)))
            self.assertAlmostEqual(np.argmax(spectrum) * 16000 / 12000, 1000, delta=2)
            self.assertAlmostEqual(np.sqrt(np.mean(y[2000:14000] ** 2)), 0.5 / np.sqrt(2), delta=0.01)

    def test_removes_content_above_new_nyquist(self):
        t = np.arange(48000) / 48000
        y = StreamResampler(48000, 16000).process(np.sin(2 * np.pi * 12000 * t))
        self.assertLess(np.sqrt(np.mean(y[1000:-1000] ** 2)), 0.01)


class RingTests(unittest.TestCase):
    def test_write_read_and_wraparound(self):
        clock = FakeClock()
        ring = ReferenceRing(seconds=1, clock=clock)
        x = np.arange(40000, dtype=np.float32)
        for i in range(0, 40000, 160):
            clock.now += 0.01
            ring.write(x[i:i + 160], clock.now)
        self.assertEqual(ring.end, 40000)
        np.testing.assert_array_equal(ring.read(39000, 1000), x[39000:])
        # Older than the 1 s capacity: zeros, not stale data.
        np.testing.assert_array_equal(ring.read(20000, 10), np.zeros(10))
        with self.assertRaises(ValueError):
            ring.read(39990, 20)

    def test_silence_gap_is_zero_filled_on_the_timeline(self):
        clock = FakeClock()
        ring = ReferenceRing(clock=clock)
        ring.write(np.ones(160, np.float32), clock.now)
        clock.now += 0.5  # loopback sent nothing for half a second
        ring.write(np.ones(160, np.float32), clock.now)
        self.assertEqual(ring.end, 8160)
        self.assertEqual(ring.gap_samples, 8000 - 160)
        np.testing.assert_array_equal(ring.read(160, 7840), np.zeros(7840))
        self.assertEqual(ring.index_at(clock.now), 8160)

    def test_advance_pads_idle_time_but_keeps_slack(self):
        clock = FakeClock()
        ring = ReferenceRing(clock=clock)
        ring.write(np.ones(160, np.float32), clock.now)
        clock.now += 1.0
        ring.advance()
        self.assertEqual(ring.end, 160 + 16000 - ring.slack)
        clock.now += 0.01  # a real packet arriving after the padding continues the timeline
        ring.write(np.ones(160, np.float32), clock.now)
        self.assertLessEqual(round((clock.now - ring.time_of(0)) * RATE) - ring.end, ring.slack)
        self.assertEqual(ring.read(ring.end - 160, 160)[0], 1)

    def test_late_callback_is_not_a_silence_gap(self):
        clock = FakeClock()
        ring = ReferenceRing(clock=clock)
        for i in range(100):
            clock.now += 0.01
            ring.write(np.ones(160, np.float32), clock.now)
        clock.now += 0.12  # the scheduler was busy: the next 12 packets arrive in a burst
        for _ in range(13):
            ring.write(np.ones(160, np.float32), clock.now)
        self.assertEqual(ring.gap_samples, 0)
        np.testing.assert_array_equal(ring.read(0, ring.end), np.ones(ring.end))

    def test_slow_device_clock_lag_stays_bounded(self):
        clock = FakeClock()
        ring = ReferenceRing(clock=clock)
        for i in range(3000):  # 30 s of a device clock 1 % slow
            clock.now += 0.0101
            ring.write(np.ones(160, np.float32), clock.now)
            lag = ring.index_at(clock.now) - ring.end
            self.assertLessEqual(lag, ring.slack + 0.0101 * RATE * 101)
        self.assertGreater(ring.gap_samples, 0)

    def test_fast_device_clock_reanchors(self):
        clock = FakeClock()
        ring = ReferenceRing(clock=clock)
        ring.write(np.ones(160, np.float32), clock.now)
        ring.write(np.ones(3200, np.float32), clock.now)  # 200 ms of audio in no time
        self.assertEqual(ring.index_at(clock.now), ring.end)


class DeviceStreamTests(unittest.TestCase):
    def run_frames(self, stream, mic, ref):
        return np.concatenate([stream.process(mic[i:i + HOP], ref[i:i + HOP]) for i in range(0, len(mic), HOP)])

    def test_one_block_equals_frame_by_frame(self):
        rng = np.random.default_rng(5)
        mic = rng.uniform(-0.5, 0.5, HOP * 300).astype(np.float32)
        ref = np.zeros_like(mic)
        ref[HOP * 40:HOP * 90] = LOUD
        ref[HOP * 160:HOP * 170] = LOUD
        block = DeviceStream(HalvingEngine(), hangover=0.3).process(mic, ref)
        frames = self.run_frames(DeviceStream(HalvingEngine(), hangover=0.3), mic, ref)
        np.testing.assert_array_equal(block, frames)

    def test_bypass_while_reference_silent(self):
        engine = HalvingEngine()
        stream = DeviceStream(engine)
        mic = np.random.default_rng(2).uniform(-0.5, 0.5, HOP * 20).astype(np.float32)
        out = self.run_frames(stream, mic, np.zeros_like(mic))
        self.assertEqual(engine.calls, 0)
        np.testing.assert_array_equal(out[640:], mic[:-640])  # raw, delayed like the engine

    def test_engine_runs_with_sound_and_hangover_then_bypasses(self):
        engine = HalvingEngine()
        stream = DeviceStream(engine, hangover=0.5)
        n = HOP * 200
        mic = np.full(n, 0.2, np.float32)
        ref = np.zeros(n, np.float32)
        ref[HOP * 20:HOP * 60] = LOUD
        out = self.run_frames(stream, mic, ref)[640:]
        self.assertEqual(stream.state, "bypass")
        # Before the reference: raw. Well inside the active stretch: engine (half level).
        np.testing.assert_allclose(out[:HOP * 18], 0.2)
        np.testing.assert_allclose(out[HOP * 30:HOP * 80], 0.1)
        # Hangover 0.5 s ~ 31 frames after the reference stops, then back to raw.
        np.testing.assert_allclose(out[HOP * 100:], 0.2)
        self.assertLess(engine.calls, 200)
        # Crossfades never jump.
        self.assertLess(np.max(np.abs(np.diff(out))), 0.2 / HOP * 1.5)


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.ref = np.full(RATE * 4, LOUD, np.float32)
        self.ring = filled_ring(self.clock, self.ref)

    def test_output_is_sample_aligned_and_complete(self):
        engine = HalvingEngine()
        turn = Turn(DeviceStream(engine), self.ring, ref_start=0, clock=self.clock)
        mic = np.random.default_rng(3).uniform(-0.5, 0.5, 30011).astype(np.float32)
        cuts = [0, 1, 100, 5000, 5001, 17000, 30011]
        out = np.concatenate([turn.feed(mic[a:b]) for a, b in zip(cuts, cuts[1:])] + [turn.finish()])
        self.assertEqual(len(out), len(mic))
        # Active from the first frame (loud reference): after priming + crossfade, exactly half.
        np.testing.assert_allclose(out[HOP * 2:], 0.5 * mic[HOP * 2:], atol=1e-6)

    def test_waits_for_reference_then_catches_up(self):
        engine = HalvingEngine()
        turn = Turn(DeviceStream(engine), self.ring, ref_start=self.ring.end - HOP * 4, clock=self.clock)
        out = turn.feed(np.zeros(HOP * 10, np.float32))
        self.assertEqual(len(out), HOP * 4 - 640)  # only the 4 frames whose reference exists
        self.assertEqual(stream_frames(turn), 4)
        self.clock.now += HOP * 6 / RATE
        self.ring.write(np.full(HOP * 6, LOUD, np.float32), self.clock.now)
        out = turn.feed(np.zeros(0, np.float32))
        self.assertEqual(stream_frames(turn), 10)
        self.assertEqual(len(out), HOP * 6)

    def test_reference_missing_too_long_counts_as_silence(self):
        engine = HalvingEngine()
        turn = Turn(DeviceStream(engine), self.ring, ref_start=self.ring.end, clock=self.clock, stall=0.3)
        mic = np.full(HOP * 4, 0.2, np.float32)
        self.assertEqual(len(turn.feed(mic)), 0)
        self.clock.now += 0.5  # capture stalled (device switch, disarm)
        out = turn.feed(np.zeros(0, np.float32))
        self.assertEqual(len(out), HOP * 4 - 640)


class FakeCapture:
    def __init__(self, ring):
        self.ring = ring
        self.running = False
        self.device_name = "Fake speakers"

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class EchoCancellerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.engines = []

        def factory():
            self.engines.append(HalvingEngine())
            return self.engines[-1]

        self.ec = EchoCanceller(engine_factory=factory, capture_factory=FakeCapture, clock=self.clock, max_devices=2)

    def play(self, seconds):
        self.clock.now += seconds
        self.ec.ring.write(np.full(int(seconds * RATE), LOUD, np.float32), self.clock.now)

    def test_turn_api_round_trip_and_state_survives_turns(self):
        self.ec.arm()
        self.play(2.5)
        preroll = to_pcm16(np.full(RATE * 2, 0.2, np.float32))
        out = self.ec.start_turn("phone-1", preroll)
        self.play(0.5)
        out += self.ec.process("phone-1", to_pcm16(np.full(RATE // 2, 0.2, np.float32)))
        out += self.ec.end_turn("phone-1")
        clean = to_float(out)
        self.assertEqual(len(clean), int(2.5 * RATE))
        np.testing.assert_allclose(clean[HOP * 2:], 0.1, atol=1e-4)
        stats = self.ec.last_turn["phone-1"]
        self.assertTrue(stats["cancelled"])
        self.assertEqual(stats["samples"], int(2.5 * RATE))

        self.play(1.0)
        self.ec.start_turn("phone-1", preroll)
        self.ec.end_turn("phone-1")
        self.assertEqual(len(self.engines), 1)  # same engine: no reset between turns

    def test_passthrough_when_not_armed(self):
        pcm = to_pcm16(np.linspace(-0.5, 0.5, 4000, dtype=np.float32))
        self.assertEqual(self.ec.start_turn("phone-1", pcm), pcm)
        self.assertEqual(self.ec.process("phone-1", pcm), pcm)
        self.assertEqual(self.ec.end_turn("phone-1"), b"")
        self.assertFalse(self.ec.last_turn["phone-1"]["cancelled"])
        self.assertEqual(self.engines, [])

    def test_new_turn_closes_previous_and_devices_are_bounded(self):
        self.ec.arm()
        self.play(1.0)
        for device in ("a", "b", "c"):
            self.ec.start_turn(device, b"")
        self.assertTrue(self.engines[0].closed)  # "a" evicted (max_devices=2)
        self.assertEqual(self.ec.status()["devices"], ["b", "c"])
        self.ec.start_turn("c", b"")  # restarting a turn flushes the old one
        self.assertIn("c", self.ec.last_turn)

    def test_process_without_turn_raises(self):
        with self.assertRaises(KeyError):
            self.ec.process("nobody", b"\0\0")

    def test_disarm_stops_capture(self):
        self.ec.arm()
        capture = self.ec._capture
        self.ec.disarm()
        self.assertFalse(capture.running)
        self.assertFalse(self.ec.status()["armed"])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.payload = b"model-bytes"
        sha = hashlib.sha256(self.payload).hexdigest()
        guard = patch.dict(assets.MODELS, {"m.bin": ("https://example.invalid/m.bin", sha)}, clear=True)
        guard.start()
        self.addCleanup(guard.stop)
        self.dir = Path(tempfile.mkdtemp())

    def test_downloads_once_and_verifies(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(self.payload)

        assets.ensure_models(self.dir, opener=opener)
        assets.ensure_models(self.dir, opener=opener)
        self.assertEqual((self.dir / "m.bin").read_bytes(), self.payload)
        self.assertEqual(calls, ["https://example.invalid/m.bin"])

    def test_hash_mismatch_leaves_nothing(self):
        with self.assertRaises(ValueError):
            assets.ensure_models(self.dir, opener=lambda request, timeout: io.BytesIO(b"tampered"))
        self.assertEqual(list(self.dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
