"""WASAPI loopback of the default output device into a `ReferenceRing` (Windows only).

One thread owns PyAudio (COM stays on it). It opens the loopback of the current default render
device, downmixes to mono and resamples to 16 kHz in the stream callback, pads silence while the
device sends nothing, and reopens the stream when Windows switches the default output (e.g. a
Bluetooth speaker connects) or the stream dies (device unplugged).
"""
import ctypes
import logging
import os
import threading
import time
import uuid

import numpy as np

from .resample import StreamResampler

log = logging.getLogger(__name__)


def default_render_id():
    """Endpoint ID string of the default multimedia render device, or None."""
    if os.name != "nt":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le

    def call(pointer, slot, argtypes, *args):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[slot])(pointer, *args)
        if result < 0:
            raise OSError(f"Windows audio HRESULT {result & 0xFFFFFFFF:#x}")

    def release(pointer):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(pointer)

    ole = ctypes.OleDLL("ole32")
    # PortAudio may already have made this thread a COM apartment of another kind: then just use it.
    initialized = ctypes.windll.ole32.CoInitializeEx(None, 0) >= 0
    refs = []
    try:
        enumerator = ctypes.c_void_p()
        clsid, iid = GUID("bcde0395-e52f-467c-8e3d-c4579291692e"), GUID("a95664d2-9614-4f35-a746-de8db63617e6")
        ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator))
        refs.append(enumerator)
        device = ctypes.c_void_p()
        call(enumerator, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], 0, 1, ctypes.byref(device))
        refs.append(device)
        name = ctypes.c_void_p()
        call(device, 5, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(name))
        try:
            return ctypes.wstring_at(name)
        finally:
            ole.CoTaskMemFree(name)
    except OSError:
        return None  # no output device at all
    finally:
        for ref in reversed(refs):
            release(ref)
        if initialized:
            ole.CoUninitialize()


class LoopbackCapture:
    def __init__(self, ring, poll=0.5, tick=0.02):
        self.ring = ring
        self.poll = poll
        self.tick = tick
        self.device_name = None
        self.device_rate = None
        self.reopens = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="echo-loopback", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def _run(self):
        import pyaudiowpatch as pa
        audio = stream = None
        current = None
        next_poll = 0.0
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                if now >= next_poll:
                    next_poll = now + self.poll
                    wanted = default_render_id()
                    if wanted != current or (stream is not None and not stream.is_active()):
                        stream = self._close(stream)
                        if audio is not None:
                            audio.terminate()  # PortAudio's device list is a snapshot: re-enumerate
                        audio = pa.PyAudio()
                        current = wanted
                        try:
                            stream = self._open(audio, pa)
                        except Exception:
                            log.warning("Echo reference: no loopback device", exc_info=True)
                self.ring.advance()
                self._stop.wait(self.tick)
        finally:
            self._close(stream)
            if audio is not None:
                audio.terminate()

    def _open(self, audio, pa):
        dev = audio.get_default_wasapi_loopback()
        channels, rate = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
        resampler = StreamResampler(rate, self.ring.rate)

        def callback(data, frames, time_info, status):
            x = np.frombuffer(data, np.float32).reshape(-1, channels).mean(axis=1)
            self.ring.write(resampler.process(x), time.perf_counter())
            return None, pa.paContinue

        stream = audio.open(format=pa.paFloat32, channels=channels, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=rate // 100,
                            stream_callback=callback)
        self.device_name, self.device_rate = dev["name"], rate
        self.reopens += 1
        log.info("Echo reference: loopback of %s @ %d Hz", dev["name"], rate)
        return stream

    @staticmethod
    def _close(stream):
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        return None
