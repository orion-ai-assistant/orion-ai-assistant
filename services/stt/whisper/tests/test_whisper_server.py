import email.parser
import http.server
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
import wave

import numpy as np

spec = importlib.util.spec_from_file_location(
    "whisper_server_under_test", Path(__file__).resolve().parents[1] / "whisper_server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FakeServer(http.server.BaseHTTPRequestHandler):
    """whisper-server's /inference: keeps each request's form and answers what the test set."""
    requests, reply = [], {}

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        message = email.parser.BytesParser().parsebytes(
            b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + body)
        form = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                for part in message.get_payload()}
        FakeServer.requests.append(form)
        data = json.dumps(FakeServer.reply).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class WhisperServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeServer)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def client(self, vad=True):
        client = server.WhisperServer.__new__(server.WhisperServer)
        client.url = f'http://127.0.0.1:{self.http.server_address[1]}'
        client.vad = Path('ggml-silero-v6.2.0.bin') if vad else None
        FakeServer.requests.clear()
        return client

    def test_live_call_maps_faster_whisper_options(self):
        FakeServer.reply = {'language': 'turkish', 'segments': [
            {'text': ' Merhaba', 'no_speech_prob': .01}, {'text': ' dünya.', 'no_speech_prob': .7}]}
        audio = np.full(8000, .25, dtype=np.float32)
        segments, info = self.client().transcribe(
            audio, task='transcribe', beam_size=2, language='tr', vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400, min_speech_duration_ms=150,
                                threshold=0.5),
            no_speech_threshold=0.6, without_timestamps=True, condition_on_previous_text=False,
            initial_prompt='Şey...', temperature=0.0, compression_ratio_threshold=2.0, log_prob_threshold=-1.0)
        self.assertEqual([(s.text, s.no_speech_prob) for s in segments], [(' Merhaba', .01), (' dünya.', .7)])
        self.assertEqual(info.language, 'tr')
        form = {key: value for key, value in FakeServer.requests[0].items() if key != 'file'}
        self.assertEqual({key: value.decode() for key, value in form.items()}, {
            'language': 'tr', 'prompt': 'Şey...', 'beam_size': '2', 'temperature': '0.0', 'temperature_inc': '0',
            'no_speech_thold': '0.6', 'logprob_thold': '-1.0', 'no_timestamps': 'true', 'translate': 'false',
            'response_format': 'verbose_json', 'no_language_probabilities': 'true', 'vad': 'true',
            'vad_threshold': '0.5', 'vad_min_speech_duration_ms': '150', 'vad_min_silence_duration_ms': '500',
            'vad_speech_pad_ms': '400'})
        with wave.open(io.BytesIO(FakeServer.requests[0]['file'])) as sent:
            self.assertEqual((sent.getframerate(), sent.getnchannels(), sent.getsampwidth(), sent.getnframes()),
                             (16000, 1, 2, 8000))
            self.assertEqual(np.frombuffer(sent.readframes(1), '<i2')[0], 8191)

    def test_detected_language_comes_back_as_a_code(self):
        FakeServer.reply = {'text': '', 'language': 'turkish'}
        self.assertEqual(self.client().detect_language(audio=np.zeros(16000, np.float32)), ('tr', None, []))
        self.assertEqual(FakeServer.requests[0]['detect_language'], b'true')
        FakeServer.reply = {'language': 'english', 'segments': []}
        segments, info = self.client().transcribe(np.zeros(16000, np.float32))
        self.assertEqual((segments, info.language), ([], 'en'))
        form = FakeServer.requests[0]
        # faster-whisper's defaults: temperature fallback from 0 in steps of 0.2, the language detected.
        self.assertEqual((form['language'], form['temperature'], form['temperature_inc'], form['vad']),
                         (b'auto', b'0.0', b'0.2', b'false'))

    def test_without_a_vad_model_the_audio_goes_whole(self):
        FakeServer.reply = {'language': 'turkish', 'segments': []}
        self.client(vad=False).transcribe(np.zeros(16000, np.float32), language='tr', vad_filter=True)
        self.assertEqual(FakeServer.requests[0]['vad'], b'false')
        self.assertNotIn('vad_threshold', FakeServer.requests[0])

    def test_every_language_has_its_whisper_cpp_name(self):
        self.assertEqual(len(server.LANGUAGES), 100)
        self.assertEqual(len(server.CODES), 100)


if __name__ == '__main__':
    unittest.main()
