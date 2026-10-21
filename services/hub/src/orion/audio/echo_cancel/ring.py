"""Timestamped ring of the PC's loudspeaker output (16 kHz mono float32), the echo reference.

Samples are addressed by an absolute index on a timeline anchored to `time.perf_counter()`:
index = (t - origin) * rate. WASAPI loopback sends nothing while the PC plays nothing, so a
writer that falls behind the clock is padded with zeros (silence) and `advance()` pads idle
stretches; if the device clock runs ahead of the PC clock the origin is moved instead, so the
timeline never drifts by more than `slack` seconds.
"""
import threading
import time

import numpy as np

RATE = 16000


class ReferenceRing:
    def __init__(self, seconds=3.0, rate=RATE, clock=time.perf_counter, slack=0.05):
        self.rate = rate
        self.capacity = int(seconds * rate)
        self.clock = clock
        self.slack = int(slack * rate)
        self._buf = np.zeros(self.capacity, np.float32)
        self._end = 0  # absolute index one past the newest sample
        self._origin = None  # clock time of absolute index 0
        self._lock = threading.Lock()
        self.gap_samples = 0

    # --- writer side (loopback capture thread) ---

    def write(self, samples, now=None):
        """Append samples whose last one was captured at `now`."""
        x = np.asarray(samples, np.float32).ravel()
        now = self.clock() if now is None else now
        with self._lock:
            if self._origin is None:
                self._origin = now - (self._end + len(x)) / self.rate
            expected_end = round((now - self._origin) * self.rate)
            behind = expected_end - (self._end + len(x))
            if behind > self.slack:
                self._append(np.zeros(behind, np.float32))  # nothing played in between
                self.gap_samples += behind
            elif behind < -2 * self.slack:
                self._origin = now - (self._end + len(x)) / self.rate  # device clock ahead: re-anchor
            self._append(x)

    def advance(self, now=None):
        """Pad silence up to `now` minus the slack; call periodically while nothing is written."""
        now = self.clock() if now is None else now
        with self._lock:
            if self._origin is None:
                self._origin = now - self._end / self.rate
            behind = round((now - self._origin) * self.rate) - self.slack - self._end
            if behind > 0:
                self._append(np.zeros(behind, np.float32))
                self.gap_samples += behind

    def _append(self, x):
        if len(x) > self.capacity:
            self._end += len(x) - self.capacity  # the head would be overwritten anyway
            x = x[-self.capacity:]
        start = self._end % self.capacity
        first = min(len(x), self.capacity - start)
        self._buf[start:start + first] = x[:first]
        self._buf[:len(x) - first] = x[first:]
        self._end += len(x)

    # --- reader side (echo canceller) ---

    def index_at(self, t):
        with self._lock:
            if self._origin is None:
                self._origin = t - self._end / self.rate
            return round((t - self._origin) * self.rate)

    def time_of(self, index):
        with self._lock:
            if self._origin is None:
                return self.clock()
            return self._origin + index / self.rate

    @property
    def end(self):
        with self._lock:
            return self._end

    def read(self, start, n):
        """Samples [start, start+n). Indices older than the ring hold zeros; must not pass `end`."""
        out = np.zeros(n, np.float32)
        with self._lock:
            if start + n > self._end:
                raise ValueError("reference not captured yet")
            lo = max(start, self._end - self.capacity, 0)
            if lo < start + n:
                idx = np.arange(lo, start + n) % self.capacity
                out[lo - start:] = self._buf[idx]
        return out
