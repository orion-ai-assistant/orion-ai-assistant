import ast
import asyncio
import json
import re
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
import numpy as np

from fastapi import WebSocketDisconnect


def handler(model):
    path = Path(__file__).resolve().parents[1] / 'api.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'websocket_endpoint')
    node.decorator_list = []
    node.args.args[0].annotation = None
    namespace = dict(asyncio=asyncio, json=json, re=re, time=time, np=np, model=model, RATE=16000,
                     WebSocketDisconnect=WebSocketDisconnect, logger=SimpleNamespace(info=lambda *a: None, error=lambda *a: None),
                     MIN_AUDIO_TO_PROCESS=.6, MIN_TRANSCRIBE_INTERVAL=.2, SILENCE_DURATION_TO_END=.4,
                     FINAL_BEAM_SIZE=4, LIVE_BEAM_SIZE=2, last_debug_audio=np.array([], dtype=np.float32))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['websocket_endpoint']


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.events = []
        self.closed = False
    async def accept(self):
        pass
    async def receive(self):
        return next(self.messages, {'type': 'websocket.disconnect'})
    async def send_json(self, event):
        self.events.append(event)
    async def close(self):
        self.closed = True


def control(**data):
    return {'type': 'websocket.receive', 'text': json.dumps(data)}


class SmartStreamTests(unittest.IsolatedAsyncioTestCase):
    def model(self):
        self.audio_lengths = []
        def transcribe(audio, **kwargs):
            self.audio_lengths.append(len(audio))
            return iter([SimpleNamespace(text='Merhaba.', no_speech_prob=.01)]), SimpleNamespace(language='tr')
        return SimpleNamespace(transcribe=transcribe)

    async def test_long_gap_preserves_audio_budget_and_final_ack_order(self):
        audio = (np.sin(np.arange(16000) * .1) * 2000).astype(np.int16).tobytes()
        socket = Socket([
            control(type='configure', id=7, silence_gap=1),
            {'type': 'websocket.receive', 'bytes': audio},
            control(type='gap', id=7, samples=16000 * 65),
            {'type': 'websocket.receive', 'bytes': audio},
            control(type='finish', id=7),
        ])
        await handler(self.model())(socket, language='tr')
        self.assertEqual(socket.events[0], {'type': 'ready', 'id': 7, 'silence_gap': 1})
        self.assertEqual(socket.events[-1], {'type': 'finished', 'id': 7})
        self.assertTrue(any(e['type'] == 'final' for e in socket.events))
        self.assertTrue(all(length <= 32000 for length in self.audio_lengths))

    async def test_invalid_and_excessive_gaps_fail_explicitly(self):
        for gap in (control(type='gap', id=8, samples=10),
                    control(type='gap', id=7, samples=16000 * 181),
                    control(type='gap', id=7, samples=-1)):
            socket = Socket([control(type='configure', id=7, silence_gap=1), gap])
            await handler(self.model())(socket)
            self.assertEqual(socket.events[-1]['type'], 'error')
            self.assertTrue(socket.closed)

    async def test_total_timeline_budget_cannot_be_extended_indefinitely(self):
        socket = Socket([control(type='configure', id=7, silence_gap=1),
                         control(type='gap', id=7, samples=16000 * 100),
                         control(type='gap', id=7, samples=16000 * 100)])
        await handler(self.model())(socket)
        self.assertEqual(socket.events[-1]['type'], 'error')

    async def test_legacy_finish_does_not_require_configuration(self):
        socket = Socket([control(type='finish', id=7)])
        await handler(self.model())(socket)
        self.assertEqual(socket.events, [{'type': 'finished', 'id': 7}])
