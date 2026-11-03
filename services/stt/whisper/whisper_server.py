"""whisper.cpp's whisper-server under the STT service.

The service (api.py) keeps its WebSocket, prompts and filters; the model calls go to whisper-server, a child
process on a loopback port of its own. `WhisperServer.transcribe` and `.detect_language` take and return what
faster-whisper's did (segments with `text` and `no_speech_prob`, an info with `language`), so the code around
them did not change when the engine did.

One engine for every card: Bileşenler installs whisper-server built for CUDA (NVIDIA), Vulkan (any card) or the
processor, and the model is one ggml file (`ggml-*.bin`). Voice activity detection is whisper.cpp's own (the
Silero ggml file next to the server, `ggml-silero-*.bin`).
"""
import io
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

logger = logging.getLogger(__name__)

RATE = 16000
EXE = 'whisper-server.exe' if os.name == 'nt' else 'whisper-server'
READY_TIMEOUT = 300  # A big model loads for minutes from a slow disk.

# whisper.cpp names a detected language in full ("turkish"); Orion speaks in codes. The table of
# whisper.cpp v1.9.5 (src/whisper.cpp, g_lang): the same 100 languages as stt_languages.json.
LANGUAGES = {
    'en': 'english', 'zh': 'chinese', 'de': 'german', 'es': 'spanish', 'ru': 'russian', 'ko': 'korean',
    'fr': 'french', 'ja': 'japanese', 'pt': 'portuguese', 'tr': 'turkish', 'pl': 'polish', 'ca': 'catalan',
    'nl': 'dutch', 'ar': 'arabic', 'sv': 'swedish', 'it': 'italian', 'id': 'indonesian', 'hi': 'hindi',
    'fi': 'finnish', 'vi': 'vietnamese', 'he': 'hebrew', 'uk': 'ukrainian', 'el': 'greek', 'ms': 'malay',
    'cs': 'czech', 'ro': 'romanian', 'da': 'danish', 'hu': 'hungarian', 'ta': 'tamil', 'no': 'norwegian',
    'th': 'thai', 'ur': 'urdu', 'hr': 'croatian', 'bg': 'bulgarian', 'lt': 'lithuanian', 'la': 'latin',
    'mi': 'maori', 'ml': 'malayalam', 'cy': 'welsh', 'sk': 'slovak', 'te': 'telugu', 'fa': 'persian',
    'lv': 'latvian', 'bn': 'bengali', 'sr': 'serbian', 'az': 'azerbaijani', 'sl': 'slovenian', 'kn': 'kannada',
    'et': 'estonian', 'mk': 'macedonian', 'br': 'breton', 'eu': 'basque', 'is': 'icelandic', 'hy': 'armenian',
    'ne': 'nepali', 'mn': 'mongolian', 'bs': 'bosnian', 'kk': 'kazakh', 'sq': 'albanian', 'sw': 'swahili',
    'gl': 'galician', 'mr': 'marathi', 'pa': 'punjabi', 'si': 'sinhala', 'km': 'khmer', 'sn': 'shona',
    'yo': 'yoruba', 'so': 'somali', 'af': 'afrikaans', 'oc': 'occitan', 'ka': 'georgian', 'be': 'belarusian',
    'tg': 'tajik', 'sd': 'sindhi', 'gu': 'gujarati', 'am': 'amharic', 'yi': 'yiddish', 'lo': 'lao',
    'uz': 'uzbek', 'fo': 'faroese', 'ht': 'haitian creole', 'ps': 'pashto', 'tk': 'turkmen', 'nn': 'nynorsk',
    'mt': 'maltese', 'sa': 'sanskrit', 'lb': 'luxembourgish', 'my': 'myanmar', 'bo': 'tibetan', 'tl': 'tagalog',
    'mg': 'malagasy', 'as': 'assamese', 'tt': 'tatar', 'haw': 'hawaiian', 'ln': 'lingala', 'ha': 'hausa',
    'ba': 'bashkir', 'jw': 'javanese', 'su': 'sundanese', 'yue': 'cantonese',
}
CODES = {name: code for code, name in LANGUAGES.items()}

# faster-whisper's Silero defaults, used when a call turns VAD on without saying how.
VAD_DEFAULTS = {'threshold': .5, 'min_speech_duration_ms': 0, 'min_silence_duration_ms': 2000, 'speech_pad_ms': 400}


def decode_audio(source, sampling_rate=RATE):
    """Any audio file a client sends (wav, mp3, ogg, m4a, webm...) as mono float32 at `sampling_rate`."""
    import av
    chunks = []
    with av.open(source) as container:
        resampler = av.AudioResampler(format='s16', layout='mono', rate=sampling_rate)
        for frame in container.decode(audio=0):
            for piece in resampler.resample(frame):
                chunks.append(piece.to_ndarray().reshape(-1))
        for piece in resampler.resample(None):
            chunks.append(piece.to_ndarray().reshape(-1))
    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def wav_bytes(audio):
    """16 kHz mono 16-bit WAV of float32 samples (what whisper-server reads without ffmpeg)."""
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype('<i2')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


