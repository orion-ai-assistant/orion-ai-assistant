"""WASAPI loopback of the default output device into a `ReferenceRing` (Windows only).

One thread owns PyAudio (COM stays on it). It opens the loopback of the current default render
device, downmixes to mono and resamples to 16 kHz in the stream callback, pads silence while the
device sends nothing, and reopens the stream when Windows switches the default output (e.g. a
Bluetooth speaker connects) or the stream dies (device unplugged).
"""
import ctypes
import functools
import logging
import os
import threading
import time
import uuid

import numpy as np

from .resample import StreamResampler

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _com():
    """The GUID type and ole32, made once: a new ctypes class per call (this runs twice a second) is never freed."""
    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le

    return GUID, ctypes.OleDLL("ole32")


class RenderDeviceId:
    """Endpoint ID string of the default multimedia render device (None: no output device, or not Windows).

    Owned by one thread (COM stays on it) and kept for that thread's life: making and releasing the device
    enumerator twice a second leaked ~1.5 KB of native memory each time (3 MB per 2000 calls), the enumerator
    itself is reused without growth. It is made again after an error (the audio service restarted)."""

    def __init__(self):
        self._enumerator = None
        self._owns_com = False

    def _call(self, pointer, slot, argtypes, *args):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[slot])(pointer, *args)
        if result < 0:
            raise OSError(f"Windows audio HRESULT {result & 0xFFFFFFFF:#x}")

    def _release(self, pointer):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(pointer)

    def get(self):
        if os.name != "nt":
            return None
        GUID, ole = _com()
        refs = []
        try:
            if self._enumerator is None:
                # PortAudio may already have made this thread a COM apartment of another kind: then just use it.
                self._owns_com = ctypes.windll.ole32.CoInitializeEx(None, 0) >= 0 or self._owns_com
                clsid, iid = GUID("bcde0395-e52f-467c-8e3d-c4579291692e"), GUID("a95664d2-9614-4f35-a746-de8db63617e6")
                enumerator = ctypes.c_void_p()
                ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator))
                self._enumerator = enumerator
            device = ctypes.c_void_p()
            self._call(self._enumerator, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                       0, 1, ctypes.byref(device))
            refs.append(device)
            name = ctypes.c_void_p()
            self._call(device, 5, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(name))
            try:
                return ctypes.wstring_at(name)
            finally:
                ole.CoTaskMemFree(name)
        except OSError:
            self._drop()
            return None  # no output device at all
        finally:
            for ref in reversed(refs):
                self._release(ref)

    def _drop(self):
        if self._enumerator is not None:
            self._release(self._enumerator)
            self._enumerator = None

    def close(self):
        self._drop()
        if self._owns_com:
            _com()[1].CoUninitialize()
            self._owns_com = False


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
        render = RenderDeviceId()
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                if now >= next_poll:
                    next_poll = now + self.poll
                    wanted = render.get()
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
            render.close()

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
