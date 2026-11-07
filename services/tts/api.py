import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from dotenv import load_dotenv
    _local_env = os.path.join(os.path.dirname(__file__), ".env")
    # Started by Orion's Bileşenler page: its settings come from there, not from a developer's .env here.
    if os.path.exists(_local_env) and not os.getenv("ORION_PART"):
        load_dotenv(_local_env, override=True)
except ImportError:
    pass

from services.shared.environment import get_env
from services.shared.service_access import BrowserGuard, bind_host
import time
import uuid
import logging
import threading
import asyncio
import traceback
import struct

if str(get_env("GPU_COUNT", default="1", required=False)).strip() == "0":
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["GGML_BACKEND"] = "CPU"
try:
    import torch
    def is_cuda_available(): return torch.cuda.is_available()
except ImportError:
    torch = None
    def is_cuda_available(): return False
import gc

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from engines import (CLONE_MAX_SECONDS, CLONE_RECOMMENDED_SECONDS, VoiceRegistry, clone_advice, get_engine,
                     valid_voice_name)
from schemas import SpeechRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TTS_PORT = get_env("TTS_PORT", default=8810, cast=int, required=False)

# ——————————————————————————————————————————
# Config Helpers
# ——————————————————————————————————————————
def _parse_bool_env(name: str, default: bool = False) -> bool:
    val = get_env(name, default=None, required=False)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

def _parse_idle_cleanup_mins(default: int = 10) -> int:
    raw = get_env("IDLE_CLEANUP_MINS", str(default), required=False)
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning(f"IDLE_CLEANUP_MINS gecersiz ('{raw}'). Varsayilan {default} kullaniliyor.")
        return default

def create_wav_header(sample_rate: int, data_size: int) -> bytes:
    """44 byte standart WAV header (Mono, 16-bit)."""
    return struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE', b'fmt ', 16, 1, 1, 
        sample_rate, sample_rate * 2, 2, 16, b'data', data_size)

# ——————————————————————————————————————————
# Global State & Engine
# ——————————————————————————————————————————
engine = None
registry = VoiceRegistry("voices")
engine_name_env = get_env("ENGINE_NAME", "omnivoice", required=False).lower().strip()
model_path_env = (get_env("MODEL_PATH", default="", required=False) or "").lower()
is_omnivoice = "omnivoice" in engine_name_env or "omnivoice" in model_path_env
low_vram_enabled = _parse_bool_env("LOW_VRAM", default=False)

# Idle Cleanup
last_request_time = time.time()
idle_threshold_mins = _parse_idle_cleanup_mins(default=10)
async_inference_lock = asyncio.Lock()

async def idle_cleanup_worker():
    global last_request_time
    if idle_threshold_mins <= 0:
        logger.info("Idle VRAM cleanup disabled.")
        return

    logger.info(f"Idle VRAM cleanup worker started ({idle_threshold_mins} mins)")
    while True:
        await asyncio.sleep(60)
        idle_time = (time.time() - last_request_time) / 60
        if idle_time >= idle_threshold_mins:
            if not async_inference_lock.locked():
                if is_cuda_available():
                    logger.info(f"Sistem {int(idle_time)} dakikadır boşta. VRAM temizleniyor...")
                    if engine: engine.cleanup()
                    last_request_time = time.time()
            else:
                last_request_time = time.time()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    try:
        engine = get_engine()
        logger.info("TTS Engine loaded and ready.")
    except Exception:
        logger.error(f"Failed to load engine: {traceback.format_exc()}")
    
    cleanup_task = asyncio.create_task(idle_cleanup_worker())
    yield
    cleanup_task.cancel()
    if engine: engine.cleanup()

app = FastAPI(title="Orion TTS API", version="1.0.4", lifespan=lifespan)
app.add_middleware(BrowserGuard)

# ——————————————————————————————————————————
# UI & Static
# ——————————————————————————————————————————
_current_dir = os.path.dirname(os.path.abspath(__file__))
_dashboard_dir = os.path.join(_current_dir, "dashboard")

@app.get("/health")
async def health():
    # Served only after startup (engine load) completes; the lifecycle supervisor polls this for readiness.
    # Not healthy while the engine's own server is down (it is started again with the next request).
    if engine is not None and not engine.server_alive():
        _restart_engine_soon()
        return JSONResponse({"status": "engine_down"}, status_code=503)
    return {"status": "ok"}

