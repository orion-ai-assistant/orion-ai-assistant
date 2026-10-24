"""Loudness for echo-cancelled turns, decided by the raw microphone.

The PC assistant used to lift the cleaned audio up to 64x by its own level.
After echo cancellation the PC's playback leaves quiet, speech-shaped
leftovers; raising them made Silero take a video's voice for the user. Here
the gain follows the RAW microphone: while the PC is loud the microphone is
loud too, so the gain stays low; a quiet room and voice still get lifted.

What is left of the echo is lowered per frequency band before this gain
(`residual_echo.ResidualEchoSuppressor`). That alone was not enough live: a
quiet microphone's gain (~60x) lifted the leftovers of a speech video back
above Silero's threshold. So while the PC plays (`apply(gate=True)`), a 20 ms
frame the canceller turned much quieter than the raw microphone is the PC's
echo, not the user, and is lowered instead of lifted. Soft sounds inside words
look the same for a moment, so the gate waits for 160 ms of echo in a row,
keeps off for 300 ms after the user's voice, and ramps: no "dotted" voice. It
never runs while the PC is silent.

Gains are applied to the cleaned samples of the same time.
"""
import numpy as np

FRAME = 320  # 20 ms at 16 kHz
TARGET_RMS = .04  # the PC normalizer's target level
CEILING = 64.0
NOISE_FLOOR = .00012  # below this the room is silent: no lift
RELEASE = .97  # per frame: ~0.7 s, the PC normalizer's envelope release
ECHO_DROP_DB = 15.0  # cleaned this far below raw: an echo frame
ECHO_GAIN = .1  # echo is lowered by 20 dB instead of lifted
ECHO_RUN = 8  # 160 ms of echo frames in a row before lowering: soft sounds inside words stay
VOICE_DB = 6.0  # cleaned this close to raw: the user is speaking
VOICE_HOLD = 15  # frames (300 ms) after the user's voice the gate stays off: word endings stay
DOWN_STEP = 3.0  # the gain falls at most 3x (~10 dB) per frame: ~80 ms to the echo level
UP_STEP = 10.0  # ... and rises 10x per frame, so the user's next syllable is not swallowed


class RawLevelGain:
    """Gain for one stream (a turn, or the PC microphone's continuous stream):
    `observe()` the raw microphone, `apply()` it to the cleaned audio. Both are
    counted from the stream's first sample; frames already applied are dropped,
    so a stream can run for hours."""

    def __init__(self, ceiling=CEILING):
        self.ceiling = ceiling
        self._gains = []  # lift per raw frame, from frame `_base` on
        self._raw = []  # raw RMS per frame, from frame `_base` on
        self._base = 0
        self._partial = np.zeros(0, np.float32)
        self._envelope = 0.0
        self._applied = 0  # cleaned samples already scaled
        self._lift = [0.0, 0, 0.0]  # sum, count, max of the lifts since the last summary
        self._run = 0  # echo frames in a row
        self._voice_hold = 0  # frames left in which the user's word ending is protected
        self._decided = -1
        self._echo = False
        self._target = None  # this frame's gain once decided
        self._previous = None  # the last frame's gain, the ramp's start
        self.echo_frames = 0  # frames lowered as echo since the last summary
        self.gated_frames = 0  # frames the gate looked at (the PC playing) since the last summary

    def observe(self, pcm):
        pcm = pcm[:len(pcm) // 2 * 2]
        raw = np.concatenate([self._partial, np.frombuffer(pcm, '<i2').astype(np.float32) / 32768])
        whole = len(raw) // FRAME * FRAME
        for frame in raw[:whole].reshape(-1, FRAME):
            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            self._raw.append(rms)
            self._envelope = max(rms, self._envelope * RELEASE)
            level = self._envelope
            lift = 1.0 if level < NOISE_FLOOR else min(max(TARGET_RMS / level, 1.0), self.ceiling)
            self._gains.append(lift)
            self._lift[0] += lift
            self._lift[1] += 1
            self._lift[2] = max(self._lift[2], lift)
        self._partial = raw[whole:]

    def apply(self, pcm, gate=False):
        """Cleaned PCM16 (in order, aligned with the raw input) -> scaled PCM16. `gate`: the PC
        is playing, so a run of echo frames is lowered instead of lifted."""
        if len(pcm) % 2:
            return pcm  # not PCM16 (a test stand-in); nothing to scale
        x = np.frombuffer(pcm, '<i2').astype(np.float32)
        if not len(x):
            return pcm
        out = np.empty_like(x)
        start = 0
        while start < len(x):
            index = (self._applied + start) // FRAME
            end = min(len(x), start + FRAME - (self._applied + start) % FRAME)
            first, last = self._frame_gain(index, x[start:end], gate)
            out[start:end] = x[start:end] * (np.linspace(first, last, end - start, endpoint=False,
                                                         dtype=np.float32) if first != last else last)
            start = end
        self._applied += len(x)
        done = min(self._applied // FRAME - 1 - self._base, len(self._gains))  # keep the current frame
        if done > 256:
            del self._gains[:done], self._raw[:done]
            self._base += done
        return np.clip(np.round(out), -32768, 32767).astype('<i2').tobytes()

    def _frame_gain(self, index, cleaned, gate):
        """(gain at the piece's start, gain at its end): a frame's first piece ramps from the last
        frame's gain to its own."""
        if index - self._base >= len(self._gains):  # cleaned audio ahead of the raw: keep the last lift
            lift = self._gains[-1] if self._gains else 1.0
            return lift, lift
        if index != self._decided:  # decide once per frame, on its first piece
            self._decided = index
            raw = self._raw[index - self._base]
            rms = float(np.sqrt(np.mean((cleaned.astype(np.float64) / 32768) ** 2)))
            # No room-noise floor: a quiet microphone (~-65 dBFS) hears the PC's echo below it.
            voiced = gate and raw >= 1e-5 and rms >= raw * 10 ** (-VOICE_DB / 20)
            self._voice_hold = VOICE_HOLD if voiced else max(0, self._voice_hold - 1)
            echo = gate and raw >= 1e-5 and rms < raw * 10 ** (-ECHO_DROP_DB / 20) and not self._voice_hold
            self._run = self._run + 1 if echo else 0
            self._echo = self._run >= ECHO_RUN
            self.gated_frames += gate
            self.echo_frames += self._echo
            lift = self._gains[index - self._base]
            target = min(ECHO_GAIN, lift) if self._echo else lift
            first = target if self._previous is None else self._previous
            self._target = min(max(target, first / DOWN_STEP), first * UP_STEP)
            self._previous = self._target
            return first, self._target
        return self._target, self._target

    def summary(self, reset=False):
        """Gain since the start (or the last `reset`)."""
        total, count, peak = self._lift
        text = (f'gain avg {total / count if count else 1.0:.1f} max {peak if count else 1.0:.1f}, '
                f'echo gate lowered {self.echo_frames} of {self.gated_frames} frames while the PC played')
        if reset:
            self._lift = [0.0, 0, 0.0]
            self.echo_frames = self.gated_frames = 0
        return text
