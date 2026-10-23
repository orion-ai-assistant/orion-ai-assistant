"""Turn-level streaming around an echo-cancel engine; pure numpy, engine-agnostic.

An engine has `process(mic, ref) -> out` on whole 256-sample frames, an output `latency` in
samples and `close()`. `DeviceStream` keeps one engine per phone across turns (no reset, so the
delay estimate and model states stay warm). By default the engine runs on every frame of a turn,
also while the PC is silent: a replay of the recordings showed that an engine left idle starts
cold when music begins mid-turn (-25 dB instead of -46 dB). With `gate=True` it only runs while
the reference had sound within the last `hangover` seconds and passes the raw mic otherwise. Raw
audio is delayed by the same latency, and switches crossfade over one frame, so the output
timeline never jumps. A stream that starts active (its first frame) uses the engine at once
instead of priming over raw audio.

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
    def __init__(self, engine, threshold=10 ** (-65 / 20), hangover=1.2, gate=False):
        self.engine = engine
        self.gate = gate
        self.latency = int(engine.latency)
        self.threshold = threshold
        self._raw = np.zeros(self.latency, np.float32)
        self._peaks = deque(maxlen=max(1, round(hangover * RATE / HOP)))
        self._fade = np.linspace(0, 1, HOP, endpoint=False, dtype=np.float32)
        self.state = "bypass"  # bypass -> priming -> active -> bypass
        self._priming = 0
        self.processed_frames = 0
        self.frames = 0
        self._steps = 0

    def process(self, mic, ref):
        """Whole 256-sample frames in, as many out (lagging by `latency`). The engine runs on the
        contiguous runs of frames that need it, so a long block (the pre-roll) is one call."""
        n = len(mic) // HOP
        raw = np.concatenate([self._raw, mic])
        self._raw, raw = raw[len(mic):], raw[:len(mic)]
        plan = [self._step(ref[f * HOP:(f + 1) * HOP]) for f in range(n)]
        self.frames += n
        out = raw.copy()
        f = 0
        while f < n:
            if plan[f] == "raw":
                f += 1
                continue
            g = f
            while g < n and plan[g] != "raw":
                g += 1
            engine = self.engine.process(mic[f * HOP:g * HOP], ref[f * HOP:g * HOP])
            self.processed_frames += g - f
            for k in range(f, g):
                e, r = engine[(k - f) * HOP:(k - f + 1) * HOP], raw[k * HOP:(k + 1) * HOP]
                if plan[k] == "engine":
                    out[k * HOP:(k + 1) * HOP] = e
                elif plan[k] == "fade_in":
                    out[k * HOP:(k + 1) * HOP] = r * (1 - self._fade) + e * self._fade
                elif plan[k] == "fade_out":
                    out[k * HOP:(k + 1) * HOP] = e * (1 - self._fade) + r * self._fade
            f = g
        return out

    def _step(self, ref):
        """Advance the bypass/priming/active state by one frame; return what that frame outputs."""
        self._peaks.append(float(np.max(np.abs(ref))))
        first, self._steps = self._steps == 0, self._steps + 1
        wanted = not self.gate or max(self._peaks) > self.threshold
        if self.state == "bypass" and not wanted:
            return "raw"
        if self.state == "bypass" and first:
            # The very first frame: the engine's latency head lines up with the Turn's dropped
            # latency, so nothing stale shows. A raw prefix here was ~20-50 ms of full echo.
            self.state = "active"
            return "engine"
        if self.state == "bypass":
            # The engine's output frame k covers input [256k - latency, 256(k+1) - latency): it is
            # stale (older than this activation) until frame ceil(latency / 256) + 1.
            self.state, self._priming = "priming", -(-self.latency // HOP) + 1
        if self.state == "priming":
            self._priming -= 1
            if self._priming > 0:
                return "prime"  # engine runs, raw goes out
            self.state = "active"
            return "fade_in"
        if not wanted:
            self.state = "bypass"
            return "fade_out"
        return "engine"

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
        # Energy sums for the turn's diagnostic line (mic in, reference, cleaned out).
        self.energy = {'mic': 0.0, 'ref': 0.0, 'out': 0.0}
        self.counted = 0

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
        frames = len(self._pending) // HOP
        start = self.ref_start + self._consumed
        captured = self.ring.end
        ready = min(frames, max(0, (captured - start) // HOP))
        if final:
            ready = frames
        else:
            # A reference the ring still lacks after `stall` seconds counts as silence (capture stopped).
            while ready < frames and self.clock() - self.ring.time_of(start + (ready + 1) * HOP) >= self.stall:
                ready += 1
        out = np.zeros(0, np.float32)
        if ready:
            n = ready * HOP
            ref = np.zeros(n, np.float32)
            have = min(n, max(0, captured - start))
            if have:
                ref[:have] = self.ring.read(start, have)
            out = self.stream.process(self._pending[:n], ref)
            mic = self._pending[:n].astype(np.float64)
            self.energy['mic'] += float(np.dot(mic, mic))
            self.energy['ref'] += float(np.dot(ref.astype(np.float64), ref.astype(np.float64)))
            self.energy['out'] += float(np.dot(out.astype(np.float64), out.astype(np.float64)))
            self.counted += n
            self._pending = self._pending[n:]
            self._consumed += n
        if self._drop:
            cut = min(self._drop, len(out))
            out, self._drop = out[cut:], self._drop - cut
        if not final:
            self.emitted += len(out)
        return out


def to_float(pcm16):
    return np.frombuffer(pcm16, "<i2").astype(np.float32) / 32768


def to_pcm16(x):
    return (np.clip(x, -1, 32767 / 32768) * 32768).round().astype("<i2").tobytes()