def multipart(fields, audio):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + wav_bytes(audio) + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def find(folder, pattern):
    found = sorted(Path(folder).rglob(pattern)) if folder and Path(folder).is_dir() else []
    return found[0] if found else None


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class WhisperServer:
    """whisper-server running `model` from `engine_dir` on `hardware` ('cuda', 'vulkan' or 'cpu')."""

    def __init__(self, model, engine_dir, hardware='vulkan', port=None, threads=None):
        self.exe = find(engine_dir, EXE)
        if self.exe is None:
            raise RuntimeError(f'whisper-server is not in {engine_dir}')
        if not Path(model).is_file():
            raise RuntimeError(f'Model file not found: {model}')
        self.model, self.hardware = str(model), hardware
        self.vad = find(engine_dir, 'ggml-silero-*.bin')
        self.port = port or free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        # On the processor every thread helps (12 against 6 on a Ryzen 5600X: 20% faster); on a card the
        # server's own default (4) only prepares the audio.
        threads = threads or ((os.cpu_count() or 4) if hardware == 'cpu' else 4)
        argv = [str(self.exe), '-m', self.model, '--host', '127.0.0.1', '--port', str(self.port),
                '-t', str(threads)]
        if hardware == 'cpu':
            argv.append('-ng')
        if self.vad:
            argv += ['--vad', '-vm', str(self.vad)]
        logger.info('Starting whisper-server: %s', ' '.join(argv))
        self.process = subprocess.Popen(argv, cwd=str(self.exe.parent), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.ready = False
        threading.Thread(target=self._log, daemon=True).start()
        self._wait()

    def _log(self):
        """Its start goes to the service log (why it did not start); afterwards only what went wrong: every
        request prints a dozen lines."""
        for raw in iter(self.process.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            if line and (not self.ready or any(word in line.lower() for word in ('error', 'fail', 'warn'))):
                logger.info('[whisper-server] %s', line)

    def _wait(self):
        until = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < until:
            if self.process.poll() is not None:
                raise RuntimeError(f'whisper-server stopped while starting (exit code {self.process.returncode})')
            try:
                with urllib.request.urlopen(self.url + '/health', timeout=2) as reply:
                    if json.loads(reply.read()).get('status') == 'ok':
                        self.ready = True
                        logger.info('whisper-server ready on %s (%s)', self.hardware, Path(self.model).name)
                        return
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        self.close()
        raise RuntimeError('whisper-server did not become ready in time')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _post(self, fields, audio):
        body, kind = multipart(fields, audio)
        request = urllib.request.Request(self.url + '/inference', data=body, headers={'Content-Type': kind})
        try:
            with urllib.request.urlopen(request, timeout=600) as reply:
                return json.loads(reply.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'whisper-server: {error.read().decode("utf-8", "replace")[:300]}') from None

    def detect_language(self, audio=None):
        """(code, probability, []) like faster-whisper; whisper-server does not say the probability."""
        reply = self._post({'detect_language': 'true', 'language': 'auto', 'response_format': 'json'}, audio)
        return CODES.get(reply.get('language', ''), reply.get('language')), None, []

    def transcribe(self, audio, *, task='transcribe', beam_size=5, language=None, initial_prompt=None,
                   temperature=(0.0, .2, .4, .6, .8, 1.0), vad_filter=False, vad_parameters=None,
                   no_speech_threshold=.6, log_prob_threshold=-1.0, without_timestamps=False, **_):
        """faster-whisper's call, its options mapped to whisper-server's. Not mapped (no counterpart):
        `compression_ratio_threshold` (whisper.cpp's fallback looks at the entropy instead) and
        `condition_on_previous_text` (the server never carries text between requests)."""
        if isinstance(audio, (str, os.PathLike)):
            audio = decode_audio(audio)
        temperatures = list(temperature) if isinstance(temperature, (list, tuple)) else [temperature]
        fields = {
            'language': language or 'auto', 'prompt': initial_prompt or '', 'beam_size': beam_size,
            'temperature': temperatures[0],
            'temperature_inc': temperatures[1] - temperatures[0] if len(temperatures) > 1 else 0,
            'no_speech_thold': no_speech_threshold, 'logprob_thold': log_prob_threshold,
            'no_timestamps': 'true' if without_timestamps else 'false', 'translate': str(task == 'translate').lower(),
            # verbose_json carries each segment's no_speech_prob; without the flag it would also work out every
            # language's probability, encoding the audio a second time.
            'response_format': 'verbose_json', 'no_language_probabilities': 'true',
            'vad': 'true' if vad_filter and self.vad else 'false',
        }
        if vad_filter and self.vad:
            vad = {**VAD_DEFAULTS, **(vad_parameters or {})}
            fields.update(vad_threshold=vad['threshold'], vad_min_speech_duration_ms=vad['min_speech_duration_ms'],
                          vad_min_silence_duration_ms=vad['min_silence_duration_ms'],
                          vad_speech_pad_ms=vad['speech_pad_ms'])
        reply = self._post(fields, audio)
        segments = [SimpleNamespace(text=item.get('text', ''), no_speech_prob=item.get('no_speech_prob', 0.0),
                                    avg_logprob=item.get('avg_logprob'))
                    for item in reply.get('segments', [])]
        name = reply.get('language', '')
        info = SimpleNamespace(language=language or CODES.get(name, name), duration=reply.get('duration'))
        return segments, info