_engine_restart = {"running": False, "at": 0.0}

def _restart_engine_soon():
    """The engine's server is down (it died, or did not start: e.g. the card was full): try it again in the
    background, at most every 30 s, so the service comes back by itself without waiting for a request."""
    if _engine_restart["running"] or time.time() - _engine_restart["at"] < 30:
        return
    _engine_restart.update(running=True, at=time.time())

    def run():
        try:
            engine.ensure_server()
        except Exception as e:
            logger.error(f"Engine restart failed: {e}")
        finally:
            _engine_restart["running"] = False
    threading.Thread(target=run, daemon=True).start()

@app.get("/")
async def read_index():
    index_path = os.path.join(_dashboard_dir, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"status": "running", "service": "Orion TTS (Speech)"})
    return FileResponse(index_path)

if os.path.exists(_dashboard_dir):
    app.mount("/dashboard", StaticFiles(directory=_dashboard_dir), name="dashboard")

# ——————————————————————————————————————————
# Speech Generation
# ——————————————————————————————————————————
@app.post("/v1/audio/speech")
async def create_speech(speech_request: SpeechRequest, request: Request):
    global last_request_time
    last_request_time = time.time()
    tts = engine
    if not tts: raise HTTPException(status_code=503, detail="Engine not loaded")

    abort_event = threading.Event()
    async def monitor_disconnect():
        try:
            while not abort_event.is_set():
                if await request.is_disconnected():
                    abort_event.set()
                    break
                await asyncio.sleep(0.5)
        except asyncio.CancelledError: pass

    monitor_task = asyncio.create_task(monitor_disconnect())
    should_stream = bool(speech_request.stream and not is_omnivoice)

    try:
        if not tts.server_alive():
            async with async_inference_lock:
                await run_in_threadpool(tts.ensure_server)
            if not tts.server_alive():
                raise HTTPException(status_code=503, detail="Ses motoru başlatılamadı (bilgisayarda yeterli bellek olmayabilir).")
        req_voice = (speech_request.voice or "").strip()
        voice_cache = None
        if req_voice and req_voice.lower() not in ("none", "default", "null", "alloy", ""):
            voice_cache = registry.get_voice_cache(req_voice, engine_name_env)
            if not voice_cache and not registry.voice_exists(req_voice, engine_name_env):
                available = registry.list_voices(engine_name_env)
                avail_str = f" Aktif motor ({engine_name_env}) için mevcut sesler: {', '.join(available)}" if available else f" Aktif motor ({engine_name_env}) için kayıtlı ses yok."
                other_engines = registry.find_voice_in_any_engine(req_voice)
                if other_engines:
                    detail_msg = (
                        f"Bu ses klonu ('{req_voice}') şu anki aktif motor ('{engine_name_env}') ile uyumlu değil "
                        f"(farklı motorlar için üretilmiş: {', '.join(other_engines)}).{avail_str}"
                    )
                else:
                    detail_msg = f"Böyle bir ses klonu bulunamadı: '{req_voice}'.{avail_str}"
                raise HTTPException(status_code=400, detail=detail_msg)

        # VoxCPM2's own extra (the other engines do not take it).
        extra = {"temperature": speech_request.temperature} if engine_name_env == "voxcpm2-gguf" else {}
        guidance = speech_request.guidance_scale
        if guidance is None and speech_request.creativity is not None:
            # The Router's 0-2 "temperature" is the creativity: OmniVoice takes it as its guidance, VoxCPM2 lines it up
            # with its own 2-4 (0 -> 2, 1 -> 3, 2 -> 4).
            level = min(2.0, max(0.0, float(speech_request.creativity)))
            guidance = level + 2.0 if engine_name_env == "voxcpm2-gguf" else level
        # Steps not given: the engine's own default (VoxCPM2 10, in engines.py); OmniVoice keeps the 15 it always got.
        steps_val = speech_request.steps or (None if engine_name_env == "voxcpm2-gguf" else 15)
        if guidance is None and engine_name_env != "voxcpm2-gguf":
            guidance = 2.0  # OmniVoice's own default; VoxCPM2 picks its own (engines.py)
        generic_model_names = {"local-model", "local-tts", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "voxcpm2-gguf", "default", "none"}
        instruct_val = getattr(speech_request, "instructions", None)
        if not instruct_val:
            model_val = (speech_request.model or "").strip()
            if model_val.lower() not in generic_model_names:
                instruct_val = model_val
            else:
                instruct_val = ""

        if should_stream:
            await async_inference_lock.acquire()
            try:
                if abort_event.is_set():
                    async_inference_lock.release()
                    return JSONResponse({"status": "aborted"}, status_code=499)

                stream_gen = tts.generate_stream(
                    text=speech_request.input, voice_cache=voice_cache, voice_name=speech_request.voice, instruct=instruct_val or "",
                    speed=speech_request.speed, guidance_scale=guidance,
                    steps=steps_val, seed=speech_request.seed, language=speech_request.language,
                    abort_event=abort_event, **extra
                )
                sr = await run_in_threadpool(next, stream_gen)

                async def audio_stream_generator():
                    try:
                        # The engine's generator blocks while it makes each piece: run it in a thread so the event
                        # loop sends every piece as it comes (inline, nothing left until ~64 KB had piled up).
                        async for chunk in iterate_in_threadpool(stream_gen):
                            if abort_event.is_set(): break
                            yield chunk
                    finally:
                        async_inference_lock.release()
                        abort_event.set()

                return StreamingResponse(audio_stream_generator(), media_type="audio/pcm", headers={"X-Sample-Rate": str(sr)})
            except Exception as e:
                async_inference_lock.release()
                raise e
        else:
            async with async_inference_lock:
                if abort_event.is_set(): return JSONResponse({"status": "aborted"}, status_code=499)
                # In a thread, as the stream: the event loop keeps answering (health, a hang-up that aborts this).
                sr, audio_data = await run_in_threadpool(
                    tts.generate,
                    text=speech_request.input, voice_cache=voice_cache, voice_name=speech_request.voice, instruct=instruct_val or "",
                    speed=speech_request.speed, guidance_scale=guidance,
                    steps=steps_val, seed=speech_request.seed, language=speech_request.language,
                    abort_event=abort_event, **extra
                )
            
            if sr == 500:  # The engine's failure value (engines.py): not an empty "success"
                raise HTTPException(status_code=500, detail="Ses motoru bu isteğe yanıt veremedi.")
            header = create_wav_header(sr, len(audio_data) * 2)
            return Response(content=header + audio_data.tobytes(), media_type="audio/wav", headers={"X-Sample-Rate": str(sr)})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generation error: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        last_request_time = time.time()
        if not should_stream:
            abort_event.set()
            # Not awaited: the watcher sleeps 0.5 s between checks and now runs while the engine works, so
            # waiting for it held every answer back by up to half a second.
            monitor_task.cancel()

