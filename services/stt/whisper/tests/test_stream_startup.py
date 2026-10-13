"""Exercise the real streaming handler without loading a Whisper model."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import patch

import numpy as np
from fastapi import WebSocketDisconnect

spec = importlib.util.spec_from_file_location(
    "whisper_api_under_test", Path(__file__).resolve().parents[1] / "api.py"
)
api = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]):
    spec.loader.exec_module(api)


class Socket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.events = []

    async def accept(self):
        pass

    async def receive(self):
        try:
            return {"type": "websocket.receive", "bytes": next(self.chunks)}
        except StopIteration:
            return {"type": "websocket.disconnect"}

    async def send_json(self, event):
        self.events.append(event)

    async def close(self):
        pass


class Model:
    def __init__(self):
        self.durations = []
        self.options = []

    def detect_language(self, **kwargs):
        return "tr", 0.99, [("tr", 0.99)]

    def transcribe(self, audio, **kwargs):
        self.durations.append(len(audio) / api.RATE)
        self.options.append(kwargs)
        # Recognizer succeeds; verify that the handler doesn't discard speech.
        segment = SimpleNamespace(text="Merhaba", no_speech_prob=0.01)
        return iter([segment]), SimpleNamespace(language="tr")


def speech(samples, seconds=1.0, amplitude=0.05):
    count = int(api.RATE * seconds / samples)
    phase = np.arange(samples) / api.RATE
    chunk = (np.sin(2 * np.pi * 250 * phase) * amplitude * 32767).astype(np.int16).tobytes()
    return [chunk] * count


class StreamStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_stream_detects_language_and_selects_turkish_prompt(self):
        socket = Socket(speech(1024))
        model = Model()
        with patch.object(api, "model", model):
            await api.websocket_endpoint(socket)
        self.assertTrue(model.options)
        for options in model.options:
            self.assertEqual(options["task"], "transcribe")
            self.assertEqual(options["language"], "tr")
            self.assertIn("Hmm... Naber? Şey, aslında şöyle desem...", options["initial_prompt"])

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
        # Empty final events acknowledge completed spans without publishing text.
        self.assertTrue(all(event.get("text", "") == "" for event in events))

    async def test_speech_after_initial_silence_is_preserved(self):
        events, _ = await self.run_stream([bytes(1024 * 2)] * 3 + speech(1024))
        self.assertTrue(events)
        self.assertEqual(events[0]["text"], "Merhaba")


if __name__ == "__main__":
    unittest.main()
