"""The echo canceller the PC-microphone relay calls (see package docstring for the contract)."""
from collections import OrderedDict, deque
import logging
import os
import threading
import time

import numpy as np

from .ring import ReferenceRing
from .stream import DeviceStream, Turn, to_float, to_pcm16

log = logging.getLogger(__name__)
# Shown in the Hub log by default (uvicorn's handler), unlike this package's own logger.
_diag = logging.getLogger("uvicorn.error")


class _PassThrough:
    """A turn without cancellation: the PC assistant is not armed or the engine is unavailable."""
    stream = None

    def __init__(self):
        self.received = self.emitted = 0

    def feed(self, mic):
        self.received += len(mic)
        self.emitted += len(mic)
        return mic

    def finish(self):
        return np.zeros(0, np.float32)


def _factories(models_dir):
    """(chain factory, LocalVQE-alone factory) over one set of loaded models."""
    from . import assets, engines
    models_dir = assets.ensure_models(models_dir)
    dtln_models = engines.DtlnModels(models_dir)
    engines.LocalVqeLibrary.load()
    model = models_dir / assets.LOCALVQE_MODEL
    return (lambda: engines.DtlnLocalVqe(dtln_models, model)), (lambda: engines.LocalVqeOnly(model))


DRIFT_WINDOW = 3.0  # seconds of arrival timing behind one clock-drift estimate
DRIFT_LIMIT = 320  # samples (20 ms) of drift before the reference pairing is moved
LOSS_WINDOW = 0.6  # seconds: a microphone this far behind for this long lost audio (not jitter)
# Where the browser's microphone really is against the loopback: its audio reaches the Hub late
# by an unknown, browser-made delay (live: ~1 s), so the first packet's arrival only roughly says
# which reference belongs to it. The echo's own delay is measured instead, from the envelopes of
# the microphone and the reference while the PC plays, and the pairing is moved so the echo
# follows its reference by ALIGN_TARGET (what the engines expect; LocalVQE searches ~1 s at most).
ALIGN_WINDOW = 3.0  # seconds of microphone per measurement
ALIGN_EARLY = 0.5  # seconds the echo may seem to come before its reference
ALIGN_LATE = 2.5  # ... or after it
ALIGN_TARGET = 1280  # samples (80 ms): echo after its reference once aligned
ALIGN_SLACK = 2400  # samples (150 ms) either side of the target left alone
ALIGN_EVERY = 1.0  # seconds between measurements
ALIGN_FRAME = 160  # 10 ms envelope frames
# Measuring only: live, envelope matching found 40 ms once, then -90, 1270 and 2140 ms on music
# and speech, and moving the pairing by those broke the canceller and "Orion". The arrival-time
# pairing was right (40 ms). Moving stays off until a measurement is trustworthy.
ALIGN_MOVE = False


