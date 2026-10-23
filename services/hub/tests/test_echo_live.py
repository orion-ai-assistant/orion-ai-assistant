"""The PC microphone's continuous stream (LocalVQE always, the chain in turns) and quick checks."""
import unittest

import numpy as np

from orion.audio.echo_cancel import EchoCanceller
from orion.audio.echo_cancel.engines import HOP, MIN_PLAY_HOPS, REF_HANGOVER, WARMUP, AdaptiveEngine
from orion.audio.echo_cancel.stream import to_float, to_pcm16

RATE = 16000
LOUD = 0.3


class ScaleEngine:
    """Stands in for an engine: `factor` times the mic, `latency` samples late."""

    def __init__(self, latency, factor):
        self.latency, self.factor = latency, factor
        self.line = np.zeros(latency, np.float32)
        self.fed = 0
        self.closed = False

    def process(self, mic, ref):
        assert len(mic) == len(ref) and len(mic) % HOP == 0
        self.fed += len(mic)
        x = np.concatenate([self.line, mic])
        self.line = x[len(mic):]
        return self.factor * x[:len(mic)]

    def close(self):
        self.closed = True


def vqe():
    return ScaleEngine(HOP, .25)


class AdaptiveEngineTests(unittest.TestCase):
    def setUp(self):
        self.chains = []

        def chain():
            self.chains.append(ScaleEngine(640, .5))
            return self.chains[-1]
        self.engine = AdaptiveEngine(vqe(), chain)
        self.mic = np.ones(HOP * 8, np.float32)
        self.loud = np.full(HOP * 8, LOUD, np.float32)

    def test_without_a_turn_only_localvqe_runs_at_the_chains_latency(self):
        self.assertEqual(self.engine.latency, 640)
        out = np.concatenate([self.engine.process(self.mic, self.loud) for _ in range(3)])
        np.testing.assert_allclose(out[:640], 0)  # LocalVQE delayed to the chain's latency
        np.testing.assert_allclose(out[640:], .25)
        self.assertEqual(self.chains, [])

    def test_a_turn_with_pc_sound_warms_the_chain_then_crossfades_to_it(self):
        for _ in range(4):
            self.engine.process(self.mic, self.loud)
        self.engine.want_chain = True
        out = self.engine.process(self.mic, self.loud)
        chain = self.chains[0]
        self.assertEqual(chain.fed, len(self.mic) * 5)  # ~1 s of history, then this block
        self.assertTrue(self.engine.on_chain)
        self.assertTrue(np.all(np.diff(out[:HOP]) >= -1e-6))  # .25 -> .5 within one hop
        np.testing.assert_allclose(out[HOP:], .5)

    def test_a_turn_with_a_silent_pc_stays_on_localvqe(self):
        self.engine.want_chain = True
        silent = np.zeros_like(self.mic)
        for _ in range(int(REF_HANGOVER / 8) + 2):
            self.engine.process(self.mic, silent)
        self.assertFalse(self.engine.on_chain)
        self.assertEqual(self.chains, [])

    def test_a_short_sound_stays_on_localvqe(self):
        self.engine.want_chain = True  # the assistant's own "heard" sound, ~0.25 s
        self.engine.process(self.mic, self.loud)
        self.engine.process(self.mic, self.loud)
        self.assertFalse(self.engine.on_chain)
        self.assertEqual(self.chains, [])

    def test_back_to_localvqe_when_the_turn_ends(self):
        self.engine.want_chain = True
        for _ in range(MIN_PLAY_HOPS // 8 + 2):
            self.engine.process(self.mic, self.loud)
        self.engine.want_chain = False
        out = self.engine.process(self.mic, self.loud)
        self.assertFalse(self.engine.on_chain)
        np.testing.assert_allclose(out[HOP:], .25)
        self.assertEqual(self.engine.switches, 2)

    def test_warm_up_history_is_about_one_second(self):
        for _ in range(20):
            self.engine.process(self.mic, self.loud)
        self.assertEqual(len(self.engine._history_mic), WARMUP)


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeCapture:
    def __init__(self, ring):
        self.ring = ring
        self.device_name = "Fake speakers"

    def start(self):
        pass

    def stop(self):
        pass


class _CancellerCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.vqes, self.chains = [], []

        def make_vqe():
            self.vqes.append(vqe())
            return self.vqes[-1]

        def make_chain():
            self.chains.append(ScaleEngine(640, .5))
            return self.chains[-1]
        # The stand-in engines' plumbing is under test here, not the residual echo suppression.
        self.ec = EchoCanceller(engine_factory=make_chain, vqe_factory=make_vqe, capture_factory=FakeCapture,
                                clock=self.clock, suppress=False)
        self.ec.arm()

    def play(self, seconds):
        self.clock.now += seconds
        self.ec.ring.write(np.full(int(seconds * RATE), LOUD, np.float32), self.clock.now)

    def play_and_feed(self, seconds, mic_level=.2, chunk=.1):
        out = b""
        for _ in range(round(seconds / chunk)):
            self.clock.now += chunk
            self.ec.ring.write(np.full(int(chunk * RATE), LOUD, np.float32), self.clock.now)
            out += self.ec.feed_live("pc:a", to_pcm16(np.full(int(chunk * RATE), mic_level, np.float32)), self.clock.now)
        return to_float(out)



class LiveStreamTests(_CancellerCase):
    def test_stream_is_cleaned_by_localvqe_and_stays_warm_across_reopens(self):
        self.ec.open_live("pc:a", self.clock.now)
        out = self.play_and_feed(1.0)
        self.assertGreater(len(out), RATE * .8)
        np.testing.assert_allclose(out[HOP * 4:], .05, atol=1e-3)  # .2 * .25
        self.ec.close_live("pc:a")
        self.ec.open_live("pc:a", self.clock.now)
        self.play_and_feed(.5)
        self.assertEqual(len(self.vqes), 1)  # same engine after a reconnect

    def test_a_turn_switches_the_stream_to_the_chain(self):
        self.ec.open_live("pc:a", self.clock.now)
        self.play_and_feed(1.0)
        self.ec.live_turn("pc:a", True)
        out = self.play_and_feed(1.0)
        np.testing.assert_allclose(out[-RATE // 2:], .1, atol=1e-3)  # .2 * .5
        self.ec.live_turn("pc:a", False)
        out = self.play_and_feed(1.0)
        np.testing.assert_allclose(out[-RATE // 2:], .05, atol=1e-3)

    def test_standby_is_localvqe_alone_the_suppressor_only_in_turns(self):
        ec = EchoCanceller(engine_factory=lambda: ScaleEngine(640, .5), vqe_factory=vqe,
                           capture_factory=FakeCapture, clock=self.clock, suppress=True)
        ec.arm()
        self.ec = ec
        ec.open_live("pc:a", self.clock.now)
        suppressor = ec._live["pc:a"].turn.stream.suppressor
        self.assertFalse(suppressor.enabled)  # "Orion" is listened for on LocalVQE's output
        self.ec.live_turn("pc:a", True)
        self.assertTrue(suppressor.enabled)
        self.ec.live_turn("pc:a", False)
        self.assertFalse(suppressor.enabled)

    def test_passthrough_until_the_models_are_ready(self):
        ec = EchoCanceller(capture_factory=FakeCapture, clock=self.clock)  # no factories yet
        ec.arm()
        ec.open_live("pc:a", self.clock.now)
        pcm = to_pcm16(np.full(1600, .2, np.float32))
        self.assertEqual(ec.feed_live("pc:a", pcm, self.clock.now), pcm)

    def test_clock_drift_moves_the_reference_pairing(self):
        self.ec.open_live("pc:a", self.clock.now)
        live = self.ec._live["pc:a"]
        self.play_and_feed(1.0)
        start = live.turn.ref_start
        # The browser's packets now arrive 1% slower than their samples: 80 ms over 8 s.
        for _ in range(80):
            self.clock.now += .101
            self.ec.ring.write(np.full(1616, LOUD, np.float32), self.clock.now)
            self.ec.feed_live("pc:a", to_pcm16(np.full(1600, .2, np.float32)), self.clock.now)
        self.assertGreater(live.turn.ref_start - start, 320)


class AlignmentTests(_CancellerCase):
    def speech_like(self, seconds, seed=0):
        """Noise with a syllable-rate envelope, so its envelope has something to match."""
        rng = np.random.default_rng(seed)
        n = int(seconds * RATE)
        env = np.repeat(rng.uniform(.02, .3, n // 1600 + 1), 1600)[:n]
        return (rng.normal(0, 1, n) * env).astype(np.float32)

    def test_measures_the_echo_delay(self):
        from orion.audio.echo_cancel.service import _echo_delay
        ref = self.speech_like(6.0)
        late, window = int(2.5 * RATE), int(3.0 * RATE)
        for delay in (1280, 14400, -4000):  # 80 ms, 900 ms, echo before its reference
            mic = .3 * ref[late - delay:late - delay + window]
            found = _echo_delay(mic, ref, late, len(ref) - window - late)
            self.assertIsNotNone(found)
            self.assertLess(abs(found - delay), 320)

    def test_a_late_browser_microphone_is_realigned(self):
        """The browser's audio reaches the Hub ~0.9 s late: the echo seems 0.9 s after its pair."""
        play = self.speech_like(12.0, seed=1)
        lag = int(.9 * RATE)
        self.clock.now += 4.0
        self.ec.ring.write(play[:int(4.0 * RATE)], self.clock.now)
        self.ec.open_live("pc:a", self.clock.now)
        live = self.ec._live["pc:a"]
        for k in range(int(4.0 * 10), int(11.0 * 10)):  # 100 ms chunks
            a, b = k * 1600, (k + 1) * 1600
            self.clock.now += .1
            self.ec.ring.write(play[a:b], self.clock.now)
            # Mic sample paired with ring index `start + i` holds the echo of index `start + i - lag`.
            self.ec.feed_live("pc:a", to_pcm16(.3 * play[a - lag:b - lag]), self.clock.now)
        self.assertIsNotNone(live.echo_delay)
        self.assertLess(abs(live.realigned - (lag - 1280)), 480)

    def test_silent_pc_is_not_measured(self):
        self.clock.now += 4.0
        self.ec.ring.write(np.zeros(int(4.0 * RATE), np.float32), self.clock.now)
        self.ec.open_live("pc:a", self.clock.now)
        live = self.ec._live["pc:a"]
        for _ in range(60):
            self.clock.now += .1
            self.ec.ring.write(np.zeros(1600, np.float32), self.clock.now)
            self.ec.feed_live("pc:a", to_pcm16(self.speech_like(.1)), self.clock.now)
        self.assertIsNone(live.echo_delay)
        self.assertEqual(live.realigned, 0)


class PhoneTurnTests(_CancellerCase):
    def test_silent_pc_around_orion_needs_no_check(self):
        self.clock.now += 3.0
        self.ec.ring.write(np.zeros(RATE * 3, np.float32), self.clock.now)
        self.assertTrue(self.ec.reference_quiet(self.clock.now, RATE * 2))
        self.play(.2)  # the PC starts playing right after "Orion"
        self.assertFalse(self.ec.reference_quiet(self.clock.now - .2, RATE * 2))

    def test_phone_turn_uses_localvqe_while_the_pc_is_silent(self):
        self.clock.now += 3.0
        self.ec.ring.write(np.zeros(RATE * 3, np.float32), self.clock.now)
        out = to_float(self.ec.start_turn("phone-1", to_pcm16(np.full(RATE * 2, .2, np.float32)), self.clock.now))
        np.testing.assert_allclose(out[RATE:], .05, atol=1e-3)  # .2 * .25: LocalVQE, not the chain
        self.assertEqual(self.chains, [])

    def test_phone_turn_uses_the_chain_while_the_pc_plays(self):
        self.play(3.0)
        out = to_float(self.ec.start_turn("phone-1", to_pcm16(np.full(RATE * 2, .2, np.float32)), self.clock.now))
        np.testing.assert_allclose(out[-RATE // 2:], .1, atol=1e-3)  # .2 * .5: the chain


    def test_check_uses_localvqe_alone_and_keeps_the_length(self):
        self.play(2.0)
        preroll = to_pcm16(np.full(RATE * 2, .2, np.float32))
        out = to_float(self.ec.check_preroll("phone-1", preroll, self.clock.now))
        self.assertEqual(len(out), RATE * 2)
        np.testing.assert_allclose(out[HOP * 2:], .05, atol=1e-3)
        self.assertEqual(self.chains, [])  # the chain is not touched

    def test_check_without_models_returns_the_pre_roll(self):
        ec = EchoCanceller(capture_factory=FakeCapture, clock=self.clock)
        ec.arm()
        preroll = to_pcm16(np.full(800, .2, np.float32))
        self.assertEqual(ec.check_preroll("phone-1", preroll), preroll)
