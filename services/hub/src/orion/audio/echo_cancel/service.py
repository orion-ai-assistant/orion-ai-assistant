"""The echo canceller the PC-microphone relay calls (see package docstring for the contract)."""
from collections import OrderedDict
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


def _loopback(ring):
    from .loopback import LoopbackCapture
    return LoopbackCapture(ring)


class EchoCanceller:
    def __init__(self, engine_factory=None, capture_factory=_loopback, models_dir=None, ring_seconds=8.0,
                 clock=time.perf_counter, max_devices=4, vqe_factory=None, suppress=None):
        self.ring = ReferenceRing(ring_seconds, clock=clock)
        if suppress is None:  # off unless ORION_RESIDUAL_ECHO=1 (Plan A; not needed live so far)
            suppress = os.environ.get("ORION_RESIDUAL_ECHO", "0") == "1"
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
        self._ref_dump = {}  # ORION_PC_MIC_DUMP: the last turn's paired reference per device
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
            if stream is not None and hasattr(stream.engine, "new_turn"):
                stream.engine.new_turn()  # warm-ups use this turn's audio, not the last turn's end
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

    def take_ref_dump(self, device_id):
        return self._ref_dump.pop(device_id, b"")

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
        while self._checkers:
            self._checkers.popitem()[1].close()

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
        if os.environ.get("ORION_PC_MIC_DUMP") == "1" and isinstance(turn, Turn):
            start = max(turn.ref_start, self.ring.end - self.ring.capacity)
            end = min(turn.ref_start + turn.received, self.ring.end)
            ref = self.ring.read(start, end - start) if end > start else np.zeros(0, np.float32)
            self._ref_dump[device_id] = to_pcm16(ref)
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
        """A turn's engine (a phone's or this PC's microphone): LocalVQE while the PC is silent and
        the chain while it plays (warmed on the last second); the chain alone without LocalVQE."""
        if self._vqe_factory is None:
            return self._engine_factory()
        from .engines import AdaptiveEngine
        engine = AdaptiveEngine(self._vqe_factory(), self._engine_factory)
        engine.want_chain = True  # audio only comes inside turns
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
