"""Residual echo suppression after the canceller, per frequency band.

DTLN and LocalVQE leave quiet, speech-shaped traces of the PC's playback; a quiet microphone's
gain can lift them enough for Silero to hear a video's voice as the user. A whole 20 ms frame
lowered when the cleaned audio is far below the raw microphone (the old gate) also cut the
user's soft word endings, which LocalVQE had already weakened. Here mainly the bands the echo
dominates are lowered:

- Delay: the reference reaches the microphone some hops later (loudspeaker, room, browser).
  Band-power envelopes of the reference at each lag are correlated with the raw microphone
  over a few seconds; the best lag is taken when it clearly wins, then refined to the sample
  by the phase of the aligned cross-spectrum. The canceller's output is aligned with the raw
  microphone, so the same delay holds for it.
- The user, per band: the raw microphone above the echo path's usual coupling (raw mic /
  aligned reference) by VOICE_DB. Such bands are held as the user's for a while.
- Expected residual per band = aligned reference power (with a decaying room tail) x a leak
  factor learned per band while the user is silent (the hop lowered by ECHO_ONLY_DB in all, the
  band not the user's). It tracks an upper quantile; bands with only echo take it x4.
- Coherence of the cleaned output with the aligned reference per band: what the canceller left
  of the playback still follows it; the user's voice does not.
- The larger of the two echo shares sets the band's gain, down to -20 dB, smoothed over time
  and across neighbouring bands so no "musical" tones appear.
- The canceller's lapses (a burst 10+ dB above the learned leak, e.g. right after the user
  stops): a hop lowered by GATE_DB in all while the raw microphone showed the user in at most
  GATE_MAX_VOICE bands lately goes to the floor, except those bands. On recordings this kept
  the leak under Silero's threshold where the band rule alone did not, at a fraction of the
  old gate's cost to the user's speech.

While the reference has been silent for REF_HANGOVER hops nothing is computed: the output is the
input, delayed. One instance per stream (a phone, the PC microphone), kept across turns so the
delay and the learned profile stay. Output lags the input by `latency` (one hop, the STFT overlap).
"""
import numpy as np

HOP = 256
FFT = 2 * HOP
BINS = FFT // 2 + 1
REF_THRESHOLD = 10 ** (-65 / 20)  # as AdaptiveEngine: a reference peak above this is playing
REF_HANGOVER = 75  # hops (~1.2 s) of silent reference before suppression stops
MAX_LAG = 64  # hops (~1 s) of reference history searched for the echo delay
MIN_GAIN = 10 ** (-20 / 20)
VOICE_DB = 6.0  # raw mic this far above the echo path's usual coupling: the user in that band
VOICE_HOLD = 12  # hops (~190 ms) a band stays the user's for the band rule
ECHO_ONLY_DB = 10.0  # the canceller lowered the hop this much in all: learn the leak
OVERESTIMATE = 1.0  # expected residual x this in the user's bands
OVERESTIMATE_ECHO = 4.0  # and in bands with only echo, so the residual's peaks are covered
LEAK_UP, LEAK_DOWN = .05, .01  # per hop, toward a higher / lower ratio: about the 83rd percentile
TAIL = .6  # per hop: how fast the room's echo tail of the aligned reference fades
COHERENCE_FLOOR = .25  # coherence of unrelated signals over the smoothing window
GATE_DB = 12.0  # the whole-hop rule: lowered this much in all ...
GATE_MAX_VOICE = 6  # ... with at most this many bands of the user ...
GATE_HOLD = 20  # ... within this many hops (~320 ms)
SPREAD = [.1, .8, .1]  # gain smoothing across neighbouring bands
DOWN, UP = .6, .7  # gain smoothing in time, per hop


def _band_edges(count=32):
    """Bin edges of `count` bands, about mel-spaced, at least two bins wide."""
    mel = np.linspace(0, 2595 * np.log10(1 + 8000 / 700), count + 1)
    edges = np.round(700 * (10 ** (mel / 2595) - 1) / 8000 * (BINS - 1)).astype(int)
    for i in range(1, count):
        edges[i] = max(edges[i], edges[i - 1] + 2)
    edges[-1] = BINS
    return edges


