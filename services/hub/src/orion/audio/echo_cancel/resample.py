"""Streaming rational resampler (polyphase windowed-sinc FIR), numpy only.

WASAPI loopback runs at the device mix rate (usually 48 kHz, sometimes 44.1 kHz); the echo
canceller needs 16 kHz. Callbacks deliver arbitrary block sizes, so the filter history and the
output phase carry over between calls; concatenating outputs equals resampling the whole signal.
"""
from math import gcd

import numpy as np


class StreamResampler:
    def __init__(self, src_rate, dst_rate, taps_per_phase=48):
        g = gcd(int(src_rate), int(dst_rate))
        self.up, self.down = int(dst_rate) // g, int(src_rate) // g
        self.taps = taps_per_phase
        n = taps_per_phase * self.up
        # Prototype low-pass at the upsampled rate; cut slightly below the lower Nyquist.
        cutoff = 0.5 / max(self.up, self.down) * 0.92
        k = np.arange(n) - (n - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * k) * np.kaiser(n, 8.0) * self.up
        # bank[p] holds the taps of phase p, reversed so a forward input window dots with it.
        self.bank = np.stack([h[p::self.up][::-1] for p in range(self.up)]).astype(np.float64)
        self.history = np.zeros(self.taps - 1, np.float64)
        self.next_out = 0  # upsampled index of the next output, relative to the start of the next input

    def process(self, x):
        x = np.asarray(x, np.float64).ravel()
        if self.up == 1 and self.down == 1:
            return x.astype(np.float32)
        buf = np.concatenate([self.history, x])
        limit = len(x) * self.up  # outputs whose newest input sample is inside this block
        count = max(0, -(-(limit - self.next_out) // self.down))
        if count:
            pos = self.next_out + self.down * np.arange(count)
            newest = pos // self.up + self.taps - 1  # index into buf
            windows = np.lib.stride_tricks.sliding_window_view(buf, self.taps)[newest - self.taps + 1]
            y = np.einsum("ij,ij->i", windows, self.bank[pos % self.up])
        else:
            y = np.zeros(0)
        self.next_out += count * self.down - limit
        self.history = buf[len(buf) - (self.taps - 1):]
        return y.astype(np.float32)
