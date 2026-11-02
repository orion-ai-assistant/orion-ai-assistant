"""The GGUF TTS engine reads tts-server's streamed WAV whole (services/tts/engines.py streamed_wav)."""
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tts'))
from engines import streamed_wav  # noqa: E402


def test_a_streamed_wav_is_read_past_its_first_part():
    samples = np.arange(58560, dtype=np.int16)
    first_part = 10800 * 2  # What the streamed header claims: only the first chunk.
    header = (b'RIFF' + struct.pack('<I', 36 + first_part) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 1, 24000, 48000, 2, 16)
              + b'data' + struct.pack('<I', first_part))
    rate, audio = streamed_wav(header + samples.tobytes())
    assert rate == 24000 and len(audio) == 58560 and audio[-1] == samples[-1]
