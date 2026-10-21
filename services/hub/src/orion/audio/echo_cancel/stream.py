"""Turn-level streaming around an echo-cancel engine; pure numpy, engine-agnostic.

An engine has `process(mic, ref) -> out` on whole 256-sample frames, an output `latency` in
samples and `close()`. `DeviceStream` keeps one engine per phone across turns (no reset, so the
delay estimate and model states stay warm) and switches between the engine and the raw mic:
the engine only runs while the reference had sound within the last `hangover` seconds (the echo
reaches the mic up to ~1 s after the reference). Raw audio is delayed by the same latency, and
switches crossfade over one frame, so the output timeline never jumps.

`Turn` turns a phone's contiguous mic samples into output samples aligned one to one with the
input (output[k] belongs to input[k]): it pairs mic sample k with the reference captured at
`ref_start + k` (unaligned; the engine finds the echo delay), waits for the reference to be
captured, and drops the engine's latency at the start and flushes it at the end.
"""
from collections import deque
import time

import numpy as np

HOP = 256
RATE = 16000


class DeviceStream:
    def __init__(self, engine, threshold=10 ** (-65 / 20), hangover=1.2):
        self.engine = engine
        self.latency = int(engine.latency)
        self.threshold = threshold
        self._raw = np.zeros(self.latency, np.float32)
        self._peaks = deque(maxlen=max(1, round(hangover * RATE / HOP)))
        self._fade = np.linspace(0, 1, HOP, endpoint=False, dtype=np.float32)
        self.state = "bypass"  # bypass -> priming -> active -> bypass
        self._priming = 0
        self.processed_frames = 0
        self.frames = 0

    def frame(self, mic, ref):
        """One 256-sample frame in, one out (lagging by `latency`)."""
        raw = np.concatenate([self._raw, mic])
        self._raw, raw = raw[HOP:], raw[:HOP]
        self._peaks.append(float(np.max(np.abs(ref))) if len(ref) else 0.0)
        wanted = max(self._peaks) > self.threshold
        self.frames += 1
        if self.state == "bypass" and not wanted:
            return raw
        out = self.engine.process(mic, ref)
        self.processed_frames += 1
        if self.state == "bypass":
            # The engine's output frame k covers input [256k - latency, 256(k+1) - latency): it is
            # stale (older than this activation) until frame ceil(latency / 256) + 1.
            self.state, self._priming = "priming", -(-self.latency // HOP) + 1
        if self.state == "priming":
            self._priming -= 1
            if self._priming > 0:
                return raw
            self.state = "active"
            return raw * (1 - self._fade) + out * self._fade
        if not wanted:
            self.state = "bypass"
            return out * (1 - self._fade) + raw * self._fade
        return out

    def close(self):
        self.engine.close()


class Turn:
    def __init__(self, stream, ring, ref_start, clock=time.perf_counter, stall=0.3):
        self.stream = stream
        self.ring = ring
        self.ref_start = ref_start  # absolute ring index paired with mic sample 0
        self.clock = clock
        self.stall = stall  # after this long, a reference the ring still lacks counts as silence
        self._pending = np.zeros(0, np.float32)
        self._consumed = 0  # mic samples already turned into frames
        self._drop = stream.latency
        self.received = 0
        self.emitted = 0

    def feed(self, mic):
        mic = np.asarray(mic, np.float32).ravel()
        self.received += len(mic)
        self._pending = np.concatenate([self._pending, mic])
        return self._run(final=False)

    def finish(self):
        """Flush: pad the last partial frame and the engine latency with zeros, trim to the input."""
        missing = self.received - self.emitted
        pad = (-len(self._pending)) % HOP + -(-self.stream.latency // HOP) * HOP
        self._pending = np.concatenate([self._pending, np.zeros(pad, np.float32)])
        return self._run(final=True)[:missing]

    def _run(self, final):
        outs = []
        while len(self._pending) >= HOP:
            start = self.ref_start + self._consumed
            ref = self._reference(start, final)
            if ref is None:
                break
            outs.append(self.stream.frame(self._pending[:HOP], ref))
            self._pending = self._pending[HOP:]
            self._consumed += HOP
        out = np.concatenate(outs) if outs else np.zeros(0, np.float32)
        if self._drop:
            cut = min(self._drop, len(out))
            out, self._drop = out[cut:], self._drop - cut
        if not final:
            self.emitted += len(out)
        return out

    def _reference(self, start, final):
        end = self.ring.end
        if end >= start + HOP:
            return self.ring.read(start, HOP)
        if not final and self.clock() - self.ring.time_of(start + HOP) < self.stall:
            return None  # not captured yet; the next feed will retry
        ref = np.zeros(HOP, np.float32)
        if end > start:
            ref[:end - start] = self.ring.read(start, end - start)
        return ref


def to_float(pcm16):
    return np.frombuffer(pcm16, "<i2").astype(np.float32) / 32768


def to_pcm16(x):
    return (np.clip(x, -1, 32767 / 32768) * 32768).round().astype("<i2").tobytes()
