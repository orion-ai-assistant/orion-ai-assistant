import os
from services.shared.environment import get_env
import sys
import torch
import gc
import numpy as np
import logging
from pathlib import Path
from typing import Optional, Tuple, Any

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
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.low_vram = _env_to_bool(get_env("LOW_VRAM", "false"), default=False)

    def set_seed(self, seed: int):
        """Merkezi rastgelelik (seed) ayarı."""
        if seed != -1:
            s = int(seed) % (2**32)
            torch.manual_seed(s)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(s)
            np.random.seed(s)
            import random
            random.seed(s)

    def encode_voice(self, audio_path: str, text: str) -> Any:
        raise NotImplementedError

    def generate(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None) -> Tuple[int, np.ndarray]:
        raise NotImplementedError

    def generate_stream(self, text: str, voice_cache: Any = None, instruct: str = "", speed: float = 1.0, guidance_scale: float = 2.0, steps: Optional[int] = None, seed: int = -1, language: str = "tr", abort_event: Optional[Any] = None):
        raise NotImplementedError

    def cleanup(self):
        gc.collect()
        if torch.cuda.is_available():
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

def get_engine():
    engine_name = get_env("ENGINE_NAME", "omnivoice").lower()
    model_path = get_env("MODEL_PATH", default=None)
    
    logger.info(f"Yüklenen TTS Motoru: {engine_name}")

    engine = None
    if engine_name == "voxcpm2":
        engine = VoxCPMEngine(model_id=model_path or "openbmb/VoxCPM2")
    elif engine_name == "omnivoice":
        engine = OmniVoiceEngine(model_id=model_path or "k2-fsa/OmniVoice")
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
        if path.exists():
            return torch.load(path, weights_only=False)
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
        torch.save(cache_obj, path)
        return str(path)

    def list_voices(self, engine_name: str):
        suffix = f"_{engine_name}.pt"
        return [f.name[:-len(suffix)] for f in self.storage_path.glob(f"*{suffix}")]
