"""Timestamped ring of the PC's loudspeaker output (16 kHz mono float32), the echo reference.

Samples are addressed by an absolute index on a timeline anchored to `time.perf_counter()`:
index = (t - origin) * rate, the newest sample being "now". WASAPI loopback sends nothing while
the PC plays nothing: after such a pause (no callback for `gap` seconds) the missing stretch is
zeros, and `advance()` pads idle time as it passes. A merely late callback is not a pause: its
data follows in a burst, so only a lag that persists for a whole second is padded (device clock
slower than the PC's), and a writer running ahead is re-anchored (device clock faster).
"""
import threading
import time

import numpy as np

RATE = 16000


class ReferenceRing:
    def __init__(self, seconds=3.0, rate=RATE, clock=time.perf_counter, slack=0.05, gap=0.15, window=1.0):
        self.rate = rate
        self.capacity = int(seconds * rate)
        self.clock = clock
        self.slack = int(slack * rate)
        self.gap = gap
        self.window = window
        self._last_write = None
        self._window_start = None
        self._window_lag = None
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
            behind = round((now - self._origin) * self.rate) - (self._end + len(x))
            paused = self._last_write is None or now - len(x) / self.rate - self._last_write > self.gap
            self._last_write = now
            if paused and behind > 0:
                self._pad(behind)  # nothing played in between
            elif behind < -2 * self.slack:
                self._origin = now - (self._end + len(x)) / self.rate
            else:
                if self._window_start is None or now - self._window_start > self.window:
                    if self._window_lag is not None and self._window_lag > self.slack:
                        lag = self._window_lag
                        self._pad(lag)
                        behind -= lag
                    self._window_start, self._window_lag = now, behind
                self._window_lag = min(self._window_lag, behind)
            self._append(x)

    def advance(self, now=None):
        """Pad silence up to `now` minus the slack; call periodically while nothing is written."""
        now = self.clock() if now is None else now
        with self._lock:
            if self._origin is None:
                self._origin = now - self._end / self.rate
            if self._last_write is not None and now - self._last_write <= self.gap:
                return  # the device is sending; late data is still on its way
            behind = round((now - self._origin) * self.rate) - self.slack - self._end
            if behind > 0:
                self._pad(behind)

    def _pad(self, n):
        self._append(np.zeros(n, np.float32))
        self.gap_samples += n
        self._window_start = self._window_lag = None

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