class _Live:
    """The PC microphone's continuous stream: one never-ending turn on an `AdaptiveEngine`.

    The browser's clock and the loopback's drift apart over hours; the earliest arrival of each
    packet (jitter only ever delays it) says where the reference should be, and the pairing is
    moved when it is off by more than 20 ms."""

    def __init__(self, turn, engine, arrived):
        self.turn, self.engine = turn, engine
        self.started = arrived
        self.cpu = 0.0
        self._offset = None
        self._deviations = deque()
        # Lost microphone audio (a Realtek: 13560 of 16000 samples a second) is filled with
        # silence; moving the pairing for each loss made the reference stutter.
        self.filled = self.fills = 0
        self.moves = self.moved = 0
        self._padded = deque()  # (first input sample, count) filled with silence, not yet emitted
        self._mic = np.zeros(0, np.float32)  # the last ALIGN_WINDOW s of raw microphone
        self._next_align = arrived + ALIGN_WINDOW
        self.turn_energy, self.turn_counted = None, 0  # the Turn's energy sums when a PC turn began
        self.echo_delay = None  # samples from the reference to its echo, last measured
        self.realigned = 0  # samples the pairing was moved by, in all

    def align(self, ring, mic, arrived):
        """Measure the echo's delay against the paired reference; move the pairing when it is off."""
        if not isinstance(self.turn, Turn):
            return
        window = int(ALIGN_WINDOW * 16000)
        self._mic = np.concatenate([self._mic, mic])[-window:]
        if arrived < self._next_align or len(self._mic) < window:
            return
        self._next_align = arrived + ALIGN_EVERY
        end = self.turn.ref_start + self.turn.received  # the reference paired with the newest sample
        early, late = int(ALIGN_EARLY * 16000), int(ALIGN_LATE * 16000)
        lo, hi = end - window - late, min(end + early, ring.end)
        if lo < ring.end - ring.capacity or hi - lo < window + late:
            return
        ref = ring.read(lo, hi - lo)
        if float(np.max(np.abs(ref))) <= 10 ** (-50 / 20):
            return  # the PC is (nearly) silent: nothing to measure
        delay = _echo_delay(self._mic, ref, late, (hi - lo) - window - late)
        if delay is None:
            return
        self.echo_delay = delay
        if not ALIGN_MOVE or abs(delay - ALIGN_TARGET) <= ALIGN_SLACK:
            return
        shift = delay - ALIGN_TARGET  # pair with a reference this much older
        self.turn.ref_start -= shift
        if self._offset is not None:
            self._offset += shift  # the drift tracker measures against the moved pairing
        self.realigned += shift
        self._mic = np.zeros(0, np.float32)  # measured against the old pairing: start over
        _diag.info("Echo live stream: the echo came %.0f ms after its reference; pairing moved by %.0f ms",
                   delay / 16, -shift / 16)

    def track(self, ring, arrived):
        """Samples of lost audio to fill before this packet (0 if none)."""
        if not isinstance(self.turn, Turn):
            return 0
        deviation = ring.index_at(arrived) - (self.turn.ref_start + self.turn.received)
        if self._offset is None:
            self._offset = deviation
            return 0
        self._deviations.append((arrived, deviation - self._offset))
        while self._deviations and arrived - self._deviations[0][0] > DRIFT_WINDOW:
            self._deviations.popleft()
        if arrived - self.started >= LOSS_WINDOW:
            recent = [d for t, d in self._deviations if arrived - t <= LOSS_WINDOW]
            lost = min(recent) if recent else 0
            if lost > DRIFT_LIMIT:  # behind for a while: audio was lost, not merely late
                self._deviations.clear()  # measured before the fill: start over
                self.fills += 1
                self.filled += lost
                return lost
        if arrived - self.started < DRIFT_WINDOW:
            return 0
        drift = min(d for _, d in self._deviations)
        if abs(drift) > DRIFT_LIMIT:
            self.moves += 1
            self.moved += drift
            self.turn.ref_start += drift
            self._offset += drift
            self._deviations.clear()
        return 0

    def feed(self, mic, fill):
        """Feed the turn, with `fill` samples of silence for lost audio first; the output has no
        samples for the filled stretch (one to one with what was sent)."""
        if fill:
            self._padded.append((self.turn.received, fill))
            mic = np.concatenate([np.zeros(fill, np.float32), mic])
        first = self.turn.emitted
        out = self.turn.feed(mic)
        if not self._padded:
            return out
        keep = np.ones(len(out), bool)
        for start, n in self._padded:
            a, b = max(start - first, 0), min(start + n - first, len(out))
            if a < b:
                keep[a:b] = False
        while self._padded and sum(self._padded[0]) <= first + len(out):
            self._padded.popleft()
        return out[keep]


