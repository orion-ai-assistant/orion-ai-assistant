"""Turn-level streaming around an echo-cancel engine; pure numpy, engine-agnostic.

An engine has `process(mic, ref) -> out` on whole 256-sample frames, an output `latency` in
samples and `close()`. `DeviceStream` keeps one engine per phone across turns (no reset, so the
delay estimate and model states stay warm), and optionally a residual echo suppressor after it
(its latency adds to the stream's). By default the engine runs on every frame of a turn,
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
QUIET = 10 ** (-65 / 20)  # the reference's silence (engines.REF_THRESHOLD)
LOUD_REF = 10 ** (-45 / 10)  # mean square: the PC plays clearly
AUDIBLE = 10 ** (-70 / 10)
# The mic's clock against the reference (`Turn.ratio`).
MAX_CLOCK_ERROR = .02
MIN_CLOCK_ERROR = 2e-5  # 20 ppm: below it a fit changes nothing worth a move
CLOCK_FIT = 6  # delay estimates...
CLOCK_SPAN = 6 * RATE  # ...over at least this much mic audio
CLOCK_SPREAD = RATE * 3 // 1000  # a fit whose delays scatter more is not a clock's straight line
ARRIVAL_WINDOW = 2 * RATE
ARRIVAL_FIT = 8
ARRIVAL_SPREAD = RATE * 4 // 1000
DELAY_WINDOW = 2 * RATE  # mic audio each delay estimate looks at
DELAY_EVERY = RATE // 2
DELAY_SEARCH = RATE // 4  # an echo up to 250 ms before its pairing (the window ends this far back)
DELAY_SEARCH_LATE = RATE * 600 // 1000  # ...or up to 600 ms after it: a Bluetooth speaker adds 150-300 ms
DELAY_OK = (RATE * 5 // 1000, RATE * 80 // 1000)  # echo 5-80 ms after its reference: fine (108 leaked)
DELAY_TARGET = RATE * 30 // 1000
DELAY_AGREE = RATE * 10 // 1000
DELAY_CORRELATION = .2


class DeviceStream:
    def __init__(self, engine, threshold=10 ** (-65 / 20), hangover=1.2, gate=False, suppressor=None):
        self.engine = engine
        self.gate = gate
        self.suppressor = suppressor  # residual echo suppression on the output, if given
        self._engine_latency = int(engine.latency)
        self.latency = self._engine_latency + (suppressor.latency if suppressor else 0)
        self.threshold = threshold
        self._raw = np.zeros(self._engine_latency, np.float32)
        self._ref = np.zeros(self._engine_latency, np.float32)
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
        if self.suppressor is None:
            return out
        ref_late = np.concatenate([self._ref, ref])  # the reference of the raw samples in `out`
        self._ref, ref_late = ref_late[len(ref):], ref_late[:len(ref)]
        return self.suppressor.process(out, raw, ref_late)

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
            self.state, self._priming = "priming", -(-self._engine_latency // HOP) + 1
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
    """A turn's mic samples, cleaned against the PC's playback they hold the echo of.

    Mic sample k is paired with the reference at `_ref_pos` + k * `ratio` (ring indices, read with
    cubic interpolation between samples). Timestamps alone do not keep that pairing: a mic's clock
    runs off the PC's (the PC's Realtek microphone delivers ~0.36 % fewer samples than its playback,
    so its echo moved ~4 ms a second and passed the canceller once it came before its reference,
    2026-11-12), and buffers, padding and queues move it too. So:

    - `ratio` (reference samples per mic sample) is learned per device and kept across turns
      (`stream.clock_ratio`): from the slope of the echo's delay measured on the audio while the PC
      plays, the truth; until that is known, from the packets' arrival times against the PC's clock.
      Following it continuously keeps the pairing right through silences, so it is right when Orion
      starts to speak.
    - The echo's delay is measured on the audio every DELAY_EVERY while the PC plays; a delay outside
      DELAY_OK on two estimates in a row moves the pairing so the reference leads by DELAY_TARGET.
    """

    def __init__(self, stream, ring, ref_start, clock=time.perf_counter, stall=0.3, playing_stall=1.5):
        self.stream = stream
        self.ring = ring
        self.ref_start = ref_start  # ring index paired with mic sample 0 (plus the pairing's moves)
        self.ratio = float(getattr(stream, 'clock_ratio', 1.0) or 1.0)
        self._ref_pos = float(ref_start)  # ring position paired with the next mic sample
        self.clock = clock
        self.stall = stall  # after this long, a reference the ring still lacks counts as silence
        # ...unless the PC was just playing: then it is late, not silent. Taken for silence, Orion's own
        # voice passed the canceller untouched for half a second when the loopback capture fell ~0.3 s
        # behind at a turn's start (2026-11-12, both dumped turns at 1.3-1.8 s).
        self.playing_stall = playing_stall
        self.late = 0  # mic samples cleaned against a reference that never came in time
        # Mic samples where the PC played loudly yet the canceller took out less than 6 dB: Orion's
        # voice (or the PC's sound) leaking into the turn. Only a figure for the log.
        self.leak = 0
        self.used_ref = None  # a list to keep the reference each frame was cleaned against (dumps)
        # The echo's delay measured on the audio (`_track_delay`).
        self._mic_history = np.zeros(0, np.float32)
        self._since_estimate = 0
        self._pending_lag = None
        self._lags = []  # (mic index, delay) since the pairing last moved, for the clock's slope
        self.shifts = 0
        self.last_lag_ms = None
        # The packets' arrival against the PC's clock (`_track_arrival`).
        self._arrivals = []  # (mic samples received, least delay in a window)
        self._arrival_min = None
        self._arrival_counted = 0
        self._arrival_windows = 0
        self._arrival_origin = 0  # mic samples received when the ratio last changed
        self._pending = np.zeros(0, np.float32)
        self._consumed = 0  # mic samples already turned into frames
        self._drop = stream.latency
        self.received = 0
        self.emitted = 0
        # Energy sums for the turn's diagnostic line (mic in, reference, cleaned out).
        self.energy = {'mic': 0.0, 'ref': 0.0, 'out': 0.0}
        self.counted = 0

    @property
    def clock_ppm(self):
        """How fast the reference runs against the mic, in millionths (positive: the mic is slower)."""
        return round((self.ratio - 1) * 1e6)

    def feed(self, mic, arrival=None):
        """`arrival`: when the packet reached the Hub (`clock` time), before any queue."""
        mic = np.asarray(mic, np.float32).ravel()
        if len(mic):
            self._track_arrival(len(mic), self.clock() if arrival is None else arrival)
        self.received += len(mic)
        self._pending = np.concatenate([self._pending, mic])
        return self._run(final=False)

    def finish(self):
        """Flush: pad the last partial frame and the engine latency with zeros, trim to the input."""
        missing = self.received - self.emitted
        pad = (-len(self._pending)) % HOP + -(-self.stream.latency // HOP) * HOP
        self._pending = np.concatenate([self._pending, np.zeros(pad, np.float32)])
        return self._run(final=True)[:missing]

    @property
    def _resampling(self):
        return self.ratio != 1.0 or self._ref_pos != int(self._ref_pos)

    def _needed(self, frames):
        """Ring index one past the last sample the next `frames` frames read."""
        last = self._ref_pos + (frames * HOP - 1) * self.ratio
        return int(np.floor(last)) + (3 if self._resampling else 1)

    def _reference(self, n, captured):
        """The reference for the next `n` mic samples; what the ring does not hold yet is silence."""
        if not self._resampling:
            start = int(self._ref_pos)
            ref = np.zeros(n, np.float32)
            have = min(n, max(0, captured - start))
            if have:
                ref[:have] = self.ring.read(start, have)
            return ref
        pos = self._ref_pos + np.arange(n) * self.ratio
        base = int(np.floor(pos[0])) - 1
        span = int(np.floor(pos[-1])) + 3 - base
        block = np.zeros(span, np.float32)
        lo = max(base, 0)
        hi = min(base + span, captured)
        if hi > lo:
            block[lo - base:hi - base] = self.ring.read(lo, hi - lo)
        i = np.floor(pos).astype(np.int64) - base
        f = (pos - np.floor(pos)).astype(np.float32)
        p0, p1, p2, p3 = block[i - 1], block[i], block[i + 1], block[i + 2]
        # Catmull-Rom: smooth between samples, exact on them.
        return p1 + .5 * f * (p2 - p0 + f * (2 * p0 - 5 * p1 + 4 * p2 - p3 + f * (3 * (p1 - p2) + p3 - p0)))

    def _run(self, final):
        frames = len(self._pending) // HOP
        captured = self.ring.end
        ready = 0
        while ready < frames and self._needed(ready + 1) <= captured:
            ready += 1
        if final:
            ready = frames
        else:
            # A reference the ring still lacks after `stall` seconds counts as silence (capture stopped).
            stall = self._stall() if ready < frames else self.stall
            while ready < frames and self.clock() - self.ring.time_of(self._needed(ready + 1)) >= stall:
                ready += 1
                if self._needed(ready) > captured:
                    self.late += HOP
        out = np.zeros(0, np.float32)
        if ready:
            n = ready * HOP
            ref = self._reference(n, captured)
            self._ref_pos += n * self.ratio
            out = self.stream.process(self._pending[:n], ref)
            mic = self._pending[:n].astype(np.float64)
            mic_energy = float(np.dot(mic, mic))
            ref_energy = float(np.dot(ref.astype(np.float64), ref.astype(np.float64)))
            out_energy = float(np.dot(out.astype(np.float64), out.astype(np.float64)))
            self.energy['mic'] += mic_energy
            self.energy['ref'] += ref_energy
            self.energy['out'] += out_energy
            if (ref_energy / n > LOUD_REF and out_energy > .25 * mic_energy
                    and out_energy / max(len(out), 1) > AUDIBLE):
                self.leak += n
            if self.used_ref is not None:
                self.used_ref.append(ref.copy())
                kept = 0  # the dump keeps 90 s: so does this, also in a turn of hours
                for i in range(len(self.used_ref) - 1, -1, -1):
                    kept += len(self.used_ref[i])
                    if kept >= 90 * RATE:
                        del self.used_ref[:i]
                        break
            self.counted += n
            self._mic_history = np.concatenate([self._mic_history, self._pending[:n]])[-(DELAY_WINDOW + DELAY_SEARCH):]
            self._pending = self._pending[n:]
            self._consumed += n
            self._since_estimate += n
            if self._since_estimate >= DELAY_EVERY and not final:
                self._track_delay()
        if self._drop:
            cut = min(self._drop, len(out))
            out, self._drop = out[cut:], self._drop - cut
        if not final:
            self.emitted += len(out)
        return out

    def _set_ratio(self, ratio, source):
        self.ratio = float(np.clip(ratio, 1 - MAX_CLOCK_ERROR, 1 + MAX_CLOCK_ERROR))
        self.stream.clock_ratio = self.ratio
        self.stream.clock_source = source
        self._lags = []
        self._arrivals = []
        self._arrival_origin = self.received

    def _track_arrival(self, n, arrival):
        """Until the audio has shown the mic's clock, learn it from when packets arrive.

        `ahead` is how far the PC's clock is past the reference position paired with this packet's
        last sample. The least of it in each ARRIVAL_WINDOW stands for the mic (the others waited in a
        queue); the first window is skipped (it waits behind the "Orion" check), and the slope over
        ARRIVAL_FIT windows is the clock difference: a mic falling behind makes `ahead` grow."""
        if getattr(self.stream, 'clock_source', None) == 'audio':
            return
        paired = self._ref_pos + (len(self._pending) + n) * self.ratio
        ahead = self.ring.index_at(arrival) - paired
        self._arrival_min = ahead if self._arrival_min is None else min(self._arrival_min, ahead)
        self._arrival_counted += n
        if self._arrival_counted < ARRIVAL_WINDOW:
            return
        least, self._arrival_min, self._arrival_counted = self._arrival_min, None, 0
        self._arrival_windows += 1
        if self._arrival_windows == 1:
            return
        self._arrivals.append((self.received + n, least))
        if len(self._arrivals) < ARRIVAL_FIT:
            return
        k, a = np.array(self._arrivals[-ARRIVAL_FIT:], np.float64).T
        fit = _robust_line(k, a, ARRIVAL_SPREAD)
        if fit is None:
            self._arrivals = self._arrivals[-(ARRIVAL_FIT // 2):]  # a busy stretch: look again
            return
        slope = fit[0]
        if abs(slope) >= MIN_CLOCK_ERROR:
            # What the old ratio let slip since it was set goes back too, not only what comes: the
            # pairing was right at the turn's start (or when the ratio last changed).
            slipped = slope * (self.received + n - self._arrival_origin)
            self._set_ratio(self.ratio + slope, 'arrival')
            self._ref_pos += slipped
            self.ref_start += slipped
        else:
            self._arrivals = self._arrivals[-(ARRIVAL_FIT // 2):]

    def skip(self, samples):
        """`samples` of mic audio were dropped before they got here: pair what follows past them."""
        self._ref_pos += samples * self.ratio
        self._mic_history = np.zeros(0, np.float32)
        self._lags = []

    def _move(self, samples):
        self._ref_pos += samples
        self.ref_start += samples
        self.shifts += 1
        self._pending_lag = None
        self._lags = []
        self._mic_history = np.zeros(0, np.float32)

    def _track_delay(self):
        """Measure the echo's delay on the audio while the PC plays; keep it inside DELAY_OK and learn
        the mic's clock from how it moves (see the class)."""
        self._since_estimate = 0
        if len(self._mic_history) < DELAY_WINDOW + DELAY_SEARCH:
            return
        # The window ends DELAY_SEARCH before the newest mic sample: the reference up to DELAY_SEARCH
        # after it is already captured (the newest frame's reference just was).
        b = int(round(self._ref_pos - DELAY_SEARCH * self.ratio))
        a = b - int(round(DELAY_WINDOW * self.ratio))
        if a - DELAY_SEARCH_LATE < max(0, self.ring.end - self.ring.capacity) or b + DELAY_SEARCH > self.ring.end:
            return
        ref = self.ring.read(a - DELAY_SEARCH_LATE, (b - a) + DELAY_SEARCH_LATE + DELAY_SEARCH).astype(np.float64)
        middle = ref[DELAY_SEARCH_LATE:DELAY_SEARCH_LATE + (b - a)]
        if float(np.mean(middle ** 2)) <= LOUD_REF:
            self._pending_lag = None
            return
        mic = self._mic_history[:DELAY_WINDOW].astype(np.float64)
        mic = mic - mic.mean()
        size = 1 << int(np.ceil(np.log2(len(ref) + len(mic))))
        corr = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(mic, size)),
                            size)[:DELAY_SEARCH_LATE + DELAY_SEARCH + 1]
        # corr[t] = sum mic[n] * ref[n + t]; mic[n] holds the echo of reference index a + n - lag.
        t = int(np.argmax(np.abs(corr)))
        lag = DELAY_SEARCH_LATE - t
        strength = abs(corr[t]) / np.sqrt(float(np.dot(mic, mic)) * float(np.dot(middle, middle)) + 1e-20)
        if strength < DELAY_CORRELATION:
            return
        self.last_lag_ms = round(1000 * lag / RATE, 1)
        self._learn_clock(self._consumed - DELAY_SEARCH - DELAY_WINDOW // 2, lag)
        if DELAY_OK[0] <= lag <= DELAY_OK[1]:
            self._pending_lag = None
            if getattr(self.stream, 'clock_source', None) == 'audio':
                self._since_estimate = -DELAY_EVERY  # settled: look half as often (half the CPU)
            return
        if self._pending_lag is None or abs(lag - self._pending_lag) > DELAY_AGREE:
            self._pending_lag = lag
            return
        self._move(DELAY_TARGET - lag)

    def _learn_clock(self, k, lag):
        """The delay drifts by (ratio - true ratio) per mic sample: its slope corrects the ratio."""
        self._lags.append((k, lag))
        if len(self._lags) < CLOCK_FIT or k - self._lags[0][0] < CLOCK_SPAN:
            return
        ks, lags = np.array(self._lags, np.float64).T
        fit = _robust_line(ks, lags, CLOCK_SPREAD)
        if fit is None:
            self._lags = self._lags[len(self._lags) // 2:]  # scattered (speech over it): look again
            return
        slope = fit[0]
        if abs(slope) >= MIN_CLOCK_ERROR:
            self._set_ratio(self.ratio - slope, 'audio')
        else:
            self.stream.clock_source = 'audio'  # the ratio holds
            self._lags = self._lags[len(self._lags) // 2:]

    def _stall(self):
        end = self.ring.end
        n = min(RATE // 2, end)
        if n and float(np.max(np.abs(self.ring.read(end - n, n)))) > QUIET:
            return self.playing_stall
        return self.stall


def _robust_line(x, y, spread):
    """(slope, offset) of the straight line through most of the points, or None when they do not lie
    on one: the point furthest off is dropped while one is off by more than 3 x `spread` (an estimate
    taken over speech, a reflection), keeping at least two thirds; the rest must scatter less than
    `spread`."""
    keep = np.ones(len(x), bool)
    while True:
        slope, offset = np.polyfit(x[keep], y[keep], 1)
        off = np.where(keep, np.abs(y - (slope * x + offset)), -1.0)
        worst = int(np.argmax(off))
        if off[worst] <= 3 * spread:
            break
        keep[worst] = False
        if keep.sum() < max(3, -(-2 * len(x) // 3)):
            return None
    if float(np.std(y[keep] - (slope * x[keep] + offset))) > spread:
        return None
    return float(slope), float(offset)


def to_float(pcm16):
    return np.frombuffer(pcm16, "<i2").astype(np.float32) / 32768


def to_pcm16(x):
    return (np.clip(x, -1, 32767 / 32768) * 32768).round().astype("<i2").tobytes()
