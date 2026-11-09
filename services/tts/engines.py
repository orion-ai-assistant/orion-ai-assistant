import os
from services.shared.environment import get_env
import sys
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    TORCH_AVAILABLE = False
import gc
import random
import io
import numpy as np
import logging
from pathlib import Path
from typing import Optional, Tuple, Any
import subprocess
import tempfile
import wave
import time
import threading

# Alt klasörlerdeki kütüphaneleri yola ekliyoruz ki import edilebilsinler
current_dir = os.path.dirname(__file__)
sys.path.insert(0, current_dir)
sys.path.insert(0, os.path.join(current_dir, "omnivoice"))
sys.path.append(os.path.join(current_dir, "voxcpm2"))

logger = logging.getLogger(__name__)

def _env_to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

_SERVER_START_LOCK = threading.Lock()


class TTSEngine:
    def __init__(self):
        self.device = "cuda" if TORCH_AVAILABLE and torch.cuda.is_available() else "cpu"
        self.low_vram = _env_to_bool(get_env("LOW_VRAM", "false"), default=False)

    def set_seed(self, seed: int):
        """Merkezi rastgelelik (seed) ayarı."""
        if seed != -1:
            s = int(seed) % (2**32)
            import random
            random.seed(s)
            np.random.seed(s)
            if TORCH_AVAILABLE:
                torch.manual_seed(s)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(s)

    def server_alive(self) -> bool:
        """Whether the engine's own server (OmniVoice's tts-server, VoxCPM2's llama-tts-server) still runs; an engine
        without one is always alive."""
        if not hasattr(self, "_start_server"):
            return True
        proc = getattr(self, "server_proc", None)
        return proc is not None and proc.poll() is None

    def ensure_server(self):
        """Starts the engine's server again when it has died (it can, e.g. when the PC runs out of memory: ggml's
        GGML_ASSERT(ctx->mem_buffer != NULL)); before, the service stayed "healthy" with no model behind it."""
        with _SERVER_START_LOCK:  # One start at a time (a request and the health check may both ask)
            if not self.server_alive():
                proc = getattr(self, "server_proc", None)
                logger.warning(f"The engine's server is not running (exit code {proc.poll() if proc else None}); starting it again")
                self._start_server()

    def encode_voice(self, audio_path: str, text: str) -> Any:
        raise NotImplementedError

    def generate(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        raise NotImplementedError

    def generate_stream(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
        raise NotImplementedError

    def cleanup(self):
        gc.collect()
        if TORCH_AVAILABLE and torch.cuda.is_available():
            torch.cuda.empty_cache()

class VoxCPMEngine(TTSEngine):
    def __init__(self, model_id="openbmb/VoxCPM2"):
        super().__init__()
        import voxcpm
        vae_device = "cpu" if self.low_vram else None
        logger.info(f"Loading VoxCPM2 (VAE Device: {vae_device})")
        
        self.model = voxcpm.VoxCPM.from_pretrained(
            model_id,
            load_denoiser=False, # ZipEnhancer (modelscope) devredışı
            vae_device=vae_device,
            local_files_only=True
        )

    def encode_voice(self, audio_path: str, text: str) -> Any:
        # VoxCPM2 cloning uses the audio reference only; never condition on a reference transcript.
        return self.model.tts_model.build_prompt_cache(
            prompt_text=None,
            prompt_wav_path=None,
            reference_wav_path=audio_path
        )

    @staticmethod
    def _audio_only_voice_cache(voice_cache: Any) -> Any:
        """Drop transcript continuation features from older VoxCPM2 clone caches."""
        if isinstance(voice_cache, dict) and "ref_audio_feat" in voice_cache:
            return {"ref_audio_feat": voice_cache["ref_audio_feat"], "mode": "reference"}
        return voice_cache

    def generate(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        self.set_seed(seed)
        voice_cache = self._audio_only_voice_cache(voice_cache)
        final_text = f"({instruct}){text}" if instruct else text
        inference_timesteps = steps if steps else (10 if self.low_vram else 15)
        
        with torch.inference_mode():
            gen = self.model.tts_model.generate_with_prompt_cache_streaming(
                target_text=final_text,
                prompt_cache=voice_cache,
                cfg_value=guidance_scale,
                inference_timesteps=inference_timesteps
            )
            
            wav_chunks = []
            for result in gen:
                if abort_event and abort_event.is_set():
                    logger.info("VoxCPMEngine: Generation aborted by event.")
                    return (self.model.tts_model.sample_rate, np.zeros(0, dtype=np.int16))
                
                wav_chunk, _, _ = result
                wav_chunks.append(wav_chunk)
            
            if not wav_chunks:
                return (self.model.tts_model.sample_rate, np.zeros(0, dtype=np.int16))
            
            wav = torch.cat(wav_chunks, dim=-1)
        
        audio_data = wav.detach().cpu().numpy().flatten()
        
        # 16-bit PCM dönüşümü
        max_val = np.max(np.abs(audio_data))
        if max_val > 0:
            audio_data = audio_data / max_val
        audio_data = (audio_data * 32767).astype(np.int16)
        
        return (self.model.tts_model.sample_rate, audio_data)

    def generate_stream(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
        self.set_seed(seed)
        voice_cache = self._audio_only_voice_cache(voice_cache)
        final_text = f"({instruct}){text}" if instruct else text
        inference_timesteps = steps if steps else (10 if self.low_vram else 15)
        
        yield self.model.tts_model.sample_rate

        with torch.inference_mode():
            buffer = []
            is_aborted = False # Buffer kontrolü için eklendi
            
            for result in self.model.tts_model.generate_with_prompt_cache_streaming(
                target_text=final_text,
                prompt_cache=voice_cache,
                cfg_value=guidance_scale,
                inference_timesteps=inference_timesteps
            ):
                if abort_event and abort_event.is_set():
                    logger.info("VoxCPMEngine: Stream generation aborted by event.")
                    is_aborted = True
                    break
                
                wav, _, _ = result
                audio_data = wav.detach().cpu().numpy().flatten()
                audio_data = np.clip(audio_data, -1.0, 1.0)
                audio_data = (audio_data * 32767).astype(np.int16)
                
                buffer.append(audio_data.tobytes())
                if len(buffer) >= 2:
                    yield b"".join(buffer)
                    buffer = []
            
            # Sadece bağlantı kopmamışsa kalan buffer'ı gönder
            if buffer and not is_aborted:
                yield b"".join(buffer)

class OmniVoiceEngine(TTSEngine):
    def __init__(self, model_id: str):
        super().__init__()
        from omnivoice.models.omnivoice import OmniVoice
        logger.info(f"Loading OmniVoice (Device: {self.device}, Low VRAM: {self.low_vram})")
        
        self.model = OmniVoice.from_pretrained(
            model_id,
            device_map=self.device,
            dtype=torch.float16 if self.device == "cuda" else torch.float32,
            local_files_only=True
        )
        
        
        if hasattr(self.model, "audio_tokenizer") and self.model.audio_tokenizer is not None:
            tokenizer_device = "cpu" if self.low_vram else self.device
            logger.info(f"OmniVoice: Audio Tokenizer device set to {tokenizer_device}")
            self.model.audio_tokenizer.to(tokenizer_device)

    def encode_voice(self, audio_path: str, text: str) -> Any:
        ref_text = text.strip() if (text and text.strip()) else None
        return self.model.create_voice_clone_prompt(
            ref_audio=audio_path,
            ref_text=ref_text
        )

    def generate(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        from omnivoice.models.omnivoice import OmniVoiceGenerationConfig
        self.set_seed(seed)

        if not text or not text.strip():
            return 24000, np.array([], dtype=np.int16)

        lang = language if (language and language not in ("Auto", "", "tr")) else None
        inst = instruct.strip() if instruct and instruct.strip() else None
        
        gen_config = OmniVoiceGenerationConfig(
            num_step=int(steps or 32),
            guidance_scale=float(guidance_scale),
            denoise=True,
            preprocess_prompt=True,
            postprocess_output=True
        )
        
        kw = {
            "text": text.strip(),
            "language": lang,
            "voice_clone_prompt": voice_cache,
            "instruct": inst,
            "generation_config": gen_config
        }
        
        if speed and float(speed) != 1.0:
            kw["speed"] = float(speed)
        
        with torch.inference_mode():
            audio_list = self.model.generate(**kw)

        if not audio_list:
            return 24000, np.array([], dtype=np.int16)

        audio_data = (audio_list[0].flatten() * 32767).astype(np.int16)
        return (24000, audio_data)

    def generate_stream(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
        sr, audio_data = self.generate(
            text=text, voice_cache=voice_cache, instruct=instruct, 
            speed=speed, guidance_scale=guidance_scale, steps=steps, 
            seed=seed, language=language
        )
        yield sr
        yield audio_data.tobytes()

class OmniVoiceGGUFEngine(TTSEngine):
    def __init__(
        self,
        bin_path: Optional[str] = None,
        model_path: Optional[str] = None,
        codec_path: Optional[str] = None
    ):
        super().__init__()
        
        is_windows = os.name == 'nt'
        exe_ext = ".exe" if is_windows else ""
        
        # Determine the base directory of the current file (services/tts)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        gguf_dir = os.path.join(current_dir, "omnivoice-gguf")
        
        # Check standard Linux Docker installation path first
        if os.path.exists(f"/usr/local/bin/tts-server{exe_ext}"):
            self.server_bin = f"/usr/local/bin/tts-server{exe_ext}"
            self.codec_bin = f"/usr/local/bin/omnivoice-codec{exe_ext}"
        else:
            # Orion's Bileşenler page installs the engine outside the code folder (OMNIVOICE_BIN_DIR).
            bin_dir = os.environ.get("OMNIVOICE_BIN_DIR") or os.path.join(gguf_dir, "bin")
            self.server_bin = os.path.join(bin_dir, f"tts-server{exe_ext}")
            self.codec_bin = os.path.join(bin_dir, f"omnivoice-codec{exe_ext}")

        # Models Path
        models_base = os.path.join(current_dir, "models", "omnivoice-gguf")

        if model_path and os.path.isdir(model_path):
            # A model folder holds both files (Bileşenler downloads them together).
            if not codec_path and os.path.exists(os.path.join(model_path, "omnivoice-tokenizer-Q8_0.gguf")):
                codec_path = os.path.join(model_path, "omnivoice-tokenizer-Q8_0.gguf")
            model_path = os.path.join(model_path, "omnivoice-base-Q8_0.gguf")
            
        if not model_path or not os.path.exists(model_path):
            model_path = os.path.join(models_base, "omnivoice-base-Q8_0.gguf")
            
        if not codec_path or not os.path.exists(codec_path):
            codec_path = os.path.join(models_base, "omnivoice-tokenizer-Q8_0.gguf")
            
        self.model_path = model_path
        self.codec_path = codec_path

        if not os.path.exists(self.server_bin):
            logger.warning(f"OmniVoice GGUF Server executable not found at: {self.server_bin}")
        if not os.path.exists(self.model_path):
            logger.warning(f"OmniVoice GGUF model not found at: {self.model_path}")
        if not os.path.exists(self.codec_path):
            logger.warning(f"OmniVoice GGUF codec not found at: {self.codec_path}")

        self.host = "127.0.0.1"
        # Its own loopback port; a second TTS (a Bileşenler one next to a source one) picks another.
        self.port = int(os.environ.get("OMNIVOICE_SERVER_PORT", "8809"))
        self.server_proc = None
        self._start_server()
        
        import atexit
        atexit.register(self._shutdown_server)
        
        logger.info(f"Loaded OmniVoiceGGUFEngine (server: {self.server_bin}, model: {self.model_path})")

    def _start_server(self):
        cmd = [
            self.server_bin,
            "--model", self.model_path,
            "--codec", self.codec_path,
            "--host", self.host,
            "--port", str(self.port)
        ]
        logger.info(f"Starting tts-server: {' '.join(cmd)}")
        self.server_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'
        )
        def _log_server_output():
            if not self.server_proc or not self.server_proc.stdout:
                return
            for line in iter(self.server_proc.stdout.readline, ''):
                line_str = line.strip()
                if line_str:
                    logger.info(f"[tts-server] {line_str}")
        threading.Thread(target=_log_server_output, daemon=True).start()

        
        import urllib.request
        import urllib.error
        
        # Wait for health check
        for _ in range(40):
            try:
                req = urllib.request.Request(f"http://{self.host}:{self.port}/health")
                with urllib.request.urlopen(req, timeout=1) as response:
                    if response.status == 200:
                        logger.info("tts-server is up and healthy.")
                        return
            except Exception:
                time.sleep(0.5)
        logger.error("tts-server failed to start or did not become healthy in time.")

    def _shutdown_server(self):
        if self.server_proc:
            logger.info("Terminating tts-server process...")
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None

    def encode_voice(self, audio_path: str, text: str) -> Any:
        ref_text = text.strip() if (text and text.strip()) else ""
        codec_bin = getattr(self, "codec_bin", os.path.join(os.path.dirname(self.server_bin), "omnivoice-codec" + (".exe" if os.name == 'nt' else "")))
        if not os.path.exists(codec_bin):
            logger.error(f"Codec executable not found: {codec_bin}")
            raise ValueError("Codec dosyası bulunamadı. Lütfen kurulumu kontrol edin.")
                
        import tempfile, shutil
        rvq_bytes = None
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_wav = os.path.join(tmp_dir, "ref.wav")
            with open(audio_path, "rb") as f:
                sample = f.read()
            with open(tmp_wav, "wb") as f:
                f.write(tidy_reference(sample))  # Trimmed and levelled (a sample that is not a WAV goes as it came)
            cmd = [codec_bin, "--model", self.codec_path, "-i", tmp_wav]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                tmp_rvq = os.path.join(tmp_dir, "ref.rvq")
                if os.path.exists(tmp_rvq):
                    with open(tmp_rvq, "rb") as f:
                        rvq_bytes = f.read()
            except Exception as e:
                logger.error(f"Failed to encode voice with codec: {e}")
        if not rvq_bytes:
            raise ValueError("Ses dosyası bozuk veya uyumsuz formatta. Lütfen geçerli bir ses dosyası (örneğin temiz bir WAV) yükleyin.")
        seconds = rvq_seconds(rvq_bytes)
        check_clone_seconds(seconds)
        return {"rvq_bytes": rvq_bytes, "ref_text": ref_text, "ref_seconds": seconds}

    def _register_voice_to_server(self, voice_cache: Any, target_name: str = None) -> str:
        if not voice_cache:
            return ""
        
        import uuid, base64, json, urllib.request
        voice_name = target_name if target_name else f"temp_voice_{uuid.uuid4().hex}"
        ref_text = ""
        audio_bytes = None
        rvq_bytes = None
        
        if isinstance(voice_cache, dict):
            ref_text = voice_cache.get("ref_text", "")
            rvq_bytes = voice_cache.get("rvq_bytes")
            if not rvq_bytes:
                if "audio_bytes" in voice_cache:
                    audio_bytes = voice_cache["audio_bytes"]
                elif "ref_audio" in voice_cache and os.path.exists(voice_cache["ref_audio"]):
                    with open(voice_cache["ref_audio"], "rb") as f:
                        audio_bytes = f.read()
        elif isinstance(voice_cache, str) and os.path.exists(voice_cache):
            with open(voice_cache, "rb") as f:
                audio_bytes = f.read()
        
        if not rvq_bytes and not audio_bytes:
            return ""
            
        payload = {
            "name": voice_name,
            "ref_text": ref_text or "dummy"
        }
        
        if rvq_bytes:
            payload["rvq_b64"] = base64.b64encode(rvq_bytes).decode('utf-8')
        else:
            payload["wav_b64"] = base64.b64encode(audio_bytes).decode('utf-8')
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(f"http://{self.host}:{self.port}/v1/audio/voices", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                if res.status == 200:
                    return voice_name
        except Exception as e:
            logger.error(f"Voice registration failed: {e}")
        return ""

    def _delete_voice_from_server(self, voice_name: str):
        if not voice_name:
            return
        import urllib.request
        req = urllib.request.Request(f"http://{self.host}:{self.port}/v1/audio/voices/{voice_name}", method="DELETE")
        try:
            with urllib.request.urlopen(req) as res:
                pass
        except Exception as e:
            logger.error(f"Failed to delete temp voice {voice_name}: {e}")

    def generate(
        self,
        text: str,
        voice_cache: Any = None,
        voice_name: str = "",
        instruct: str = "",
        speed: float = 1.0,
        guidance_scale: float = 2.0,
        steps: Optional[int] = None,
        seed: int = -1,
        language: str = "tr",
        abort_event: Optional[Any] = None
    ) -> Tuple[int, np.ndarray]:
        if not text or not text.strip():
            return 24000, np.array([], dtype=np.int16)

        import json, urllib.request
        
        target_voice = None
        if voice_cache and voice_name:
            if not hasattr(self, "registered_voices"):
                self.registered_voices = set()
            target_voice = voice_name
            if target_voice not in self.registered_voices:
                reg_name = self._register_voice_to_server(voice_cache, target_name=target_voice)
                if not reg_name:
                    raise RuntimeError(f"Ses klonu '{target_voice}' tts-server sunucusuna kaydedilemedi (uyumsuz veya bozuk ses dosyası).")
                self.registered_voices.add(target_voice)
        elif voice_cache:
            target_voice = self._register_voice_to_server(voice_cache)
            if not target_voice:
                raise RuntimeError("Ses klonu tts-server sunucusuna kaydedilemedi (uyumsuz veya bozuk ses dosyası).")
        
        try:
            payload = {
                "input": text.strip(),
                "response_format": "wav"
            }
            if target_voice:
                payload["voice"] = target_voice
            generic_models = {"local-model", "local-tts", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "voxcpm2-gguf", "default", "none"}
            if instruct and instruct.strip() and instruct.strip().lower() not in generic_models:
                payload["instructions"] = instruct.strip()
            
            lang = language if (language and language not in ("Auto", "", "tr")) else None
            if lang:
                payload["language"] = lang
                
            if seed and seed != -1:
                payload["seed"] = int(seed)
                
            if steps:
                payload["steps"] = int(steps)
                
            if guidance_scale:
                payload["guidance_scale"] = float(guidance_scale)
                
            if speed and float(speed) != 1.0:
                payload["speed"] = float(speed)
                
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(f"http://{self.host}:{self.port}/v1/audio/speech", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            
            try:
                with urllib.request.urlopen(req, timeout=600) as res:
                    if res.status == 200:
                        return streamed_wav(res.read())
            except Exception as e:
                logger.error(f"GGUF Server request failed: {e}")
            
            return 500, np.array([], dtype=np.int16)
        finally:
            # Sadece isimsiz geçici sesler için bellek temizliği yap (önbelleklenmeyenler)
            if target_voice and not voice_name:
                self._delete_voice_from_server(target_voice)

    def generate_stream(
        self,
        text: str,
        voice_cache: Any = None,
        voice_name: str = "",
        instruct: str = "",
        speed: float = 1.0,
        guidance_scale: float = 2.0,
        steps: Optional[int] = None,
        seed: int = -1,
        language: str = "tr",
        abort_event: Optional[Any] = None
    ):
        if not text or not text.strip():
            yield 24000
            return

        import json, urllib.request
        
        target_voice = None
        is_temp_voice = False
        if voice_cache and voice_name:
            if not hasattr(self, "registered_voices"):
                self.registered_voices = set()
            target_voice = voice_name
            if target_voice not in self.registered_voices:
                reg_name = self._register_voice_to_server(voice_cache, target_name=target_voice)
                if not reg_name:
                    raise RuntimeError(f"Ses klonu '{target_voice}' tts-server sunucusuna kaydedilemedi (uyumsuz veya bozuk ses dosyası).")
                self.registered_voices.add(target_voice)
        elif voice_cache:
            target_voice = self._register_voice_to_server(voice_cache)
            if not target_voice:
                raise RuntimeError("Ses klonu tts-server sunucusuna kaydedilemedi (uyumsuz veya bozuk ses dosyası).")
            is_temp_voice = True
        
        payload = {
            "input": text.strip(),
            "response_format": "pcm"
        }
        if target_voice:
            payload["voice"] = target_voice
        generic_models = {"local-model", "local-tts", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "voxcpm2-gguf", "default", "none"}
        if instruct and instruct.strip() and instruct.strip().lower() not in generic_models:
            payload["instructions"] = instruct.strip()
        
        lang = language if (language and language not in ("Auto", "", "tr")) else None
        if lang:
            payload["language"] = lang
            
        if seed and seed != -1:
            payload["seed"] = int(seed)
            
        if steps:
            payload["steps"] = int(steps)
            
        if guidance_scale:
            payload["guidance_scale"] = float(guidance_scale)
            
        if speed and float(speed) != 1.0:
            payload["speed"] = float(speed)
            
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(f"http://{self.host}:{self.port}/v1/audio/speech", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        
        yield 24000 # Send sample rate
        
        try:
            with urllib.request.urlopen(req, timeout=600) as res:
                if res.status == 200:
                    while True:
                        if abort_event and abort_event.is_set():
                            break
                        chunk = res.read(4096)
                        if not chunk:
                            break
                        yield chunk
        except Exception as e:
            logger.error(f"GGUF Server stream request failed: {e}")
        finally:
            if is_temp_voice and target_voice:
                self._delete_voice_from_server(target_voice)

class VoxCPM2GGUFEngine(TTSEngine):
    """VoxCPM2 without PyTorch: llama.cpp-omni's llama-tts-server (ggml, CUDA/Vulkan/CPU) with its two GGUFs, the
    BaseLM and the Acoustic model, as a child process on loopback. 48 kHz, 30 languages (Turkish among them).
    A cloned voice is its WAV sample and the sample's transcript, sent with every request (the server keeps no
    voices): the sample is processed again each time, so the same 5 s / 20 s rule as OmniVoice's applies."""

    SAMPLE_RATE = 24000  # Orion's whole speech path is 24 kHz mono 16-bit; the server's 48 kHz is halved here.

    def __init__(self, model_path: Optional[str] = None, acoustic_path: Optional[str] = None):
        super().__init__()
        exe = "llama-tts-server" + (".exe" if os.name == "nt" else "")
        bin_dir = os.environ.get("VOXCPM2_BIN_DIR") or ""
        self.server_bin = os.path.join(bin_dir, exe)
        self.model_path, self.acoustic_path = model_path, acoustic_path
        for path, what in ((self.server_bin, "llama-tts-server"), (model_path, "VoxCPM2 BaseLM"),
                           (acoustic_path, "VoxCPM2 Acoustic")):
            if not path or not os.path.exists(path):
                logger.warning(f"{what} not found at: {path}")
        self.host = "127.0.0.1"
        self.port = int(os.environ.get("VOXCPM2_SERVER_PORT", "8807"))
        self.server_proc = None
        self._prime_lock = threading.Lock()
        self._plain_prime = None  # The default voice's warm-up (PRIME_TEXT spoken), read or made once.
        self._start_server()
        try:
            self._plain_prime_wav()
        except Exception as e:
            logger.warning(f"VoxCPM2 warm-up sample not ready yet (made at the first request): {e}")
        import atexit
        atexit.register(self._shutdown_server)

    def _start_server(self):
        cmd = [self.server_bin, "--voxcpm2-base-lm", self.model_path, "--voxcpm2-acoustic", self.acoustic_path,
               "--host", self.host, "--port", str(self.port)]
        layers = os.environ.get("VOXCPM2_GPU_LAYERS", "").strip()
        if layers.isdigit():  # The memory mode (Bileşenler): only this many of the BaseLM's layers on the card.
            cmd += ["--voxcpm2-n-gpu-layers", layers]
        logger.info(f"Starting llama-tts-server: {' '.join(cmd)}")
        self.server_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                            encoding="utf-8", errors="replace", cwd=os.path.dirname(self.server_bin) or None)

        def _log_server_output():
            for line in iter(self.server_proc.stdout.readline, ""):
                if line.strip():
                    logger.info(f"[llama-tts-server] {line.strip()}")
        threading.Thread(target=_log_server_output, daemon=True).start()
        import urllib.request
        # Loading the two GGUFs (~3.5 GB) from a cold disk takes a while.
        for _ in range(360):
            if self.server_proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://{self.host}:{self.port}/health", timeout=1) as response:
                    if response.status == 200:
                        logger.info("llama-tts-server is up and healthy.")
                        # One short sentence in the background, so the first real one does not pay for the server's
                        # first run (about 0.5 s more) or for encoding the warm-up voice (kept by the server after).
                        threading.Thread(target=self._warm_up, daemon=True).start()
                        return
            except Exception:
                time.sleep(0.5)
        logger.error("llama-tts-server failed to start or did not become healthy in time.")

    def _warm_up(self):
        started = time.time()
        # With the defaults a request gets (guidance 3, 10 steps): the server builds its decode graph per guidance and
        # steps, so a warm-up with other values would leave the first real sentence to build it.
        rate, audio = self.generate("Merhaba.", guidance_scale=None)
        logger.info(f"VoxCPM2 warmed up in {time.time() - started:.2f} s ({len(audio)} samples)")

    def _shutdown_server(self):
        if self.server_proc:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None

    def encode_voice(self, audio_path: str, text: str) -> Any:
        with open(audio_path, "rb") as f:
            data = tidy_reference(f.read())
        try:
            with wave.open(io.BytesIO(data)) as w:
                seconds = round(w.getnframes() / float(w.getframerate()), 2)
        except Exception:
            raise ValueError("VoxCPM2 ses örneğini WAV olarak ister; lütfen temiz bir WAV dosyası yükleyin.") from None
        check_clone_seconds(seconds)
        # Soften excessive sibilance in clone recordings once, when the clone is saved. Do not filter generated output.
        ref_wav = soften_reference(data)
        # Ignore the optional transcript. The saved sample itself is the reference for every clone request;
        # do not synthesize and cache an extra, model-generated clone warm-up.
        return {"ref_wav": ref_wav, "ref_seconds": seconds}

    def _make_prime(self, *, text: Optional[str] = None, design: Optional[str] = None,
                    seed: Optional[int] = None, guidance: Optional[float] = None,
                    steps: Optional[int] = None, temperature: Optional[float] = None,
                    leading_trim_ms: float = 50.0) -> bytes:
        """Generate the default warm-up through the same PCM post-processing path as full speech output."""
        rate, audio = self.generate(
            text=PRIME_TEXT if text is None else text,
            instruct=design or DEFAULT_VOICE_DESIGN,
            guidance_scale=PRIME_GUIDANCE if guidance is None else guidance,
            steps=PRIME_STEPS if steps is None else steps,
            seed=PRIME_SEED if seed is None else seed,
            temperature=PRIME_TEMPERATURE if temperature is None else temperature,
        )
        if rate == 500 or len(audio) < self.SAMPLE_RATE // 2:
            raise RuntimeError("warm-up came back (nearly) empty")
        trim_samples = int(round(rate * max(0.0, leading_trim_ms) / 1000.0))
        if trim_samples:
            audio = audio[trim_samples:]
        audio = soften_pcm16(audio, rate)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio.tobytes())
        return out.getvalue()

    def save_plain_prime(self, *, text: Optional[str] = None, design: str = "",
                         seed: int = -1, guidance: Optional[float] = None,
                         steps: Optional[int] = None, temperature: Optional[float] = None,
                         leading_trim_ms: float = 50.0) -> bytes:
        """Generate and persist the default warm-up with the current VoxCPM2 design/settings."""
        data = self._make_prime(text=text, design=design, seed=seed,
                                guidance=DEFAULT_GUIDANCE if guidance is None else guidance,
                                steps=DEFAULT_STEPS if steps is None else steps,
                                temperature=DEFAULT_TEMPERATURE if temperature is None else temperature,
                                leading_trim_ms=leading_trim_ms)
        path = voice_data_dir() / "_prime_voxcpm2-gguf_custom.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        with self._prime_lock:
            self._plain_prime = data
        return data

    def _plain_prime_wav(self) -> bytes:
        """The default voice's warm-up, kept in voices/ and made when it is not there (or the design changed)."""
        cache_dir = voice_data_dir()
        custom_path = cache_dir / "_prime_voxcpm2-gguf_custom.wav"
        if custom_path.is_file():
            self._plain_prime = custom_path.read_bytes()
            return self._plain_prime
        if self._plain_prime:
            return self._plain_prime
        with self._prime_lock:
            if self._plain_prime:
                return self._plain_prime
            import hashlib
            tag = hashlib.sha1(f"{DEFAULT_VOICE_DESIGN}|{PRIME_TEXT}|{PRIME_SEED}".encode()).hexdigest()[:8]
            path = cache_dir / f"_prime_voxcpm2-gguf_{tag}.wav"
            if path.exists():
                self._plain_prime = path.read_bytes()
            else:
                data = self._make_prime()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                logger.info(f"VoxCPM2 warm-up made: {path}")
                self._plain_prime = data
            return self._plain_prime

    def _payload(self, text, voice_cache, instruct, guidance_scale, seed, steps=None, temperature=None):
        import base64
        text = text.strip()
        generic = {"local-model", "local-tts", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm",
                   "voxcpm2", "voxcpm2-gguf", "default", "none"}
        cloned = isinstance(voice_cache, dict) and bool(voice_cache.get("ref_wav"))
        designed = bool(instruct and instruct.strip() and instruct.strip().lower() not in generic)
        ref = None
        if designed:
            text = f"({instruct.strip()}){text}"  # VoxCPM2's voice design: a description in parentheses first.
        if cloned:
            # Always use the saved clone reference, including for old caches that still contain prime_wav.
            ref = voice_cache["ref_wav"]
        elif not designed:
            try:  # Nothing asked for: the default voice, from its warm-up audio alone.
                ref = self._plain_prime_wav()
            except Exception as e:
                logger.warning(f"VoxCPM2 warm-up unavailable, plain voice design instead: {e}")
                text = f"({DEFAULT_VOICE_DESIGN}){text}"
        payload = {"model": "voxcpm", "input": text, "response_format": "wav"}
        if ref:
            payload["reference_audio"] = base64.b64encode(ref).decode()
        # No seed asked for: a new one every time (without one the server always uses the same, so every answer to the
        # same text came out the same).
        payload["seed"] = int(seed) if seed is not None and seed != -1 else random.randint(0, 2**31 - 1)
        # Guidance: 2.6 when not given; never above 4, where the voice gets louder and louder and the server's 16-bit
        # output clips (it shouts and clicks: measured rms 1.5-5 k at 2, 10-13 k at 5).
        payload["cfg_value"] = (min(MAX_GUIDANCE, max(MIN_GUIDANCE, float(guidance_scale)))
                                if guidance_scale else DEFAULT_GUIDANCE)
        # The CFM's steps (15 when not given; the server's own is 10) and randomness (0.5-0.9, 0.9 when not given).
        payload["inference_timesteps"] = max(1, int(steps)) if steps else DEFAULT_STEPS
        payload["temperature"] = (min(MAX_TEMPERATURE, max(MIN_TEMPERATURE, float(temperature)))
                                  if temperature is not None else DEFAULT_TEMPERATURE)
        return payload

    def _request(self, path, payload):
        import json, urllib.request
        req = urllib.request.Request(f"http://{self.host}:{self.port}{path}", data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=600)

    def generate(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "", speed: float = 1.0,
                 guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr",
                 abort_event: Optional[Any] = None, temperature: Optional[float] = None) -> Tuple[int, np.ndarray]:
        if not text or not text.strip():
            return self.SAMPLE_RATE, np.array([], dtype=np.int16)
        try:
            with self._request("/v1/audio/speech", self._payload(text, voice_cache, instruct, guidance_scale, seed, steps, temperature)) as res:
                rate, audio = streamed_wav(res.read())
            if rate == 2 * self.SAMPLE_RATE:
                half = HalfRate()
                audio = np.concatenate([half.feed(audio.tobytes()), half.flush()])
            leveler = Leveler()
            audio = np.concatenate([leveler.feed(audio), leveler.flush()])
            return self.SAMPLE_RATE, audio
        except Exception as e:
            logger.error(f"llama-tts-server request failed: {e}")
            return 500, np.array([], dtype=np.int16)

    def generate_stream(self, text: str, voice_cache: Any = None, voice_name: str = "", instruct: str = "",
                        speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1,
                        language: str = "tr", abort_event: Optional[Any] = None,
                        temperature: Optional[float] = None):
        yield self.SAMPLE_RATE
        if not text or not text.strip():
            return
        try:
            with self._request("/v1/audio/speech/stream", self._payload(text, voice_cache, instruct, guidance_scale, seed, steps, temperature)) as res:
                head = b""
                while b"data" not in head[:-4] and len(head) < 4096:  # The WAV header, then PCM as it comes.
                    chunk = res.read(64)
                    if not chunk:
                        return
                    head += chunk
                at = head.find(b"data")
                if len(head) < at + 8:
                    head += res.read(at + 8 - len(head))
                half = HalfRate() if int.from_bytes(head[24:28], "little") == 2 * self.SAMPLE_RATE else None
                leveler, odd = Leveler(), b""
                pending = head[at + 8:]
                while True:
                    if pending:
                        if half:
                            samples = half.feed(pending)
                        else:  # Whole 16-bit samples only: an odd byte goes in front of the next piece.
                            data, odd = odd + pending, b""
                            if len(data) % 2:
                                data, odd = data[:-1], data[-1:]
                            samples = np.frombuffer(data, dtype=np.int16)
                        out = leveler.feed(samples).tobytes()
                        if out:
                            yield out
                    if abort_event and abort_event.is_set():
                        return
                    pending = res.read(4096)
                    if not pending:
                        break
                tail = half.flush() if half else np.zeros(0, dtype=np.int16)
                tail = np.concatenate([leveler.feed(tail), leveler.flush()]).tobytes()
                if tail:
                    yield tail
        except Exception as e:
            logger.error(f"llama-tts-server stream failed: {e}")


# What VoxCPM2 speaks with when the request has neither a description nor a cloned voice (without one the voice
# is a different one with every seed).
DEFAULT_VOICE_DESIGN = "A clear voice sweet girl."
# The default-voice warm-up used when no clone reference or voice description is supplied: a short sentence alone
# ("Selam naber.") was read like by someone who does not speak Turkish; after this it is not.
PRIME_TEXT = "Merhaba bugün nasılsınız?"
PRIME_SEED = 42424
DEFAULT_GUIDANCE = 3.0  # what pages show as creativity 1 (0-2 -> 2-4)
MIN_GUIDANCE = 2.0
MAX_GUIDANCE = 4.0
DEFAULT_STEPS = 10  # the model's own default
MIN_TEMPERATURE = 0.5
MAX_TEMPERATURE = 0.9
DEFAULT_TEMPERATURE = 0.9  # when the request does not say (Router, Hub); the 8810 page starts here too
# The warm-up voices are made with these, fixed, so a change of the defaults above does not change them.
PRIME_GUIDANCE = 2.6
PRIME_STEPS = 15
PRIME_TEMPERATURE = 0.75


class Leveler:
    """Keeps VoxCPM2's loudness in check, as the audio arrives (24 kHz, 16-bit mono). A higher guidance (cfg_value)
    makes the voice louder and louder (measured rms: cfg 2 ~1.5-5 k, 3 ~3-7.7 k, 5 ~10-13 k, 8 ~17-19 k; it shouts),
    so the level is only ever turned down, towards TARGET: the speech's loudness is followed over 20 ms frames (fast
    up, slow down, silence ignored) and the gain glides from frame to frame, so there are no steps. Quiet speech
    stays as it is. The old PyTorch engine scaled each whole answer to its peak instead, which a stream cannot."""

    FRAME = 480          # 20 ms
    TARGET = 3000.0      # rms
    SPEECH = 300.0       # frames quieter than this are silence and do not move the level

    def __init__(self):
        self.level = self.TARGET
        self.gain = 1.0
        self.rest = np.zeros(0)

    def _frame(self, x):
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > self.SPEECH:
            self.level += (0.5 if rms > self.level else 0.05) * (rms - self.level)
        gain = min(1.0, self.TARGET / self.level)
        out = x * np.linspace(self.gain, gain, len(x), endpoint=False)
        self.gain = gain
        return out

    def feed(self, samples: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.rest, samples.astype(np.float64)])
        whole = len(x) // self.FRAME * self.FRAME
        self.rest = x[whole:]
        if not whole:
            return np.zeros(0, dtype=np.int16)
        out = np.concatenate([self._frame(x[i:i + self.FRAME]) for i in range(0, whole, self.FRAME)])
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)

    def flush(self) -> np.ndarray:
        x, self.rest = self.rest, np.zeros(0)
        return np.clip(np.round(x * self.gain), -32768, 32767).astype(np.int16)


class HalfRate:
    """48 kHz -> 24 kHz, 16-bit mono, as the audio arrives: a windowed-sinc low-pass (cut at 11 kHz, below the new
    Nyquist) and every second sample. The filter's past and the odd byte or sample carry over between pieces, so
    the joins are seamless. (Dropping every other sample without the filter folds 12-24 kHz into the voice.)"""

    TAPS = 63

    def __init__(self):
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        cut = 11000 / 48000
        h = 2 * cut * np.sinc(2 * cut * n) * np.blackman(self.TAPS)
        self.h = h / h.sum()
        self.history = np.zeros(self.TAPS - 1)
        self.byte = b""
        self.phase = 0  # Which input sample is the next kept one (0 or 1), counted over the whole stream.
        self.pad = (self.TAPS - 1) // 2  # The filter's delay, given back at flush.

    def feed(self, data: bytes) -> np.ndarray:
        data = self.byte + data
        even = len(data) & ~1
        self.byte = data[even:]
        x = np.frombuffer(data[:even], dtype=np.int16).astype(np.float64)
        if not len(x):
            return np.array([], dtype=np.int16)
        joined = np.concatenate([self.history, x])
        y = np.convolve(joined, self.h, mode="valid")  # One output per new input sample.
        self.history = joined[-(self.TAPS - 1):]
        # Skip the delay at the start so the output lines up with the input (no 0.6 ms of silence first).
        skip = min(self.pad, len(y))
        self.pad -= skip
        y = y[skip:]
        kept = y[self.phase::2]
        self.phase = (self.phase + len(y)) % 2
        return np.clip(np.round(kept), -32768, 32767).astype(np.int16)

    def flush(self) -> np.ndarray:
        """The last samples still inside the filter (its delay), once the input has ended."""
        # Silence as long as the delay pushes them out; whatever delay is still unskipped (a stream shorter than
        # the filter) is skipped from these, so the output always has half as many samples as the input.
        return self.feed(np.zeros((self.TAPS - 1) // 2, dtype=np.int16).tobytes())


# A cloned voice's sample goes into every request together with the new sentence, so its length costs on every
# one (RTX 3060 Ti, CUDA, 32 steps, real-time factor: no sample 0.24, 4.5 s sample 0.27, 17 s 0.46, 52 s 1.16;
# docs/bilesenler.md). About 5 s is enough for the voice.
CLONE_RECOMMENDED_SECONDS = 5
# The upper limit: OmniVoice itself trims a sample past 20 s (upstream trim_long_audio), and well before the
# model's context the card runs out: on 8 GB a 69 s sample took +4.9 GB and spoke at 2.6x real time, a 121 s
# one spilled into system memory and needed over 15 minutes for one sentence.
CLONE_MAX_SECONDS = 20
RVQ_FRAMES_PER_SECOND = 25  # omnivoice-tokenizer: 24 kHz, hop 960.
RVQ_BITS_PER_FRAME = 8 * 11  # 8 codebooks of 11 bits, packed with no header (omnivoice-gguf/src/rvq-file.h).


def rvq_seconds(rvq_bytes: bytes) -> float:
    """How long the audio behind an OmniVoice .rvq code stream is."""
    return round(len(rvq_bytes) * 8 // RVQ_BITS_PER_FRAME / RVQ_FRAMES_PER_SECOND, 2)


def check_clone_seconds(seconds: float) -> None:
    if seconds > CLONE_MAX_SECONDS:
        raise ValueError(
            f"Ses örneği {seconds:.1f} saniye; en çok {CLONE_MAX_SECONDS} saniye olabilir. Örnek her konuşmada yeni "
            f"cümleyle birlikte işlendiği için uzun örnek her konuşmayı yavaşlatır (17 saniyelik örnek 5 saniyelikten "
            f"~1,7 kat yavaş). Yaklaşık {CLONE_RECOMMENDED_SECONDS} saniyelik temiz bir kayıt yeterli.")


def clone_advice(seconds: float) -> str:
    """What to tell the user about an accepted sample that is longer than recommended ('' when it is not)."""
    if seconds <= CLONE_RECOMMENDED_SECONDS + 1:
        return ""
    return (f"Ses örneği {seconds:.1f} saniye. Kaydedildi, ama daha uzun örnekler her konuşmanın süresini uzatır; "
            f"yaklaşık {CLONE_RECOMMENDED_SECONDS} saniyelik temiz bir kayıt önerilir.")


REF_TARGET_RMS = 2500.0      # the speech's level after tidying (about -22 dBFS); the clones came out loud
REF_PEAK = 0.9 * 32767       # and never past this
REF_MARGIN = 0.15            # seconds of quiet kept before and after the speech
REF_LOWPASS_HZ = 5000        # reduce unusually sharp sibilants copied from clone recordings
def tidy_reference(wav_data: bytes) -> bytes:
    """A clone sample made even, once, when the voice is cloned (both engines): to mono, the silence before and
    after the speech cut (it is processed with every sentence, and the model copies it), and the speech brought to
    one level (a loud sample made a loud clone). Anything that is not a 16-bit PCM WAV is returned as it came."""
    try:
        with wave.open(io.BytesIO(wav_data)) as w:
            rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
        if width != 2 or channels < 1:
            return wav_data
        x = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
        x = x[:len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
        hop = max(1, rate // 100)  # 10 ms
        n = len(x) // hop
        if n < 3:
            return wav_data
        level = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
        loud = level > max(100.0, level.max() * 10 ** (-35 / 20))
        if not loud.any():
            return wav_data
        first, last = int(np.argmax(loud)), int(n - 1 - np.argmax(loud[::-1]))
        margin = int(REF_MARGIN * rate)
        x = x[max(0, first * hop - margin):min(len(x), (last + 1) * hop + margin)]
        speech = level[loud].mean()
        gain = REF_TARGET_RMS / speech if speech > 0 else 1.0
        if np.abs(x).max() * gain > REF_PEAK:
            gain = REF_PEAK / np.abs(x).max()
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.clip(np.round(x * gain), -32768, 32767).astype(np.int16).tobytes())
        return out.getvalue()
    except Exception:
        return wav_data


def soften_pcm16(samples: np.ndarray, sample_rate: int, cutoff: float = REF_LOWPASS_HZ) -> np.ndarray:
    """Gently roll off sibilant highs above 5 kHz without changing overall gain."""
    x = np.asarray(samples, dtype=np.int16)
    if not x.size or cutoff >= sample_rate / 2:
        return x
    spectrum = np.fft.rfft(x.astype(np.float64))
    frequencies = np.fft.rfftfreq(len(x), 1 / sample_rate)
    softened = np.fft.irfft(spectrum / (1 + (frequencies / cutoff) ** 8), len(x))
    return np.clip(np.round(softened), -32768, 32767).astype(np.int16)


def soften_reference(wav_data: bytes, cutoff: float = REF_LOWPASS_HZ) -> bytes:
    """Gently roll off clone-recording highs above 5 kHz, once at clone save time."""
    try:
        with wave.open(io.BytesIO(wav_data)) as w:
            rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
        if width != 2 or channels != 1 or cutoff >= rate / 2:
            return wav_data
        samples = soften_pcm16(np.frombuffer(frames, dtype=np.int16), rate, cutoff)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(samples.tobytes())
        return out.getvalue()
    except Exception:
        return wav_data


def streamed_wav(wav_data: bytes) -> Tuple[int, np.ndarray]:
    """tts-server streams its WAV, so the header's sizes describe only the first part (a 2.4 s sentence came
    back as 0.45 s). Every byte after the "data" tag is audio."""
    import struct
    if len(wav_data) < 44 or wav_data[:4] != b"RIFF":
        raise ValueError("tts-server did not send a WAV")
    sample_rate = struct.unpack_from("<I", wav_data, 24)[0]
    at = wav_data.find(b"data", 12)
    if at < 0:
        raise ValueError("tts-server WAV has no data")
    pcm = wav_data[at + 8:]
    return sample_rate, np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype=np.int16)


def get_engine():
    engine_name = get_env("ENGINE_NAME", "omnivoice", required=False).lower()
    model_path = get_env("MODEL_PATH", default=None, required=False)
    
    logger.info(f"Yüklenen TTS Motoru: {engine_name}")

    engine = None
    if engine_name == "voxcpm2":
        engine = VoxCPMEngine(model_id=model_path or "openbmb/VoxCPM2")
    elif engine_name == "omnivoice":
        engine = OmniVoiceEngine(model_id=model_path or "k2-fsa/OmniVoice")
    elif engine_name in ("omnivoice-gguf", "omnivoice_gguf", "gguf"):
        # Bileşenler names both files (a user's own model may call them anything).
        engine = OmniVoiceGGUFEngine(model_path=model_path,
                                     codec_path=get_env("CODEC_PATH", default=None, required=False))
    elif engine_name == "voxcpm2-gguf":
        engine = VoxCPM2GGUFEngine(model_path=model_path,
                                   acoustic_path=get_env("ACOUSTIC_PATH", default=None, required=False))
    else:
        raise ValueError(f"Hata: '{engine_name}' adında bir motor bulunamadı.")
    
    engine.cleanup()
    return engine

def valid_voice_name(name) -> bool:
    """A voice name becomes a file name: no folders, "..", or glob characters,
    or a request could read, write or delete .pt files elsewhere on the disk."""
    import re
    name = str(name).strip()
    return bool(re.fullmatch(r"\w[\w .-]{0,63}", name)) and ".." not in name


def voice_data_dir() -> Path:
    """Writable TTS user data, colocated with the selected, relocatable TTS model store."""
    configured = os.environ.get("ORION_TTS_VOICE_DATA_DIR")
    if configured:
        return Path(configured)
    models_dir = os.environ.get("ORION_NATIVE_MODELS_DIR")
    return (Path(models_dir) if models_dir else Path.cwd()) / "voices"


class VoiceRegistry:
    def __init__(self, storage_path=None):
        self.storage_path = Path(storage_path) if storage_path is not None else voice_data_dir()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.service_voices_path = Path(os.path.dirname(__file__)) / "voices"

    def _get_search_dirs(self) -> list[Path]:
        dirs = []
        if self.storage_path.exists():
            dirs.append(self.storage_path)
        if self.service_voices_path.exists() and self.storage_path.resolve() != self.service_voices_path.resolve():
            dirs.append(self.service_voices_path)
        return dirs

    def _find_voice_file(self, name: str, engine_name: str) -> Path | None:
        name_clean = str(name).strip().lower()
        if not valid_voice_name(name_clean):
            return None
        
        target_engine = engine_name.lower().strip()
        target = f"{name_clean}_{target_engine}.pt"

        for d in self._get_search_dirs():
            p = d / target
            if p.exists():
                return p
            for f in d.glob("*.pt"):
                if f.name.lower() == target:
                    return f
        return None

    def find_voice_in_any_engine(self, name: str) -> list[str]:
        """Returns list of other engine names where this voice exists."""
        name_clean = str(name).strip().lower()
        if not valid_voice_name(name_clean):
            return []
        found_in = set()
        prefix = f"{name_clean}_"
        for d in self._get_search_dirs():
            for f in d.glob(f"{name_clean}_*.pt"):
                f_lower = f.name.lower()
                if f_lower.startswith(prefix) and f_lower.endswith(".pt"):
                    eng = f_lower[len(prefix):-3]
                    if eng:
                        found_in.add(eng)
        return sorted(list(found_in))

    def get_voice_cache(self, name: str, engine_name: str) -> Any:
        path = self._find_voice_file(name, engine_name)
        if not path or not path.exists():
            return None
        try:
            if TORCH_AVAILABLE:
                return torch.load(path, weights_only=False)
            else:
                import pickle
                with open(path, "rb") as f:
                    return pickle.load(f)
        except Exception as e:
            logger.error(f"Failed to load voice cache {path}: {e}")
            return None

    def voice_exists(self, name: str, engine_name: str) -> bool:
        return self._find_voice_file(name, engine_name) is not None

    def delete_voice(self, name: str, engine_name: str) -> bool:
        if not valid_voice_name(str(name).strip()):
            return False
        target = f"{str(name).strip()}_{engine_name.lower().strip()}.pt".lower()
        for path in self.storage_path.glob("*.pt"):
            if path.name.lower() == target:
                path.unlink()
                return True
        return False

    def save_voice_cache(self, name: str, cache_obj: Any, engine_name: str):
        name = str(name).strip()
        if not valid_voice_name(name):
            raise ValueError("Invalid voice name")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        path = self.storage_path / f"{name}_{engine_name}.pt"
        if TORCH_AVAILABLE:
            torch.save(cache_obj, path)
        else:
            import pickle
            with open(path, "wb") as f:
                pickle.dump(cache_obj, f)
        return str(path)

    def list_voices(self, engine_name: str) -> list[str]:
        target_engine = engine_name.lower().strip()
        suffix = f"_{target_engine}.pt"

        names = set()
        for d in self._get_search_dirs():
            for f in d.glob("*.pt"):
                f_name_lower = f.name.lower()
                if f_name_lower.endswith(suffix):
                    names.add(f_name_lower[:-len(suffix)])
        return sorted(list(names))
