"""PCM16 at any rate to 24 kHz mono, continuous across arbitrary transport chunks.

Every output sample is a windowed-sinc (Kaiser) interpolation of the input around its exact position, with
the cut below the lower of the two Nyquists: going down (48 or 44.1 kHz) nothing above 12 kHz folds back into
the voice as hiss, going up (16 or 8 kHz) no images of the voice are added. The position is kept as an integer
ratio, so there is no drift, and an output sample depends only on the input around it, so chunk boundaries do
not change a single byte. Before the first and after the last sample the edge sample is held: no click at the
start or end of a sentence.
"""
import numpy as np

TARGET = 24000
_LOBES = 16  # Zero crossings of the sinc on each side.
_BETA = 8.6  # Kaiser window: about 85 dB stop band.
_BATCH = 4096  # Output samples computed at once (bounds memory).


class PcmNormalizer:
    def __init__(self, rate, channels):
        if rate <= 0 or channels not in (1, 2):
            raise ValueError("Unsupported PCM format")
        self.rate, self.channels = rate, channels
        self.pending = b""
        self.frames = 0  # Input frames seen.
        self.output_frames = 0
        # The pass band ends at 90% of the lower Nyquist (10.8 kHz for 24 kHz out); in input samples, the
        # sinc's zero crossings are 1 / cut apart.
        self.cut = 0.9 * min(rate, TARGET) / rate
        self.width = int(np.ceil(_LOBES / self.cut))  # Taps on each side, in input samples.
        self.taps = np.arange(-self.width + 1, self.width + 1)
        # The output position's fraction takes only TARGET / gcd values: one row of weights for each.
        from math import gcd
        step = gcd(rate, TARGET)
        distance = (np.arange(0, TARGET, step) / TARGET)[:, None] - self.taps
        window = np.i0(_BETA * np.sqrt(np.clip(1 - (distance / self.width) ** 2, 0, None))) / np.i0(_BETA)
        weights = self.cut * np.sinc(self.cut * distance) * window
        self.weights = weights / weights.sum(axis=1, keepdims=True)  # Unity gain at every fraction.
        self.step = step
        self.buffer = np.zeros(0)
        self.offset = 0  # Input index of buffer[0] (negative while the held first sample pads the start).

    def _mono(self, data):
        values = np.frombuffer(data, dtype="<i2").astype(np.float64)
        return values if self.channels == 1 else values.reshape(-1, 2).mean(axis=1)

    def feed(self, data, *, final=False):
        data = self.pending + data
        width = self.channels * 2
        aligned = len(data) // width * width
        self.pending = data[aligned:]
        if final and self.pending:
            raise ValueError("Incomplete PCM frame")
        mono = self._mono(data[:aligned])
        if self.rate == TARGET:
            return np.clip(np.round(mono), -32768, 32767).astype("<i2").tobytes()
        if len(mono):
            if not self.frames:
                self.buffer = np.full(self.width, mono[0])
                self.offset = -self.width
            self.buffer = np.concatenate([self.buffer, mono])
            self.frames += len(mono)
        if not self.frames:
            return b""
        total = self.frames * TARGET // self.rate  # The stream never exceeds its final length.
        if final:
            self.buffer = np.concatenate([self.buffer, np.full(self.width + 1, self.buffer[-1])])
            end = total
        else:
            # Output n needs input up to floor(n * rate / 24000) + width.
            end = min(total, max(0, -(-(self.frames - self.width) * TARGET // self.rate)))
        result = []
        while self.output_frames < end:
            n = np.arange(self.output_frames, min(end, self.output_frames + _BATCH), dtype=np.int64)
            base, remainder = np.divmod(n * self.rate, TARGET)
            weights = self.weights[remainder // self.step]
            samples = self.buffer[(base - self.offset)[:, None] + self.taps]
            result.append((samples * weights).sum(axis=1))
            self.output_frames = int(n[-1]) + 1
        # Keep only what the next output sample can still reach.
        keep_from = self.output_frames * self.rate // TARGET - self.width + 1
        drop = max(0, min(keep_from - self.offset, len(self.buffer)))
        self.buffer = self.buffer[drop:]
        self.offset += drop
        if not result:
            return b""
        return np.clip(np.round(np.concatenate(result)), -32768, 32767).astype("<i2").tobytes()
