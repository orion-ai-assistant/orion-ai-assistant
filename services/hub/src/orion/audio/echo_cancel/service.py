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

    def track(self, ring, arrived):
        if not isinstance(self.turn, Turn):
            return
        deviation = ring.index_at(arrived) - (self.turn.ref_start + self.turn.received)
        if self._offset is None:
            self._offset = deviation
            return
        self._deviations.append((arrived, deviation - self._offset))
        while self._deviations and arrived - self._deviations[0][0] > DRIFT_WINDOW:
            self._deviations.popleft()
        if arrived - self.started < DRIFT_WINDOW:
            return
        drift = min(d for _, d in self._deviations)
        if abs(drift) > DRIFT_LIMIT:
            self.turn.ref_start += drift
            self._offset += drift
            self._deviations.clear()


def _loopback(ring):
    from .loopback import LoopbackCapture
    return LoopbackCapture(ring)


class EchoCanceller:
    def __init__(self, engine_factory=None, capture_factory=_loopback, models_dir=None, ring_seconds=3.0,
                 clock=time.perf_counter, max_devices=4, vqe_factory=None):
        self.ring = ReferenceRing(ring_seconds, clock=clock)
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
            live.track(self.ring, arrived)
            c0 = time.thread_time()
            out = live.turn.feed(mic)
            live.cpu += time.thread_time() - c0
            return to_pcm16(out)

    def live_turn(self, device_id, active):
        """A turn opened or closed on the PC: the chain may run while one is open."""
        live = self._live.get(device_id)
        if live is None or live.engine is None:
            return
        engine = live.engine
        if engine.want_chain and not active:
            _diag.info("Echo live turn %s: %.1f s, PC playing %.0f%% of it, chain switches %s so far",
                       device_id, engine.hops * 256 / 16000,
                       100 * engine.playing_hops / engine.hops if engine.hops else 0, engine.switches)
        if active and not engine.want_chain:
            engine.hops = engine.playing_hops = 0
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
        return _Live(Turn(stream, self.ring, self.ring.index_at(arrived), clock=self.clock), stream.engine, arrived)

    def _keyed_stream(self, streams, device_id, factory):
        if self._capture is None or factory is None:
            return None
        with self._lock:
            stream = streams.get(device_id)
            if stream is None:
                stream = DeviceStream(factory())
                streams[device_id] = stream
                while len(streams) > self.max_devices:
                    _, old = streams.popitem(last=False)
                    old.close()
            streams.move_to_end(device_id)
            return stream

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
        _diag.info("Echo turn %s: %s s, reference %s dBFS (%s), mic %s dBFS -> cleaned %s dBFS, cpu %s s",
                   device_id, round(turn.received / 16000, 1), stats.get("reference_db"),
                   getattr(self._capture, "device_name", None) or "no loopback", stats.get("mic_db"),
                   stats.get("cleaned_db"), stats["cpu_seconds"])
        return out

    def _stream(self, device_id):
        if self._capture is None or self._engine_factory is None:
            return None
        with self._lock:
            stream = self._streams.get(device_id)
            if stream is None:
                stream = DeviceStream(self._engine_factory())
                self._streams[device_id] = stream
                while len(self._streams) > self.max_devices:
                    _, old = self._streams.popitem(last=False)
                    old.close()
            self._streams.move_to_end(device_id)
            return stream
