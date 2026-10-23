"""Loudness for echo-cancelled turns, decided by the raw microphone.

The PC assistant used to lift the cleaned audio up to 64x by its own level.
After echo cancellation the PC's playback leaves quiet, speech-shaped
leftovers; raising them by their own (low) level made Silero take a video's
voice for the user. Here the gain follows the RAW microphone instead: while
the PC is loud the microphone is loud too, so the gain stays low and the
leftovers stay quiet; a quiet room with a quiet voice still gets lifted.
The gain is applied to the cleaned samples of the same time.
"""
import numpy as np

FRAME = 320  # 20 ms at 16 kHz
TARGET_RMS = .04  # the PC normalizer's target level
CEILING = 64.0
NOISE_FLOOR = .00012  # below this the room is silent: no lift
RELEASE = .97  # per frame: ~0.7 s, the PC normalizer's envelope release


class RawLevelGain:
    """Per-turn gain: `observe()` the raw microphone, `apply()` it to the cleaned
    audio. Both are counted from the turn's first sample (the pre-roll's)."""

    def __init__(self, ceiling=CEILING):
        self.ceiling = ceiling
        self._gains = []  # one gain per raw frame
        self._partial = np.zeros(0, np.float32)
        self._envelope = 0.0
        self._applied = 0  # cleaned samples already scaled

    def observe(self, pcm):
        pcm = pcm[:len(pcm) // 2 * 2]
        raw = np.concatenate([self._partial, np.frombuffer(pcm, '<i2').astype(np.float32) / 32768])
        whole = len(raw) // FRAME * FRAME
        for frame in raw[:whole].reshape(-1, FRAME):
            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            self._envelope = max(rms, self._envelope * RELEASE)
            level = self._envelope
            self._gains.append(1.0 if level < NOISE_FLOOR else min(max(TARGET_RMS / level, 1.0), self.ceiling))
        self._partial = raw[whole:]

    def apply(self, pcm):
        """Cleaned PCM16 (in order, aligned with the raw input) -> scaled PCM16."""
        if len(pcm) % 2:
            return pcm  # not PCM16 (a test stand-in); nothing to scale
        x = np.frombuffer(pcm, '<i2').astype(np.float32)
        if not len(x):
            return pcm
        index = (self._applied + np.arange(len(x))) // FRAME
        last = self._gains[-1] if self._gains else 1.0
        gains = np.array([self._gains[i] if i < len(self._gains) else last for i in index], np.float32)
        self._applied += len(x)
        return np.clip(np.round(x * gains), -32768, 32767).astype('<i2').tobytes()

    def summary(self):
        if not self._gains:
            return 'gain 1.0'
        g = np.array(self._gains)
        return f'gain avg {g.mean():.1f} max {g.max():.1f}'
