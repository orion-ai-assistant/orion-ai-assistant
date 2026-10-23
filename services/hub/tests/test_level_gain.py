import unittest

import numpy as np

from orion.audio.level_gain import FRAME, RawLevelGain


def pcm(level, frames):
    tone = level * np.sin(np.arange(frames * FRAME) * 2 * np.pi * 200 / 16000)
    return (tone * 32768).round().astype('<i2').tobytes()


def rms(data):
    x = np.frombuffer(data, '<i2').astype(np.float64) / 32768
    return float(np.sqrt(np.mean(x ** 2)))


class RawLevelGainTests(unittest.TestCase):
    def test_loud_microphone_keeps_cleaned_leftovers_quiet(self):
        gain = RawLevelGain()
        gain.observe(pcm(.1, 50))  # the PC's playback reaches the microphone loudly
        leftover = pcm(.001, 50)  # what the canceller left of it
        self.assertLess(rms(gain.apply(leftover)), 2 * rms(leftover))

    def test_quiet_room_and_voice_are_lifted(self):
        gain = RawLevelGain()
        gain.observe(pcm(.004, 50))
        out = gain.apply(pcm(.004, 50))
        self.assertGreater(rms(out), 5 * rms(pcm(.004, 50)))
        self.assertLessEqual(max(gain._gains), 64)

    def test_silence_is_not_lifted(self):
        gain = RawLevelGain()
        gain.observe(bytes(FRAME * 2 * 10))
        self.assertEqual(gain.apply(pcm(.00005, 10)), pcm(.00005, 10))

    def test_cleaned_audio_follows_the_raw_timeline(self):
        gain = RawLevelGain()
        gain.observe(pcm(.004, 25) + pcm(.2, 25))  # quiet, then loud
        first, second = gain.apply(pcm(.004, 25)), gain.apply(pcm(.004, 25))
        self.assertGreater(rms(first), rms(second))  # the loud part gets less gain

    def test_cleaned_audio_ahead_of_the_raw_uses_the_last_gain(self):
        gain = RawLevelGain()
        gain.observe(pcm(.004, 5))
        self.assertGreater(rms(gain.apply(pcm(.004, 10))), rms(pcm(.004, 10)))

    def test_echo_frames_are_lowered_not_lifted(self):
        gain = RawLevelGain(gate=True)
        gain.observe(pcm(.01, 20))  # the microphone hears the PC
        leftover = pcm(.0005, 20)  # 26 dB quieter after the canceller: echo
        self.assertLess(rms(gain.apply(leftover)), rms(leftover))
        self.assertGreater(gain.echo_frames, 15)

    def test_gate_off_counts_echo_frames_but_lifts_them(self):
        gain = RawLevelGain(gate=False)
        gain.observe(pcm(.01, 20))
        leftover = pcm(.0005, 20)
        self.assertGreater(rms(gain.apply(leftover)), rms(leftover))
        self.assertGreater(gain.echo_frames, 15)
        self.assertIn('echo gate off: would lower', gain.summary(reset=True))
        self.assertEqual((gain.echo_frames, gain.audible, gain.frames), (0, 0, 0))

    def test_the_users_voice_through_the_canceller_is_lifted(self):
        gain = RawLevelGain(gate=True)
        gain.observe(pcm(.01, 20))
        voice = pcm(.008, 20)  # nearly unchanged by the canceller: the user
        self.assertGreater(rms(gain.apply(voice)), rms(voice))
        self.assertEqual(gain.echo_frames, 0)