def _echo_delay(mic, ref, late, early):
    """Samples the echo in `mic` follows its reference: `ref` holds `late` samples before the
    microphone's window, the window, and `early` after it. None when no clear echo is found."""
    f = ALIGN_FRAME

    def envelope(x):
        n = len(x) // f
        e = np.log10(np.mean(x[:n * f].reshape(n, f).astype(np.float64) ** 2, axis=1) + 1e-10)
        return e

    em, er = envelope(mic), envelope(ref)
    em = em - em.mean()
    if em.std() < 1e-6:
        return None
    em /= em.std()
    n, base = len(em), late // f
    scores = []
    for lag in range(-(early // f), late // f + 1):  # lag > 0: the echo comes after the reference
        seg = er[base - lag:base - lag + n]
        if len(seg) < n or seg.std() < 1e-6:
            scores.append(-1.0)
            continue
        scores.append(float(np.dot(em, (seg - seg.mean()) / seg.std()) / n))
    scores = np.array(scores)
    best = int(np.argmax(scores))
    if scores[best] < .3 or scores[best] - np.median(scores) < .2:
        return None
    return (best - early // f) * f


def _loopback(ring):
    from .loopback import LoopbackCapture
    return LoopbackCapture(ring)


class EchoCanceller:
    def __init__(self, engine_factory=None, capture_factory=_loopback, models_dir=None, ring_seconds=8.0,
                 clock=time.perf_counter, max_devices=4, vqe_factory=None, suppress=None):
        self.ring = ReferenceRing(ring_seconds, clock=clock)
        if suppress is None:  # ORION_RESIDUAL_ECHO=0 turns it off (A/B tests)
            suppress = os.environ.get("ORION_RESIDUAL_ECHO", "1") != "0"
        self.suppress = suppress  # residual echo suppression after the engine (per stream)
        self.clock = clock
        self.max_devices = max_devices
        self._models_dir = models_dir
        self._capture_factory = capture_factory
        self._capture = None
        self._engine_factory = engine_factory
        self._vqe_factory = vqe_factory
        self._preparing = None
        self.unavailable = None  # why the chain cannot run (None while it can, or is still loading)
        self._streams = OrderedDict()  # device_id -> DeviceStream, least recently used first
        self._turns = {}
        self._checkers = OrderedDict()  # device_id -> LocalVQE-alone stream for quick pre-roll checks
        self._live_streams = OrderedDict()  # device_id -> DeviceStream(AdaptiveEngine), kept warm
        self._live = {}  # device_id -> _Live
        self._device_locks = {}
        self._lock = threading.Lock()
        self.last_turn = {}  # device_id -> stats of its last finished turn

    # --- PC assistant session ---

    def arm(self):
        """Start capturing the loudspeaker reference (PC assistant session armed). Idempotent."""
        with self._lock:
            if self._capture is None:
                if os.name != "nt" and self._capture_factory is _loopback:
                    self.unavailable = "loopback capture needs Windows"
                    return
                self._capture = self._capture_factory(self.ring)
            self._capture.start()
            if self._engine_factory is None and self._preparing is None and self.unavailable is None:
                self._preparing = threading.Thread(target=self._prepare, name="echo-prepare", daemon=True)
                self._preparing.start()

    def disarm(self):
        """Stop the reference capture. Per-phone engine state stays warm for the next session."""
        with self._lock:
            capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()

    @property
    def armed(self):
        return self._capture is not None

    def wait_ready(self, timeout=None):
        thread = self._preparing
        if thread is not None:
            thread.join(timeout)
        return self._engine_factory is not None

    def _prepare(self):
        try:
            chain, vqe = _factories(self._models_dir)
        except Exception as exc:
            log.warning("Echo cancellation unavailable", exc_info=True)
            self.unavailable = f"{type(exc).__name__}: {exc}"
        else:
            self._vqe_factory = vqe
            self._engine_factory = chain
            log.info("Echo cancellation ready")

    # --- turns (one phone at a time per device_id; calls for one device must not overlap) ---

    def start_turn(self, device_id, preroll_pcm=b"", start_time=None):
        """Open a turn and return the cleaned pre-roll (same length as `preroll_pcm`).

        `preroll_pcm`: PCM16 LE mono 16 kHz recorded before the live stream (wake word included).
        `start_time`: `time.perf_counter()` when the first live sample (the one right after the
        pre-roll) was captured; default now, i.e. when the turn start reached the Hub."""
        start_time = self.clock() if start_time is None else start_time
        preroll = to_float(preroll_pcm)
        with self._lock:
            lock = self._device_locks.setdefault(device_id, threading.Lock())
        with lock:
            self._finish(device_id)
            stream = self._stream(device_id)
            if stream is None:
                turn = _PassThrough()
            else:
                turn = Turn(stream, self.ring, self.ring.index_at(start_time) - len(preroll), clock=self.clock)
            turn.started = self.clock()
            turn.cpu = 0.0
            self._turns[device_id] = turn
            return to_pcm16(self._feed(turn, preroll))

    def process(self, device_id, pcm):
        """Feed live PCM16; returns the cleaned samples that are ready (sample-aligned with the input,
        possibly fewer or more bytes than given while the reference catches up)."""
        with self._device_locks[device_id]:
            turn = self._turns.get(device_id)
            if turn is None:
                raise KeyError(f"No open turn for {device_id}")
            return to_pcm16(self._feed(turn, to_float(pcm)))

    def pc_playing(self, seconds=1.2):
        """The PC played something within the last `seconds` (the echo-frame gate runs then)."""
        if self._capture is None:
            return False
        from .engines import REF_THRESHOLD
        end = self.ring.end
        n = min(int(seconds * 16000), end)
        return n > 0 and float(np.max(np.abs(self.ring.read(end - n, n)))) > REF_THRESHOLD

    def reference_quiet(self, start_time, preroll_samples, margin=0.5):
        """Whether the PC played nothing from `preroll_samples` (plus `margin` s for the echo's way
        to the microphone) before `start_time` until now: then the phone's "Orion" cannot have
        come from the PC's loudspeaker and needs no check. False when unknown."""
        if self._capture is None:
            return False
        from .engines import REF_THRESHOLD
        end = self.ring.end
        start = self.ring.index_at(start_time) - preroll_samples - round(margin * 16000)
        if end <= start:
            return False
        return float(np.max(np.abs(self.ring.read(start, end - start)))) <= REF_THRESHOLD

    def check_preroll(self, device_id, preroll_pcm, start_time=None):
        """The pre-roll cleaned by LocalVQE alone (same length), for the PC's quick Orion check
        while the chain is still on its way. Its own warm stream per device; never the chain's."""
        preroll = to_float(preroll_pcm)
        start_time = self.clock() if start_time is None else start_time
        with self._lock:
            lock = self._device_locks.setdefault(device_id + "#check", threading.Lock())
        with lock:
            stream = self._keyed_stream(self._checkers, device_id, self._vqe_factory)
            if stream is None:
                return preroll_pcm
            turn = Turn(stream, self.ring, self.ring.index_at(start_time) - len(preroll), clock=self.clock)
            out = np.concatenate([turn.feed(preroll), turn.finish()])
            return to_pcm16(out[:len(preroll)])

    # --- the PC microphone's continuous stream ---

    def open_live(self, device_id, arrived=None):
        """Start (or restart) a device's continuous stream; its engines stay warm in between."""
        arrived = self.clock() if arrived is None else arrived
        with self._lock:
            lock = self._device_locks.setdefault(device_id + "#live", threading.Lock())
        with lock:
            self._live[device_id] = self._new_live(device_id, arrived)

    def feed_live(self, device_id, pcm, arrived=None):
        """Raw PCM16 in (in order, no gaps); cleaned PCM16 out, aligned sample for sample."""
        arrived = self.clock() if arrived is None else arrived
        with self._device_locks[device_id + "#live"]:
            live = self._live[device_id]
            if live.engine is None and self._capture is not None and self._engine_factory and self._vqe_factory:
                live = self._live[device_id] = self._new_live(device_id, arrived)  # models just became ready
            mic = to_float(pcm)
            fill = live.track(self.ring, arrived) or 0
            c0 = time.thread_time()
            out = live.feed(mic, fill) if isinstance(live.turn, Turn) else live.turn.feed(mic)
            live.align(self.ring, mic, arrived)  # after feeding: `received` includes this packet
            live.cpu += time.thread_time() - c0
            return to_pcm16(out)

    def live_turn(self, device_id, active):
        """A turn opened or closed on the PC: the chain may run while one is open."""
        live = self._live.get(device_id)
        if live is None or live.engine is None:
            return
        engine = live.engine
        suppressor = getattr(live.turn.stream, "suppressor", None)
        if engine.want_chain and not active:
            _diag.info("Echo live turn %s: %.1f s, PC playing %.0f%% of it, chain switches %s so far, "
                       "echo %s after its reference (pairing moved %.0f ms in all), lost microphone "
                       "audio filled %d times (%.0f ms), drift corrections %d (%.0f ms), %s",
                       device_id, engine.hops * 256 / 16000,
                       100 * engine.playing_hops / engine.hops if engine.hops else 0, engine.switches,
                       "not measured" if live.echo_delay is None else f"{live.echo_delay / 16:.0f} ms",
                       -live.realigned / 16, live.fills, live.filled / 16, live.moves, live.moved / 16,
                       suppressor.summary(reset=True) if suppressor else "no residual echo suppression")
        if suppressor:
            suppressor.enabled = bool(active)  # standby: LocalVQE alone, as "Orion" is listened for
        turn = live.turn
        if isinstance(turn, Turn) and engine.want_chain and not active and live.turn_energy is not None:
            mic, out = (turn.energy[k] - live.turn_energy[k] for k in ('mic', 'out'))
            if mic > 0 and out > 0:
                _diag.info("Echo live turn %s: microphone %.1f dB -> cleaned %.1f dB (the canceller took "
                           "out %.1f dB, the user's own voice included)", device_id,
                           10 * np.log10(mic / max(1, turn.counted - live.turn_counted)),
                           10 * np.log10(out / max(1, turn.counted - live.turn_counted)),
                           10 * np.log10(mic / out))
        if active and not engine.want_chain and isinstance(turn, Turn):
            live.turn_energy, live.turn_counted = dict(turn.energy), turn.counted
        if active and not engine.want_chain:
            engine.hops = engine.playing_hops = 0
            if suppressor:
                suppressor.summary(reset=True)  # standby's counts are dropped
        engine.want_chain = bool(active)

    def close_live(self, device_id):
        with self._device_locks.get(device_id + "#live", threading.Lock()):
            live = self._live.pop(device_id, None)
        if live is not None:
            seconds = live.turn.received / 16000
            engine = live.engine
            _diag.info("Echo live stream %s closed: %.0f s, cpu %.1f s (%.0f%% of a core), chain switches %s",
                       device_id, seconds, live.cpu, 100 * live.cpu / seconds if seconds else 0,
                       engine.switches if engine else "n/a (passthrough)")

    def _new_live(self, device_id, arrived):
        if self._capture is None or self._engine_factory is None or self._vqe_factory is None:
            return _Live(_PassThrough(), None, arrived)
        from .engines import AdaptiveEngine
        chain_factory, vqe_factory = self._engine_factory, self._vqe_factory
        stream = self._keyed_stream(self._live_streams, device_id,
                                    lambda: AdaptiveEngine(vqe_factory(), chain_factory))
        stream.engine.want_chain = False
        if getattr(stream, "suppressor", None):
            stream.suppressor.enabled = False
        return _Live(Turn(stream, self.ring, self.ring.index_at(arrived), clock=self.clock), stream.engine, arrived)

    def _keyed_stream(self, streams, device_id, factory):
        if self._capture is None or factory is None:
            return None
        with self._lock:
            stream = streams.get(device_id)
            if stream is None:
                stream = self._device_stream(factory())
                streams[device_id] = stream
                while len(streams) > self.max_devices:
                    _, old = streams.popitem(last=False)
                    old.close()
            streams.move_to_end(device_id)
            return stream

    def _device_stream(self, engine):
        if not self.suppress:
            return DeviceStream(engine)
        from ..residual_echo import ResidualEchoSuppressor
        return DeviceStream(engine, suppressor=ResidualEchoSuppressor())

    def end_turn(self, device_id):
        """Close the turn; returns the remaining cleaned samples. Total output == total input."""
        with self._device_locks.get(device_id, threading.Lock()):
            return to_pcm16(self._finish(device_id))

    def forget_device(self, device_id):
        with self._lock:
            stream = self._streams.pop(device_id, None)
            self._turns.pop(device_id, None)
        if stream is not None:
            stream.close()

    def close(self):
        self.disarm()
        for device_id in list(self._streams):
            self.forget_device(device_id)
        for streams in (self._checkers, self._live_streams):
            while streams:
                streams.popitem()[1].close()

    def status(self):
        capture = self._capture
        return {"armed": capture is not None,
                "ready": self._engine_factory is not None,
                "unavailable": self.unavailable,
                "reference_device": getattr(capture, "device_name", None),
                "devices": list(self._streams)}

    def _feed(self, turn, mic):
        c0 = time.thread_time()
        out = turn.feed(mic)
        turn.cpu += time.thread_time() - c0
        return out

    def _finish(self, device_id):
        turn = self._turns.pop(device_id, None)
        if turn is None:
            return np.zeros(0, np.float32)
        c0 = time.thread_time()
        out = turn.finish()
        stream = turn.stream
        stats = self.last_turn[device_id] = {
            "samples": turn.received,
            "processed_frames": getattr(stream, "processed_frames", 0),
            "cpu_seconds": round(turn.cpu + time.thread_time() - c0, 3),
            "wall_seconds": round(self.clock() - turn.started, 3),
            "cancelled": stream is not None,
        }
        counted = getattr(turn, "counted", 0)
        if counted:
            level = {k: round(10 * float(np.log10(v / counted + 1e-12)), 1) for k, v in turn.energy.items()}
            stats.update(mic_db=level["mic"], reference_db=level["ref"], cleaned_db=level["out"])
        # One line per turn: whether the reference had sound and how much the canceller took out
        # (the user's own speech is in both mic and cleaned, so this is not a pure echo figure).
        suppressor = getattr(stream, "suppressor", None)
        _diag.info("Echo turn %s: %s s, reference %s dBFS (%s), mic %s dBFS -> cleaned %s dBFS, cpu %s s, %s",
                   device_id, round(turn.received / 16000, 1), stats.get("reference_db"),
                   getattr(self._capture, "device_name", None) or "no loopback", stats.get("mic_db"),
                   stats.get("cleaned_db"), stats["cpu_seconds"],
                   suppressor.summary(reset=True) if suppressor else "no residual echo suppression")
        return out

    def _turn_engine(self):
        """A phone turn's engine: as for the PC microphone, LocalVQE while the PC is silent and the
        chain while it plays (warmed on the last second); the chain alone without LocalVQE."""
        if self._vqe_factory is None:
            return self._engine_factory()
        from .engines import AdaptiveEngine
        engine = AdaptiveEngine(self._vqe_factory(), self._engine_factory)
        engine.want_chain = True  # a phone's stream only runs inside turns
        return engine

    def _stream(self, device_id):
        if self._capture is None or self._engine_factory is None:
            return None
        with self._lock:
            stream = self._streams.get(device_id)
            if stream is None:
                stream = self._device_stream(self._turn_engine())
                self._streams[device_id] = stream
                while len(self._streams) > self.max_devices:
                    _, old = self._streams.popitem(last=False)
                    old.close()
            self._streams.move_to_end(device_id)
            return stream
