"""Bounded, continuous PCM16 resampling across arbitrary transport chunks."""
import struct


class PcmNormalizer:
    def __init__(self, rate, channels):
        self.rate, self.channels = rate, channels
        self.pending = b""
        self.samples = []
        self.offset = 0
        self.frames = 0
        self.output_frames = 0

    def feed(self, data, *, final=False):
        data = self.pending + data
        width = self.channels * 2
        aligned = len(data) // width * width
        self.pending = data[aligned:]
        if final and self.pending:
            raise ValueError("Incomplete PCM frame")
        values = struct.unpack(f"<{aligned // 2}h", data[:aligned])
        mono = values if self.channels == 1 else [
            (values[i] + values[i + 1]) / 2 for i in range(0, len(values), 2)
        ]
        self.samples.extend(mono)
        self.frames += len(mono)
        result = bytearray()
        while self.samples:
            # Integer phase avoids drift and makes chunk boundaries irrelevant.
            position, fraction = divmod(self.output_frames * self.rate, 24000)
            # The stream never exceeds its final length, even before the final chunk.
            if self.output_frames >= self.frames * 24000 // self.rate:
                break
            if not final and position + 1 >= self.frames:
                break
            left = self.samples[position - self.offset]
            right = self.samples[min(position + 1, self.frames - 1) - self.offset]
            value = round(left + (right - left) * fraction / 24000)
            result.extend(struct.pack("<h", max(-32768, min(32767, value))))
            self.output_frames += 1
        keep_from = min(self.output_frames * self.rate // 24000, max(0, self.frames - 1))
        discard = max(0, keep_from - self.offset)
        del self.samples[:discard]
        self.offset += discard
        return bytes(result)
