import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from dotenv import load_dotenv
    _local_env = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_local_env):
        load_dotenv(_local_env, override=True)
except ImportError:
    pass

from services.shared.environment import get_env
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
from fastapi.concurrency import run_in_threadpool
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from engines import get_engine, VoiceRegistry
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

# ——————————————————————————————————————————
# UI & Static
# ——————————————————————————————————————————
_current_dir = os.path.dirname(os.path.abspath(__file__))
_dashboard_dir = os.path.join(_current_dir, "dashboard")

@app.get("/")
async def read_index():
    index_path = os.path.join(_dashboard_dir, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"error": "UI file not found"}, status_code=404)
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

        generic_model_names = {"local-model", "local-tts", "tts-1", "tts-1-hd", "omnivoice", "omnivoice-gguf", "voxcpm", "voxcpm2", "default", "none"}
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
                    speed=speech_request.speed, guidance_scale=speech_request.guidance_scale,
                    steps=speech_request.steps, seed=speech_request.seed, language=speech_request.language,
                    abort_event=abort_event
                )
                sr = next(stream_gen)

                async def audio_stream_generator():
                    try:
                        for chunk in stream_gen:
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
                sr, audio_data = tts.generate(
                    text=speech_request.input, voice_cache=voice_cache, voice_name=speech_request.voice, instruct=instruct_val or "",
                    speed=speech_request.speed, guidance_scale=speech_request.guidance_scale,
                    steps=speech_request.steps, seed=speech_request.seed, language=speech_request.language,
                    abort_event=abort_event
                )
            
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
            try: await monitor_task
            except: pass

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
    if registry.voice_exists(name, engine_name_env):
        raise HTTPException(status_code=400, detail="Voice already exists")
    tts = engine
    temp_path = f"/tmp/{uuid.uuid4()}.wav"
    with open(temp_path, "wb") as f: f.write(await file.read())
    try:
        try:
            cache_obj = engine.encode_voice(temp_path, text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
            
        saved_path = registry.save_voice_cache(name, cache_obj, engine_name_env)
        if hasattr(engine, "registered_voices") and name in engine.registered_voices:
            engine.registered_voices.remove(name)
        return {"status": "success", "voice_name": name, "path": saved_path}
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@app.get("/v1/voices")
async def list_voices():
    return {"voices": registry.list_voices(engine_name_env)}

@app.delete("/v1/voices/{name}")
async def delete_voice(name: str):
    if registry.delete_voice(name, engine_name_env): 
        if hasattr(engine, "registered_voices") and name in engine.registered_voices:
            engine.registered_voices.remove(name)
        return {"status": "success"}
    raise HTTPException(status_code=404)

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

if is_omnivoice:
    _initialize_languages()

@app.get("/v1/languages")
async def get_languages():
    return {"languages": _OMNIVOICE_LANGS if is_omnivoice else []}

@app.get("/v1/design_options")
async def get_design_options():
    return _OMNIVOICE_DESIGN_OPTIONS

@app.get("/v1/model_info")
async def get_model_info():
    dev = "NVIDIA CUDA (RTX 3060 Ti)" if is_cuda_available() else "CPU"
    return {"engine": engine_name_env, "device": dev, "low_vram": low_vram_enabled, "idle_cleanup_mins": idle_threshold_mins}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=TTS_PORT)
