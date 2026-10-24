"""The two echo-cancel stages, both streaming at 16 kHz mono float32.

DTLN-aec 512 (breizhn/DTLN-aec, MIT): two TFLite models; block 512, shift 128. Model 1 turns the
magnitude spectra of the mic and reference blocks into a mask; model 2 turns the masked mic block
and the raw reference block into an output block that is overlap-added. The models are stateless
(LSTM states go in and out as tensors), so one pair of interpreters serves every phone; each
`Dtln` instance only owns its states and buffers.

LocalVQE v1.2 (localai-org/LocalVQE, Apache-2.0): `localvqe.dll` through ctypes, one context per
phone, fed 256-sample hops with `localvqe_process_frame_f32`. Never the batch
`localvqe_process_f32`: it resets the state on every call and primes the delay from the whole file.
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import os
import threading
from pathlib import Path

import numpy as np

HOP = 256  # LocalVQE hop; the chain works in these frames (16 ms)
DTLN_BLOCK, DTLN_SHIFT = 512, 128
DTLN_LATENCY = DTLN_BLOCK - DTLN_SHIFT  # output sample n belongs to input sample n - 384
CHAIN_LATENCY = DTLN_LATENCY + HOP  # 640 samples = 40 ms
PIPELINE_BLOCK = 4 * HOP
_PIPELINE = ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-dtln")
NATIVE = Path(__file__).resolve().parent / "native"


class DtlnModels:
    """One pair of interpreters shared by all `Dtln` streams (calls are serialised)."""

    def __init__(self, model_dir, threads=1):
        from ai_edge_litert.interpreter import Interpreter
        model_dir = Path(model_dir)
        self.i1 = Interpreter(model_path=str(model_dir / "dtln_aec_512_1.tflite"), num_threads=threads)
        self.i2 = Interpreter(model_path=str(model_dir / "dtln_aec_512_2.tflite"), num_threads=threads)
        self.i1.allocate_tensors()
        self.i2.allocate_tensors()
        self.d1, self.o1 = self.i1.get_input_details(), self.i1.get_output_details()
        self.d2, self.o2 = self.i2.get_input_details(), self.i2.get_output_details()
        self.lock = threading.Lock()

    def step(self, mic_block, ref_block, s1, s2):
        spectrum = np.fft.rfft(mic_block).astype(np.complex64)
        mic_mag = np.abs(spectrum).reshape(1, 1, -1).astype(np.float32)
        ref_mag = np.abs(np.fft.rfft(ref_block)).reshape(1, 1, -1).astype(np.float32)
        with self.lock:
            self.i1.set_tensor(self.d1[0]["index"], mic_mag)
            self.i1.set_tensor(self.d1[2]["index"], ref_mag)
            self.i1.set_tensor(self.d1[1]["index"], s1)
            self.i1.invoke()
            mask = self.i1.get_tensor(self.o1[0]["index"])
            s1 = self.i1.get_tensor(self.o1[1]["index"])
            estimate = np.fft.irfft(spectrum * mask).reshape(1, 1, -1).astype(np.float32)
            self.i2.set_tensor(self.d2[1]["index"], s2)
            self.i2.set_tensor(self.d2[0]["index"], estimate)
            self.i2.set_tensor(self.d2[2]["index"], ref_block.reshape(1, 1, -1).astype(np.float32))
            self.i2.invoke()
            block = self.i2.get_tensor(self.o2[0]["index"])
            s2 = self.i2.get_tensor(self.o2[1]["index"])
        return np.squeeze(block), s1, s2


class Dtln:
    def __init__(self, models):
        self.models = models
        self.s1 = np.zeros(models.d1[1]["shape"], np.float32)
        self.s2 = np.zeros(models.d2[1]["shape"], np.float32)
        self.mic = np.zeros(DTLN_BLOCK, np.float32)
        self.ref = np.zeros(DTLN_BLOCK, np.float32)
        self.ola = np.zeros(DTLN_BLOCK, np.float32)

    def process(self, mic, ref):
        """`len(mic) == len(ref)`, a multiple of 128. Output lags input by DTLN_LATENCY samples."""
        out = np.empty(len(mic), np.float32)
        for k in range(0, len(mic), DTLN_SHIFT):
            self.mic[:-DTLN_SHIFT] = self.mic[DTLN_SHIFT:]
            self.mic[-DTLN_SHIFT:] = mic[k:k + DTLN_SHIFT]
            self.ref[:-DTLN_SHIFT] = self.ref[DTLN_SHIFT:]
            self.ref[-DTLN_SHIFT:] = ref[k:k + DTLN_SHIFT]
            block, self.s1, self.s2 = self.models.step(self.mic, self.ref, self.s1, self.s2)
            self.ola[:-DTLN_SHIFT] = self.ola[DTLN_SHIFT:]
            self.ola[-DTLN_SHIFT:] = 0
            self.ola += block
            out[k:k + DTLN_SHIFT] = self.ola[:DTLN_SHIFT]
        return out


class LocalVqeLibrary:
    _lib = None
    _load_lock = threading.Lock()

    @classmethod
    def load(cls, path=None):
        with cls._load_lock:
            if cls._lib is None:
                path = Path(path or NATIVE / "localvqe.dll")
                if os.name == "nt":
                    os.add_dll_directory(str(path.parent))
                lib = ctypes.CDLL(str(path))
                handle = ctypes.c_size_t
                fp = ctypes.POINTER(ctypes.c_float)
                lib.localvqe_options_new.restype = handle
                lib.localvqe_options_set_model_path.argtypes = [handle, ctypes.c_char_p]
                lib.localvqe_options_set_threads.argtypes = [handle, ctypes.c_int]
                lib.localvqe_options_free.argtypes = [handle]
                lib.localvqe_new_with_options.argtypes = [handle]
                lib.localvqe_new_with_options.restype = handle
                lib.localvqe_free.argtypes = [handle]
                lib.localvqe_hop_length.argtypes = [handle]
                lib.localvqe_process_frame_f32.argtypes = [handle, fp, fp, ctypes.c_int, fp]
                lib.localvqe_last_error.argtypes = [handle]
                lib.localvqe_last_error.restype = ctypes.c_char_p
                lib.localvqe_reset.argtypes = [handle]
                cls._lib = lib
            return cls._lib


class LocalVqe:
    def __init__(self, model_path, threads=1, library=None):
        self.lib = library or LocalVqeLibrary.load()
        opts = self.lib.localvqe_options_new()
        self.lib.localvqe_options_set_model_path(opts, str(model_path).encode())
        self.lib.localvqe_options_set_threads(opts, threads)
        self.ctx = self.lib.localvqe_new_with_options(opts)
        self.lib.localvqe_options_free(opts)
        if not self.ctx:
            raise RuntimeError(f"LocalVQE could not load {model_path}")
        if self.lib.localvqe_hop_length(self.ctx) != HOP:
            raise RuntimeError("Unexpected LocalVQE hop length")
        self._out = np.zeros(HOP, np.float32)

    def process(self, mic, ref):
        """`len(mic) == len(ref)`, a multiple of 256."""
        if len(mic) % HOP or len(ref) != len(mic):
            raise ValueError("LocalVQE needs equal mic/reference lengths in whole 256-sample hops")
        fp = ctypes.POINTER(ctypes.c_float)
        mic = np.ascontiguousarray(mic, np.float32)
        ref = np.ascontiguousarray(ref, np.float32)
        out = np.empty(len(mic), np.float32)
        for i in range(0, len(mic), HOP):
            rc = self.lib.localvqe_process_frame_f32(self.ctx, mic[i:].ctypes.data_as(fp), ref[i:].ctypes.data_as(fp),
                                                     HOP, self._out.ctypes.data_as(fp))
            if rc != 0:
                raise RuntimeError(f"LocalVQE: {self.lib.localvqe_last_error(self.ctx)!r}")
            out[i:i + HOP] = self._out
        return out

    def close(self):
        if self.ctx:
            self.lib.localvqe_free(self.ctx)
            self.ctx = 0


class DtlnLocalVqe:
    """The chosen chain for one phone: DTLN 512, then LocalVQE fed the same raw reference.

    The reference is not aligned to the mic and not scaled. Output lags the mic input by
    CHAIN_LATENCY samples (measured: DTLN's 384 plus LocalVQE's 256)."""

    def __init__(self, dtln_models, localvqe_model, latency=None):
        self.dtln = Dtln(dtln_models)
        self.vqe = LocalVqe(localvqe_model)
        self.latency = CHAIN_LATENCY if latency is None else latency
        # DTLN's output lags by 384 samples; delay LocalVQE's reference by as much so the second stage
        # sees the same mic/reference relation as the first. (The offline experiment fed it
        # undelayed; a replay of its recordings measured no consistent difference.)
        self._ref_delay = np.zeros(DTLN_LATENCY, np.float32)

    def process(self, mic, ref):
        delayed = np.concatenate([self._ref_delay, ref])
        self._ref_delay, delayed = delayed[len(ref):], delayed[:len(ref)]
        if len(mic) < 2 * PIPELINE_BLOCK:
            return self.vqe.process(self.dtln.process(mic, ref), delayed)
        # A backlog (the pre-roll, or catching up): DTLN on block j+1 runs in a second thread while
        # LocalVQE does block j. Both release the GIL inside TFLite / the DLL, so this nearly halves
        # the wall time; the output is identical to the sequential order.
        blocks = range(0, len(mic), PIPELINE_BLOCK)
        out = np.empty(len(mic), np.float32)
        ahead = _PIPELINE.submit(self.dtln.process, mic[:PIPELINE_BLOCK], ref[:PIPELINE_BLOCK])
        for i in blocks:
            stage1 = ahead.result()
            j = i + PIPELINE_BLOCK
            if j < len(mic):
                ahead = _PIPELINE.submit(self.dtln.process, mic[j:j + PIPELINE_BLOCK], ref[j:j + PIPELINE_BLOCK])
            out[i:i + len(stage1)] = self.vqe.process(stage1, delayed[i:i + len(stage1)])
        return out

    def close(self):
        self.vqe.close()


class LocalVqeOnly:
    """LocalVQE alone: cleans echo and steady noise; about 40% of the chain's CPU. Output lags the
    mic by one hop."""

    def __init__(self, localvqe_model):
        self.vqe = LocalVqe(localvqe_model)
        self.latency = HOP

    def process(self, mic, ref):
        return self.vqe.process(mic, ref)

    def close(self):
        self.vqe.close()


WARMUP = 62 * HOP  # ~1 s of recent audio warms the chain before it takes over
REF_THRESHOLD = 10 ** (-65 / 20)
REF_HANGOVER = 75  # hops (~1.2 s) of silent reference before the chain is let go
MIN_PLAY_HOPS = 31  # ~0.5 s of playback before the chain: the assistant's own short sounds stay on LocalVQE


class AdaptiveEngine:
    """A turn's engine: LocalVQE; the chain (DTLN -> LocalVQE) while `want_chain` is set and the PC
    plays something.

    Each runs on the raw mic and reference (never one after the other); LocalVQE's output is
    delayed to the chain's latency so the two line up sample for sample. Only one runs at a time:
    the chain has a LocalVQE of its own, so the standalone one pauses while the chain works. A
    switch either way first runs the incoming engine over the last ~1 s of raw audio (warm-up,
    output discarded), then one hop crossfades to it."""

    def __init__(self, vqe, chain_factory):
        self.vqe = vqe
        self._chain_factory = chain_factory
        self.chain = None
        self.latency = CHAIN_LATENCY
        self._align = np.zeros(self.latency - vqe.latency, np.float32)
        self._history_mic = np.zeros(0, np.float32)
        self._history_ref = np.zeros(0, np.float32)
        self._fade = np.linspace(0, 1, HOP, endpoint=False, dtype=np.float32)
        self._silent_hops = REF_HANGOVER
        self._playing_run = 0  # hops of playback since the reference last went quiet
        self.want_chain = False
        self.on_chain = False
        self.switches = 0
        self.hops = self.playing_hops = 0  # for the logs: how much of a turn the PC played

    def new_turn(self):
        """A new turn begins: the last turn's audio (maybe minutes old) warms nothing."""
        self._history_mic = np.zeros(0, np.float32)
        self._history_ref = np.zeros(0, np.float32)

    def _localvqe(self, mic, ref):
        out_v = self.vqe.process(mic, ref)
        delayed = np.concatenate([self._align, out_v])
        self._align, out_v = delayed[len(out_v):], delayed[:len(out_v)]
        return out_v

    def process(self, mic, ref):
        for k in range(0, len(ref), HOP):
            peak = float(np.max(np.abs(ref[k:k + HOP]))) if len(ref[k:k + HOP]) else 0.0
            self._silent_hops = 0 if peak > REF_THRESHOLD else self._silent_hops + 1
            if peak > REF_THRESHOLD:
                self._playing_run += 1
            elif self._silent_hops >= REF_HANGOVER:
                self._playing_run = 0
            self.hops += 1
            self.playing_hops += peak > REF_THRESHOLD
        wanted = (self.want_chain and self._silent_hops < REF_HANGOVER
                  and (self.on_chain or self._playing_run >= MIN_PLAY_HOPS))
        if wanted and not self.on_chain:
            out_v = self._localvqe(mic, ref)
            if self.chain is None:
                self.chain = self._chain_factory()
            if len(self._history_mic):
                self.chain.process(self._history_mic, self._history_ref)  # warm-up
            out_c = self.chain.process(mic, ref)
            out = out_c.copy()
            out[:HOP] = out_v[:HOP] * (1 - self._fade) + out_c[:HOP] * self._fade
            self.on_chain = True
            self.switches += 1
        elif self.on_chain:
            out_c = self.chain.process(mic, ref)
            if wanted:
                out = out_c
            else:
                if len(self._history_mic):  # warm-up; its tail refills the delay line
                    self._localvqe(self._history_mic, self._history_ref)
                out_v = self._localvqe(mic, ref)
                out = out_v.copy()
                out[:HOP] = out_c[:HOP] * (1 - self._fade) + out_v[:HOP] * self._fade
                self.on_chain = False
                self.switches += 1
        else:
            out = self._localvqe(mic, ref)
        self._history_mic = np.concatenate([self._history_mic, mic])[-WARMUP:]
        self._history_ref = np.concatenate([self._history_ref, ref])[-WARMUP:]
        return out

    def close(self):
        self.vqe.close()
        if self.chain is not None:
            self.chain.close()
