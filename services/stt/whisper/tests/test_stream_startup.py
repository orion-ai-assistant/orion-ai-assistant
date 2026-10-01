"""Exercise the real streaming handler without loading a Whisper model."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from fastapi import WebSocketDisconnect

spec = importlib.util.spec_from_file_location(
    "whisper_api_under_test", Path(__file__).resolve().parents[1] / "api.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class Socket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.events = []

    async def accept(self):
        pass

    async def receive_bytes(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise WebSocketDisconnect()

    async def send_json(self, event):
        self.events.append(event)

    async def close(self):
        pass


class Model:
    def __init__(self):
        self.durations = []

    def transcribe(self, audio, **kwargs):
        self.durations.append(len(audio) / api.RATE)
        # Recognizer succeeds; verify that the handler doesn't discard speech.
        segment = SimpleNamespace(text="Merhaba", no_speech_prob=0.01)
        return iter([segment]), SimpleNamespace(language="tr")


def speech(samples, seconds=1.0, amplitude=0.05):
    count = int(api.RATE * seconds / samples)
    phase = np.arange(samples) / api.RATE
    chunk = (np.sin(2 * np.pi * 250 * phase) * amplitude * 32767).astype(np.int16).tobytes()
    return [chunk] * count


class StreamStartupTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, chunks):
        socket = Socket(chunks)
        model = Model()
        with patch.object(api, "model", model):
            await api.websocket_endpoint(socket, language="tr")
        return socket.events, model.durations

    async def test_speech_from_first_packet_survives_different_chunk_sizes(self):
        for samples in (320, 1024, 4096):
            with self.subTest(samples=samples):
                events, durations = await self.run_stream(speech(samples))
                self.assertTrue(durations, "Whisper should have been invoked")
                self.assertTrue(events, "First words must not be discarded as silence")
                self.assertEqual(events[0]["text"], "Merhaba")
                self.assertEqual(events[0]["type"], "live")
                self.assertLess(durations[0], 0.8)

    async def test_silence_does_not_publish_recognizer_hallucination(self):
        events, _ = await self.run_stream([bytes(1024 * 2)] * 20)
        self.assertEqual(events, [])

    async def test_speech_after_initial_silence_is_preserved(self):
        events, _ = await self.run_stream([bytes(1024 * 2)] * 3 + speech(1024))
        self.assertTrue(events)
        self.assertEqual(events[0]["text"], "Merhaba")


if __name__ == "__main__":
    unittest.main()