EDGES = _band_edges()
BANDS = len(EDGES) - 1
_STARTS = EDGES[:-1]
_WIDTH = np.diff(EDGES).astype(np.float64)
_BAND_OF_BIN = np.repeat(np.arange(BANDS), np.diff(EDGES))
_WINDOW = np.sqrt(np.hanning(FFT + 1)[:FFT]).astype(np.float64)  # periodic: w^2 overlap-adds to 1
_WINDOW2 = _WINDOW ** 2
_EPS = 1e-12


class ResidualEchoSuppressor:
    latency = HOP

    def __init__(self):
        self._out = np.zeros(FFT)
        self._mic = np.zeros(FFT)
        self._ref = np.zeros(FFT + MAX_LAG * HOP)  # sample history: frames at any delay
        self._ola = np.zeros(FFT)
        self._ref_log = np.full((MAX_LAG, BANDS), np.log(1e-10))  # reference band power, newest first
        self._mean_ref = None
        self._mean_mic = None
        self._score = np.zeros(MAX_LAG)
        self.delay = None  # samples from the reference to the microphone, once found
        self._scored = 0
        self._cross = np.zeros(BINS, complex)  # mic x aligned reference, for the sub-hop delay
        self._s_yr = np.zeros(BINS, complex)
        self._s_yy = np.zeros(BINS)
        self._s_rr = np.zeros(BINS)
        self._tail = np.zeros(BANDS)
        self.leak = np.full(BANDS, -40.0)  # dB, residual / aligned reference power, per band
        self.coupling = np.full(BANDS, -10.0)  # dB, raw mic / aligned reference power, per band
        self._voice_hold = np.zeros(BANDS, int)  # hops each band still counts as the user's
        self._voice_gate = np.zeros(BANDS, int)  # the same, for the whole-hop rule
        self._learned = np.zeros(BANDS, int)  # hops each band's leak was updated on
        self._gain = np.ones(BANDS)
        self._silent = REF_HANGOVER
        self.enabled = True  # False: measure and learn, but pass the audio unchanged
        self.stats = _Stats()

    def process(self, out, mic, ref):
        """Cleaned output, raw microphone and reference, aligned with each other, whole hops in;
        the suppressed output out, `latency` samples later."""
        result = np.empty(len(out), np.float32)
        for k in range(0, len(out), HOP):
            result[k:k + HOP] = self._hop(out[k:k + HOP], mic[k:k + HOP], ref[k:k + HOP])
        return result

    def _hop(self, out, mic, ref):
        for buf, new in ((self._out, out), (self._mic, mic), (self._ref, ref)):
            buf[:-HOP] = buf[HOP:]
            buf[-HOP:] = new
        playing = float(np.max(np.abs(ref))) > REF_THRESHOLD if len(ref) else False
        self._silent = 0 if playing else self._silent + 1
        self.stats.hops += 1
        if self._silent >= REF_HANGOVER and np.all(self._gain >= 1):
            # Nothing playing: the frame passes unchanged (w^2 overlap-adds to 1). The reference
            # history only needs to know it was silent.
            self._ref_log[1:] = self._ref_log[:-1]
            self._ref_log[0] = np.log(1e-10)
            self._tail[:] = 0
            return self._emit(self._out * _WINDOW2)
        self.stats.active += 1
        y = np.fft.rfft(self._out * _WINDOW)
        m = np.fft.rfft(self._mic * _WINDOW)
        r = np.fft.rfft(self._ref[-FFT:] * _WINDOW)
        py, pm = _bands(y), _bands(m)
        self._track_delay(pm, _bands(r), playing)
        if self.delay is None:
            gain = np.ones(BANDS)
        else:
            gain = self._suppression(y, m, py, pm)
        if self._silent >= REF_HANGOVER or not self.enabled:
            gain = np.ones(BANDS)
        # Smooth across neighbouring bands, then in time.
        gain = np.convolve(np.pad(gain, 1, mode="edge"), SPREAD, mode="valid")
        rate = np.where(gain < self._gain, DOWN, UP)
        self._gain = np.minimum(1.0, self._gain + rate * (gain - self._gain))
        self._gain[self._gain > .999] = 1.0
        g = self._gain[_BAND_OF_BIN]
        cleaned = np.fft.irfft(y * g, FFT) * _WINDOW
        e_in, e_out = float(np.sum(py)), float(np.sum(py * self._gain ** 2))
        if playing and e_in > _EPS:
            self.stats.suppression += 10 * np.log10(e_out / e_in + _EPS)
            self.stats.suppressed += 1
        return self._emit(cleaned)

    def _emit(self, frame):
        self._ola += frame
        done = self._ola[:HOP].astype(np.float32)
        self._ola[:-HOP] = self._ola[HOP:]
        self._ola[-HOP:] = 0
        return done

    def _track_delay(self, pm, pr, playing):
        """Coarse delay in hops from band envelopes, then to the sample from the cross-phase."""
        lr, lm = np.log(pr + 1e-10), np.log(pm + 1e-10)
        self._ref_log[1:] = self._ref_log[:-1]
        self._ref_log[0] = lr
        if not playing:
            return
        if self._mean_ref is None:
            self._mean_ref, self._mean_mic = lr.copy(), lm.copy()
        self._mean_ref += .02 * (lr - self._mean_ref)
        self._mean_mic += .02 * (lm - self._mean_mic)
        zm = lm - self._mean_mic
        zr = self._ref_log - self._mean_ref
        corr = zr @ zm / (np.sqrt(np.sum(zr * zr, axis=1) * np.dot(zm, zm)) + _EPS)
        self._score += .01 * (corr - self._score)  # ~1.6 s
        self._scored += 1
        if self._scored < 40:
            return
        best = int(np.argmax(self._score))
        coarse = None if self.delay is None else min(MAX_LAG - 1, int(round(self.delay / HOP)))
        if self._score[best] < .1:
            return
        if coarse is None or (best != coarse and self._score[best] > self._score[coarse] + .05):
            self.delay = best * HOP
            self._cross[:] = 0
            self.stats.delay_changes += 1

    def _aligned(self):
        """The reference frame `delay` samples back, and its band power."""
        end = len(self._ref) - self.delay
        r = np.fft.rfft(self._ref[end - FFT:end] * _WINDOW)
        return r, _bands(r)

    def _refine(self, m, ra):
        """Move `delay` by the residual offset the phase of mic x aligned reference shows."""
        self._cross += .05 * (m * np.conj(ra) - self._cross)
        if self.stats.active % 16:
            return
        phat = self._cross / (np.abs(self._cross) + _EPS)
        lag = np.fft.irfft(phat, FFT)
        k = int(np.argmax(lag))
        offset = k if k < HOP else k - FFT  # mic later than the aligned reference: positive
        if lag[k] > .3 and abs(offset) >= 8:
            limit = MAX_LAG * HOP - HOP
            self.delay = int(min(max(self.delay + offset // 2, 0), limit))  # half a step: slow
            self._cross *= 0

    def _suppression(self, y, m, py, pm):
        ra, pa = self._aligned()
        playing_now = self._silent == 0
        if playing_now:
            self._refine(m, ra)
        self._tail = np.maximum(pa, self._tail * TAIL)
        ref_total = float(np.sum(self._tail))
        usable = (self._tail > 1e-4 * max(float(self._tail.max()), _EPS)) & (self._tail > 1e-9 * _WIDTH)
        # The echo path's coupling (raw mic / aligned reference, a running median per band): a
        # raw microphone well above it has the user in that band.
        coupling = 10 * np.log10((pm + _EPS) / (self._tail + _EPS))
        if playing_now:
            self.coupling = np.where(usable, self.coupling + np.where(coupling > self.coupling, .25, -.25),
                                     self.coupling)
        voice = coupling > self.coupling + VOICE_DB
        self._voice_hold = np.where(voice, VOICE_HOLD, np.maximum(self._voice_hold - 1, 0))
        self._voice_gate = np.where(voice, GATE_HOLD, np.maximum(self._voice_gate - 1, 0))
        # Learn the leak on hops the canceller lowered a lot in all, in bands without the user.
        erle = 10 * np.log10((float(np.sum(pm)) + _EPS) / (float(np.sum(py)) + _EPS))
        user_silent = playing_now and ref_total > _EPS and np.sum(voice & usable) < .25 * np.sum(usable)
        if user_silent:
            self.stats.echo_only += 1
            self.stats.erle += erle
        if user_silent and erle > ECHO_ONLY_DB:
            usable &= ~voice
            ratio = 10 * np.log10((py + _EPS) / (self._tail + _EPS))
            step = np.where(ratio > self.leak, LEAK_UP, LEAK_DOWN)
            self.leak = np.where(usable, self.leak + step * (ratio - self.leak), self.leak)
            self._learned += usable
        over = np.where(self._voice_hold > 0, OVERESTIMATE, OVERESTIMATE_ECHO)
        expected = over * 10 ** (self.leak / 10) * self._tail
        share_leak = np.where(self._learned > 20, expected / (py + _EPS), 0.0)
        # Coherence of the cleaned output with the aligned reference.
        self._s_yr += .3 * (y * np.conj(ra) - self._s_yr)
        self._s_yy += .3 * (np.abs(y) ** 2 - self._s_yy)
        self._s_rr += .3 * (np.abs(ra) ** 2 - self._s_rr)
        coh = np.abs(self._s_yr) ** 2 / (self._s_yy * self._s_rr + _EPS)
        coh = np.add.reduceat(coh, _STARTS) / _WIDTH
        share_coh = np.clip((coh - COHERENCE_FLOOR) / (1 - COHERENCE_FLOOR), 0, 1)
        share = np.maximum(np.minimum(share_leak, 1.0), share_coh)
        # The canceller's lapses: the hop goes to the floor, except the user's few bands.
        if playing_now and erle > GATE_DB and np.sum(self._voice_gate > 0) <= GATE_MAX_VOICE:
            share = np.where(self._voice_hold > 0, share, 1.0)
            self.stats.gated += 1
        return np.maximum(np.sqrt(1 - share), MIN_GAIN)  # power share -> amplitude gain

    def summary(self, reset=False):
        """For the turn logs: what the canceller took out, what this stage took out, the profile."""
        s = self.stats
        if self.delay is None:
            text = "residual echo: no delay yet"
        else:
            low, mid, high = (np.mean(self.leak[(EDGES[:-1] >= a) & (EDGES[:-1] < b)])
                              for a, b in ((0, 32), (32, 128), (128, BINS)))
            text = (f"canceller removed {s.erle / s.echo_only if s.echo_only else 0:.0f} dB "
                    f"(avg of {s.echo_only} hops without the user), residual suppression avg "
                    f"{s.suppression / s.suppressed if s.suppressed else 0:.1f} dB on {s.suppressed} "
                    f"playing hops ({100 * s.gated / s.suppressed if s.suppressed else 0:.0f}% gated), "
                    f"delay {self.delay / 16:.0f} ms, leak <1k {low:.0f} / 1-4k {mid:.0f} / "
                    f"4-8k {high:.0f} dB")
        if reset:
            delay_changes = s.delay_changes
            self.stats = _Stats()
            self.stats.delay_changes = delay_changes
        return text


class _Stats:
    def __init__(self):
        self.hops = self.active = self.echo_only = self.suppressed = self.delay_changes = self.gated = 0
        self.erle = self.suppression = 0.0


def _bands(spectrum):
    return np.add.reduceat(spectrum.real ** 2 + spectrum.imag ** 2, _STARTS)
