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

    def encode_voice(self, audio_path: str, text: str) -> Any:
        raise NotImplementedError

    def generate(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        raise NotImplementedError

    def generate_stream(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
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
            local_files_only=True,
            attn_implementation="flash_attention_2"
        )

    def encode_voice(self, audio_path: str, text: str) -> Any:
        text_clean = text.strip() if text else None
        return self.model.tts_model.build_prompt_cache(
            prompt_text=text_clean,
            prompt_wav_path=audio_path if text_clean else None,
            reference_wav_path=audio_path
        )

    def generate(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        self.set_seed(seed)
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

    def generate_stream(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
        self.set_seed(seed)
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

    def generate(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
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

    def generate_stream(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
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
            self.server_bin = os.path.join(gguf_dir, "bin", f"tts-server{exe_ext}")
            self.codec_bin = os.path.join(gguf_dir, "bin", f"omnivoice-codec{exe_ext}")

        # Models Path
        models_base = os.path.join(current_dir, "models", "omnivoice-gguf")
        
        if model_path and os.path.isdir(model_path):
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
        self.port = 8809
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_wav = os.path.join(tmp_dir, "ref.wav")
            shutil.copy2(audio_path, tmp_wav)
            cmd = [codec_bin, "--model", self.codec_path, "-i", tmp_wav]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                tmp_rvq = os.path.join(tmp_dir, "ref.rvq")
                if os.path.exists(tmp_rvq):
                    with open(tmp_rvq, "rb") as f:
                        return {"rvq_bytes": f.read(), "ref_text": ref_text}
            except Exception as e:
                logger.error(f"Failed to encode voice with codec: {e}")
                
        raise ValueError("Ses dosyası bozuk veya uyumsuz formatta. Lütfen geçerli bir ses dosyası (örneğin temiz bir WAV) yükleyin.")

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
                self._register_voice_to_server(voice_cache, target_name=target_voice)
                self.registered_voices.add(target_voice)
        elif voice_cache:
            target_voice = self._register_voice_to_server(voice_cache)
        
        try:
            payload = {
                "input": text.strip(),
                "response_format": "wav"
            }
            if target_voice:
                payload["voice"] = target_voice
            generic_models = {"local-model", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "default", "none"}
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
                        wav_data = res.read()
                        import tempfile, wave, os
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
                            tmp_wav.write(wav_data)
                            tmp_wav_path = tmp_wav.name
                            
                        with wave.open(tmp_wav_path, "rb") as wf:
                            sample_rate = wf.getframerate()
                            n_frames = wf.getnframes()
                            frames = wf.readframes(n_frames)
                            audio_data = np.frombuffer(frames, dtype=np.int16)
                            
                        os.remove(tmp_wav_path)
                        return (sample_rate, audio_data)
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
                self._register_voice_to_server(voice_cache, target_name=target_voice)
                self.registered_voices.add(target_voice)
        elif voice_cache:
            target_voice = self._register_voice_to_server(voice_cache)
            is_temp_voice = True
        
        payload = {
            "input": text.strip(),
            "response_format": "pcm"
        }
        if target_voice:
            payload["voice"] = target_voice
        generic_models = {"local-model", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "default", "none"}
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
        engine = OmniVoiceGGUFEngine(model_path=model_path)
    else:
        raise ValueError(f"Hata: '{engine_name}' adında bir motor bulunamadı.")
    
    engine.cleanup()
    return engine

class VoiceRegistry:
    def __init__(self, storage_path="voices"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)
    
    def get_voice_cache(self, name: str, engine_name: str) -> Any:
        path = self.storage_path / f"{name}_{engine_name}.pt"
        if not os.path.exists(path):
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
        path = self.storage_path / f"{name}_{engine_name}.pt"
        return path.exists()

    def delete_voice(self, name: str, engine_name: str) -> bool:
        path = self.storage_path / f"{name}_{engine_name}.pt"
        if path.exists():
            path.unlink()
            return True
        return False

    def save_voice_cache(self, name: str, cache_obj: Any, engine_name: str):
        path = self.storage_path / f"{name}_{engine_name}.pt"
        if TORCH_AVAILABLE:
            torch.save(cache_obj, path)
        else:
            import pickle
            with open(path, "wb") as f:
                pickle.dump(cache_obj, f)
        return str(path)

    def list_voices(self, engine_name: str):
        suffix = f"_{engine_name}.pt"
        return [f.name[:-len(suffix)] for f in self.storage_path.glob(f"*{suffix}")]