# ——————————————————————————————————————————
# Management & Info
# ——————————————————————————————————————————
class ReloadRequest(BaseModel): pass

@app.post("/v1/engine/reload")
async def reload_engine(reload_req: ReloadRequest):
    global engine
    async with async_inference_lock:
        try:
            if engine: engine.cleanup()
            engine = await run_in_threadpool(get_engine)
            return {"status": "success"}
        except Exception as e:
            logger.error(f"Reload error: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/voices/clone")
async def clone_voice(name: str = Form(...), file: UploadFile = File(...), text: str = Form("")):
    name = (name or "").strip()  # "zephyr2  " is "zephyr2": spaces around a name made a second, unreachable file
    if not valid_voice_name(name):
        raise HTTPException(status_code=400, detail="Voice names use letters, digits, space, '.', '_' or '-' (max 64)")
    if registry.voice_exists(name, engine_name_env):
        raise HTTPException(status_code=400, detail="Voice already exists")
    tts = engine
    temp_path = f"/tmp/{uuid.uuid4()}.wav"
    with open(temp_path, "wb") as f: f.write(await file.read())
    try:
        try:
            # The engine works in a thread now (speech too), so a clone waits its turn instead of sharing it.
            async with async_inference_lock:
                await run_in_threadpool(engine.ensure_server)
                cache_obj = await run_in_threadpool(engine.encode_voice, temp_path, text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
            
        saved_path = registry.save_voice_cache(name, cache_obj, engine_name_env)
        if hasattr(engine, "registered_voices") and name in engine.registered_voices:
            engine.registered_voices.remove(name)
        seconds = cache_obj.get("ref_seconds") if isinstance(cache_obj, dict) else None
        return {"status": "success", "voice_name": name, "path": saved_path, "seconds": seconds,
                "warning": clone_advice(seconds) if seconds else ""}
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

_ENGINE_NAMES = {"omnivoice": "omnivoice-gguf", "voxcpm2": "voxcpm2-gguf"}

@app.get("/v1/voices")
async def list_voices(engine: Optional[str] = None):
    """The cloned voices of the running engine, or of the one asked for (the settings pages edit both engines'
    voices whichever one runs: ?engine=omnivoice or voxcpm2)."""
    target = _ENGINE_NAMES.get((engine or "").lower().strip(), (engine or "").strip() or engine_name_env)
    return {"voices": registry.list_voices(target)}

@app.delete("/v1/voices/{name}")
async def delete_voice(name: str):
    if registry.delete_voice(name, engine_name_env): 
        if hasattr(engine, "registered_voices") and name in engine.registered_voices:
            engine.registered_voices.remove(name)
        return {"status": "success"}
    raise HTTPException(status_code=404)

@app.get("/v1/prime")
async def prime_audio(voice: str = ""):
    """VoxCPM2 only: the audio every sentence continues, to listen to: a clone's own sample (or, without its words,
    the warm-up made from it) when `voice` names one, the default voice's warm-up otherwise. A WAV."""
    tts = engine
    if not tts or not hasattr(tts, "_plain_prime_wav"):
        raise HTTPException(status_code=404, detail="Bu motorda hazırlık sesi yok.")
    name = (voice or "").strip()
    if name:
        cache = registry.get_voice_cache(name, engine_name_env)
        if not isinstance(cache, dict):
            raise HTTPException(status_code=404, detail="Böyle bir klon yok.")
        # What the engine continues: the clone's own sample when it has its words, else the warm-up made from it.
        if (cache.get("ref_text") or "").strip() and cache.get("ref_wav"):
            data = cache["ref_wav"]
        elif cache.get("prime_wav"):
            data = cache["prime_wav"]
        else:
            raise HTTPException(status_code=404, detail="Bu klon metinsiz ve eski; devam edilecek ses yok.")
    else:
        try:
            data = await run_in_threadpool(tts._plain_prime_wav)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Hazırlık sesi hazır değil: {e}")
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

_OMNIVOICE_LANGS = ["Auto", "Turkish", "English"]

_INSTRUCT_CATEGORIES = [
    {"male": "男", "female": "女"},
    {"child": "儿童", "teenager": "少年", "young adult": "青年",
     "middle-aged": "中年", "elderly": "老年"},
    {"very low pitch": "极低音调", "low pitch": "低音调",
     "moderate pitch": "中音调", "high pitch": "高音调",
     "very high pitch": "极高音调"},
    {"whisper": "耳语"},
    {"american accent", "british accent", "australian accent",
     "chinese accent", "canadian accent", "indian accent",
     "korean accent", "portuguese accent", "russian accent", "japanese accent"},
    {"河南话", "陕西话", "四川话", "贵州话", "云南话", "桂林话",
     "济南话", "石家庄话", "甘肃话", "宁夏话", "青岛话", "东北话"},
]

friendly_labels = {
    "male": "Erkek (Male)",
    "female": "Kadın (Female)",
    "child": "Çocuk (Child)",
    "teenager": "Genç (Teenager)",
    "young adult": "Genç Yetişkin (Young Adult)",
    "middle-aged": "Orta Yaşlı (Middle-aged)",
    "elderly": "Yaşlı (Elderly)",
    "very low pitch": "Çok Kalın (Very Low)",
    "low pitch": "Kalın (Low)",
    "moderate pitch": "Orta (Moderate)",
    "high pitch": "İnce (High)",
    "very high pitch": "Çok İnce (Very High)",
    "whisper": "Fısıltı (Whisper)",
    "american accent": "Amerikan (American)",
    "british accent": "İngiliz (British)",
    "australian accent": "Avustralya (Australian)",
    "chinese accent": "Çin (Chinese)",
    "canadian accent": "Kanada (Canadian)",
    "indian accent": "Hint (Indian)",
    "korean accent": "Kore (Korean)",
    "portuguese accent": "Portekiz (Portuguese)",
    "russian accent": "Rus (Russian)",
    "japanese accent": "Japon (Japanese)",
    "河南话": "Henan (河南话)",
    "陕西话": "Shaanxi (陕西话)",
    "四川话": "Sichuan (四川话)",
    "贵州话": "Guizhou (贵州话)",
    "云南话": "Yunnan (云南话)",
    "桂林话": "Guilin (桂林话)",
    "济南话": "Jinan (济南话)",
    "石家庄话": "Shijiazhuang (石家庄话)",
    "甘肃话": "Gansu (甘肃话)",
    "宁夏话": "Ningxia (宁夏话)",
    "青岛话": "Qingdao (青岛话)",
    "东北话": "Dongbei (东北话)",
}

def get_label(val):
    return friendly_labels.get(val, val.title() if isinstance(val, str) else str(val))

_OMNIVOICE_DESIGN_OPTIONS = {
    "gender": [{"value": k, "label": get_label(k)} for k in _INSTRUCT_CATEGORIES[0].keys()],
    "age": [{"value": k, "label": get_label(k)} for k in _INSTRUCT_CATEGORIES[1].keys()],
    "pitch": [{"value": k, "label": get_label(k)} for k in _INSTRUCT_CATEGORIES[2].keys()],
    "style": [{"value": k, "label": get_label(k)} for k in _INSTRUCT_CATEGORIES[3].keys()],
    "accent": sorted([{"value": x, "label": get_label(x)} for x in _INSTRUCT_CATEGORIES[4]], key=lambda item: item["value"]),
    "dialect": sorted([{"value": x, "label": get_label(x)} for x in _INSTRUCT_CATEGORIES[5]], key=lambda item: item["value"]),
}

def _initialize_languages():
    global _OMNIVOICE_LANGS
    # The full list (647) ships as a file made from OmniVoice's lang_map.py: that package (PyTorch) is not in the
    # release, and without it the list was only Auto, Turkish and English.
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnivoice_languages.json"), encoding="utf-8") as f:
            names = json.load(f)
        if names:
            _OMNIVOICE_LANGS = names
            return
    except Exception as e:
        logger.warning(f"omnivoice_languages.json not read: {e}")
    try:
        omnivoice_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnivoice")
        if omnivoice_dir not in sys.path:
            sys.path.insert(0, omnivoice_dir)
        from omnivoice.utils.lang_map import LANG_NAMES, lang_display_name
        if LANG_NAMES:
            _OMNIVOICE_LANGS = ["Auto"] + sorted(lang_display_name(n) for n in LANG_NAMES)
            return
    except Exception as e:
        logger.warning(f"Languages list fallback to default: {e}")

_initialize_languages()  # OmniVoice's languages are offered whichever engine runs

@app.get("/v1/languages")
async def get_languages():
    # OmniVoice's list, whichever engine runs: the settings page edits OmniVoice's defaults too.
    return {"languages": _OMNIVOICE_LANGS}

@app.get("/v1/design_options")
async def get_design_options():
    return _OMNIVOICE_DESIGN_OPTIONS

@app.get("/v1/model_info")
async def get_model_info():
    # The Hub starts every part with the backend the user chose (ORION_HW_ID); torch is not in the GGUF engines.
    hw = (os.getenv("ORION_HW_ID") or "").lower()
    dev = {"cuda": "NVIDIA CUDA", "vulkan": "Vulkan (GPU)", "cpu": "CPU"}.get(hw) or ("NVIDIA CUDA" if is_cuda_available() else "CPU")
    info = {"engine": engine_name_env, "device": dev, "low_vram": low_vram_enabled, "idle_cleanup_mins": idle_threshold_mins}
    if is_omnivoice or engine_name_env == "voxcpm2-gguf":
        # What the clone form says before a file is sent (engines.py checks it again).
        info["clone"] = {"recommended_seconds": CLONE_RECOMMENDED_SECONDS, "max_seconds": CLONE_MAX_SECONDS}
    return info

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=bind_host(), port=TTS_PORT)
