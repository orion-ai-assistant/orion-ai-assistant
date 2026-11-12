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

    def test_a_pause_keeps_the_voice_lift_instead_of_raising_the_noise(self):
        gain = RawLevelGain()
        voice, pause = pcm(.005, 50), pcm(.0003, 150)  # a quiet phone: voice, then 3 s of room noise
        gain.observe(voice)
        gain.observe(pause)
        voice_lift = gain._gains[49]
        self.assertAlmostEqual(max(gain._gains[50:]), voice_lift, places=3)
        self.assertLess(voice_lift, 20)  # not the 64x the noise alone would get

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

    def test_while_the_pc_plays_a_quiet_microphone_lifts_the_leftover_little(self):
        # The PC mic hears Orion's voice at only ~-62 dBFS: the raw level alone lifted it ~50x.
        echo, leftover = pcm(.0008, 50), pcm(.0003, 50)
        playing = RawLevelGain()
        playing.observe(echo, playing=True)
        self.assertLessEqual(rms(playing.apply(leftover)), 4.01 * rms(leftover))
        silent = RawLevelGain()
        silent.observe(echo)
        self.assertGreater(rms(silent.apply(leftover)), 20 * rms(leftover))  # a quiet voice still lifted

    def test_a_pause_while_the_pc_plays_keeps_no_more_than_the_playing_lift(self):
        gain = RawLevelGain()
        gain.observe(pcm(.002, 50))  # the user's quiet voice: a big lift
        gain.observe(pcm(.0001, 50), playing=True)  # a pause, then Orion speaks
        self.assertLessEqual(max(gain._gains[50:]), 4.0)

    def test_summary_reports_and_resets_the_gain(self):
        gain = RawLevelGain()
        gain.observe(pcm(.004, 10))
        self.assertTrue(gain.summary(reset=True).startswith('gain avg 14.1 max 14.1'))
        self.assertTrue(gain.summary().startswith('gain avg 1.0 max 1.0'))
