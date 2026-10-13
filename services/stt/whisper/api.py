import json
import os
import sys
import uuid
import time
import asyncio
import tempfile
import numpy as np
import wave
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from model_loading import load_whisper_robust
from prompts import select_transcription_prompt, WHISPER_LANGUAGES
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================================================
# CONFIGURATION
# =========================================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    _local_env = os.path.join(CURRENT_DIR, ".env")
    if os.path.exists(_local_env):
        load_dotenv(_local_env, override=True)
except ImportError:
    pass

# Determine device (CUDA if available or specified, fallback to CPU)
hw_id = os.getenv("ORION_HW_ID", "").lower()
env_device = os.getenv("WHISPER_DEVICE", os.getenv("DEVICE", "")).lower()
if hw_id == "cpu" or env_device == "cpu":
    DEVICE = "cpu"
else:
    DEVICE = "cuda"

compute_type_env = os.getenv("WHISPER_COMPUTE_TYPE", "").strip()
if DEVICE == "cpu":
    COMPUTE_TYPE = "int8"
    CPU_THREADS = os.cpu_count() or 4
else:
    COMPUTE_TYPE = compute_type_env if compute_type_env in ["int8_float16", "int8", "float16"] else "int8_float16"
    CPU_THREADS = 0

def resolve_model():
    # 1. Try MODEL_FILE directory (e.g. whisper-small-finetuned-tr/model.bin)
    model_file = os.getenv("MODEL_FILE", "").strip()
    if model_file:
        folder = os.path.dirname(model_file)
        if folder:
            p = os.path.join(CURRENT_DIR, "models", folder)
            if os.path.isdir(p):
                return p

    # 2. Try WHISPER_MODEL
    whisper_model = os.getenv("WHISPER_MODEL", "").strip()
    if whisper_model:
        if os.path.isdir(whisper_model):
            return whisper_model
        clean_name = os.path.basename(whisper_model.rstrip("/\\"))
        p = os.path.join(CURRENT_DIR, "models", clean_name)
        if os.path.isdir(p):
            return p
        if os.path.isdir(f"/models/{clean_name}"):
            return f"/models/{clean_name}"
        return whisper_model

    # 3. Fallback to default local model
    default_p = os.path.join(CURRENT_DIR, "models", "whisper-small-finetuned-tr")
    if os.path.isdir(default_p):
        return default_p
    return "whisper-small"

MODEL_SIZE = resolve_model()

RATE = 16000
LIVE_BEAM_SIZE = 2
FINAL_BEAM_SIZE = 4
SILENCE_DURATION_TO_END = 0.4
MIN_AUDIO_TO_PROCESS = 0.6
MIN_TRANSCRIBE_INTERVAL = 0.2

app = FastAPI(title="Orion STT Whisper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Model Instance
model = None

@app.on_event("startup")
async def startup_event():
    global model, DEVICE, COMPUTE_TYPE
    logger.info(f"Loading Whisper model: {MODEL_SIZE} on {DEVICE.upper()}...")
    try:
        model, DEVICE, COMPUTE_TYPE = load_whisper_robust(MODEL_SIZE, DEVICE, COMPUTE_TYPE)
        logger.info("Whisper model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        raise

# =========================================================
# UI & STATIC
# =========================================================
_dashboard_dir = os.path.join(CURRENT_DIR, "dashboard")

@app.get("/")
async def read_index():
    index_path = os.path.join(_dashboard_dir, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"error": "Dashboard UI not found"}, status_code=404)
    return FileResponse(index_path)

if os.path.exists(_dashboard_dir):
    app.mount("/dashboard", StaticFiles(directory=_dashboard_dir), name="dashboard")


# =========================================================
# REST API (Batch File Transcription)
# =========================================================
@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
):
    if not model:
        raise HTTPException(status_code=503, detail="Model is not loaded yet")
    
    start_time = time.perf_counter()
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
    
    # Save the uploaded file temporarily
    with open(temp_path, "wb") as buffer:
        buffer.write(await file.read())
        
    try:
        # If language is None, empty, or 'auto', faster-whisper will automatically detect the language
        lang_param = None if (not language or language.strip().lower() in ("auto", "none")) else language.strip().lower()
        lang_param, initial_prompt = await asyncio.to_thread(
            select_transcription_prompt, model, temp_path, lang_param, prompt
        )

        # Transcribe directly from the saved temp file
        # faster-whisper uses ffmpeg internally to decode
        segments, info = model.transcribe(
            temp_path,
            task="transcribe",
            beam_size=FINAL_BEAM_SIZE,
            language=lang_param,
            initial_prompt=initial_prompt,
            vad_filter=True
        )
        
        text = "".join([segment.text for segment in segments]).strip()
        infer_duration = time.perf_counter() - start_time
        detected_lang = lang_param if lang_param else (getattr(info, "language", "unknown") if hasattr(info, "language") else "unknown")

        # Cache debug audio ONLY if actual speech text was transcribed
        if text and re.search(r'[a-zA-Z0-9çğıöşüÇĞİÖŞÜ]', text):
            try:
                from faster_whisper.audio import decode_audio
                global last_debug_audio
                last_debug_audio = decode_audio(temp_path, sampling_rate=RATE)
            except Exception as err:
                logger.warning(f"Could not cache debug audio: {err}")

        return JSONResponse({
            "text": text,
            "language": detected_lang,
            "duration": infer_duration
        })
        
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# =========================================================
# WHISPER SUPPORTED LANGUAGES
# =========================================================

@app.get("/v1/audio/languages")
async def get_audio_languages():
    """Whisper tarafından desteklenen dillerin listesini alfabetik olarak döner."""
    langs = [
        {"code": "", "name": "Otomatik Algıla (Auto Detect)"},
    ]
    sorted_items = sorted(WHISPER_LANGUAGES.items(), key=lambda x: x[1].lower())
    for code, name in sorted_items:
        langs.append({"code": code, "name": f"{name} ({code})"})
    return {"languages": langs}


@app.get("/v1/audio/info")
@app.get("/v1/models")
async def get_audio_info():
    """Whisper aktif model ve donanım bilgilerini döner."""
    model_name = os.path.basename(MODEL_SIZE) if isinstance(MODEL_SIZE, str) else str(MODEL_SIZE)
    return {
        "model": model_name,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "status": "ready" if model is not None else "loading",
        "data": [{"id": model_name, "object": "model"}]
    }



# =========================================================
# WEBSOCKET (Live Streaming STT)
# =========================================================
import io
from fastapi.responses import Response

last_debug_audio = np.array([], dtype=np.float32)

@app.get("/v1/audio/debug/last")
async def get_last_audio():
    global last_debug_audio
    if len(last_debug_audio) == 0:
        raise HTTPException(status_code=404, detail="No audio available")
    
    buf = io.BytesIO()
    int_audio = (last_debug_audio * 32767).astype(np.int16)
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(int_audio.tobytes())
    
    buf.seek(0)
    return Response(content=buf.read(), media_type="audio/wav")

@app.post("/v1/audio/debug/clear")
async def clear_debug_audio():
    global last_debug_audio
    last_debug_audio = np.array([], dtype=np.float32)
    return {"status": "cleared"}

@app.websocket("/v1/audio/transcriptions/stream")
async def websocket_endpoint(
    websocket: WebSocket,
    language: str | None = None,
    prompt: str | None = None,
):
    await websocket.accept()
    if not model:
        await websocket.send_json({"type": "error", "message": "Model not loaded"})
        await websocket.close()
        return

    lang_param = None if (not language or language.strip().lower() in ("auto", "none", "")) else language.strip().lower()
    accumulated_audio = np.array([], dtype=np.float32)
    silence_duration = 0.0
    last_transcribe_time = 0.0
    last_speech_time = time.perf_counter()
    speech_start_time = None
    noise_ema = None
    last_infer_duration = 0.0
    context_text = ""
    context_language = None
    
    smart_session = False
    session_id = None
    received_samples = 0
    timeline_samples = 0
    started_at = time.monotonic()
    base_prompt = prompt.strip() if (prompt and prompt.strip()) else ""

    try:
        while True:
            # Expecting binary audio data (PCM 16-bit 16kHz Mono)
            if smart_session:
                remaining = 640 - (time.monotonic() - started_at)
                if remaining <= 0:
                    raise ValueError("STT session lifetime limit reached")
                message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
            else:
                message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            finishing = False
            gapping = False
            segmenting = False
            finish_id = None
            data = message.get("bytes")
            if message.get("text"):
                try:
                    control = json.loads(message["text"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(control, dict):
                    continue
                if control.get("type") == "configure" and control.get("silence_gap") == 1:
                    if smart_session or received_samples:
                        raise ValueError("STT session already started")
                    smart_session = True
                    session_id = control.get("id")
                    await websocket.send_json({"type": "ready", "id": session_id, "silence_gap": 1, "segment_end": 1})
                    continue
                if control.get("type") == "gap":
                    samples = control.get("samples")
                    if (not smart_session or control.get("id") != session_id or
                            type(samples) is not int or samples <= 0 or samples > RATE * 600):
                        raise ValueError("Invalid STT silence gap")
                    timeline_samples += samples
                    if timeline_samples > RATE * 602:
                        raise ValueError("STT sample timeline limit reached")
                    gapping = True
                elif control.get("type") == "segment_end":
                    if not smart_session or control.get("id") != session_id:
                        raise ValueError("Wrong STT segment")
                    segmenting = True
                elif control.get("type") == "finish":
                    if smart_session and control.get("id") != session_id:
                        raise ValueError("Wrong STT turn")
                    finishing = True
                    finish_id = control.get("id")
                else:
                    continue
            if not data and not finishing and not gapping and not segmenting:
                continue
            if finishing and len(accumulated_audio) == 0:
                await websocket.send_json({"type": "finished", "id": finish_id})
                continue
            if (gapping or segmenting) and len(accumulated_audio) == 0:
                context_text = ""
                continue
            data = data or b""
            if len(data) % 2:
                raise ValueError("Incomplete PCM sample")
            received_samples += len(data) // 2
            timeline_samples += len(data) // 2
            if smart_session and (received_samples > RATE * 602 or timeline_samples > RATE * 602):
                raise ValueError("STT audio budget reached")
                
            # Convert bytes to float32 numpy array [-1.0, 1.0]
            # Assuming client sends int16 PCM
            pcm_data = np.frombuffer(data, dtype=np.int16)
            chunk_data = pcm_data.astype(np.float32) / 32768.0
            chunk_time = len(chunk_data) / RATE
            
            # Reset context if silent for > 10s
            current_time = time.perf_counter()
            if context_text and (current_time - last_speech_time > 10.0):
                context_text = ""
                
            # RMS and Silence detection (Gürültü zemini takibi - streaming.py ile aynı)
            rms = np.sqrt(np.mean(chunk_data ** 2)) if len(chunk_data) else 0.0
            if noise_ema is None:
                # İlk paket konuşma olabilir: onu gürültü tabanı yapıp
                # eşiği konuşmanın 2.5 katına çıkarma. Mevcut 0.001
                # alt eşiğinden başla; sessiz paketlerle tabanı öğren.
                noise_ema = min(float(rms), 0.001 / 2.5)
                
            current_threshold = max(0.001, noise_ema * 2.5)
            
            if rms < current_threshold:
                silence_duration += chunk_time
                noise_ema = noise_ema * 0.9 + rms * 0.1
            else:
                silence_duration = 0.0
                if speech_start_time is None:
                    speech_start_time = time.perf_counter() - chunk_time
                noise_ema = noise_ema * 0.999 + rms * 0.001
                
            accumulated_audio = np.concatenate((accumulated_audio, chunk_data))
            audio_duration = len(accumulated_audio) / RATE

            # In configured sessions Silero owns the boundary. Never finalize
            # a continuously spoken word just because 30 seconds elapsed.
            sentence_ended = finishing or gapping or segmenting or (not smart_session and silence_duration >= SILENCE_DURATION_TO_END and audio_duration >= MIN_AUDIO_TO_PROCESS)
            
            now = time.perf_counter()
            elapsed = now - last_transcribe_time
            dynamic_interval = max(MIN_TRANSCRIBE_INTERVAL, last_infer_duration * 1.5)
            
            should_transcribe = finishing or gapping or segmenting or (audio_duration >= MIN_AUDIO_TO_PROCESS and (sentence_ended or elapsed >= dynamic_interval))
            
            if not should_transcribe:
                continue
                
            last_transcribe_time = now
            active_beam_size = FINAL_BEAM_SIZE if sentence_ended else LIVE_BEAM_SIZE
            
            audio_to_process = accumulated_audio.copy()
            # Keep un-normalized copy for debug playback (sounds natural)
            raw_audio_for_debug = accumulated_audio.copy()
            max_amp = np.max(np.abs(audio_to_process))
            if max_amp > 0.005:
                audio_to_process = (audio_to_process * (0.35 / max_amp)).astype(np.float32)
                
            # Transcribe (streaming.py parametreleri ile birebir)
            start_infer_time = time.perf_counter()
            active_language, current_prompt = await asyncio.to_thread(
                select_transcription_prompt, model, audio_to_process, lang_param,
                base_prompt, "" if smart_session else context_text, context_language,
            )
            segments, info = await asyncio.to_thread(
                model.transcribe,
                audio_to_process,
                task="transcribe",
                beam_size=active_beam_size,
                language=active_language,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=400,
                    min_speech_duration_ms=150,
                    threshold=0.5
                ),
                no_speech_threshold=0.6,
                without_timestamps=True,
                condition_on_previous_text=False,
                initial_prompt=current_prompt,
                temperature=0.0,
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.0,
            )
            
            text = ""
            for segment in segments:
                if segment.no_speech_prob < 0.6:
                    text += segment.text
            text = text.strip()
            
            # Halüsinasyon Engelleyici (streaming.py ile birebir)
            if text:
                # 1. Aynı harfin 5 kereden fazla tekrarı (örn: ıııııııı -> ıı)
                text = re.sub(r'(.)\1{4,}', r'\1\1', text)
                
                # 2. Aynı kelimenin arka arkaya 3 kereden fazla tekrarı (örn: İyiyim. İyiyim. İyiyim. -> İyiyim.)
                text = re.sub(r'\b(\w+)(?:[\s.,]+\1\b){3,}', r'\1', text, flags=re.IGNORECASE)
                
                # 3. İmkansız konuşma hızı (Çok kısa seste çok fazla kelime uydurması)
                if len(text) > (audio_duration * 40):
                    text = ""
                    
                # 4. Yetersiz Net Konuşma Süresi
                net_speech_duration = max(0.0, audio_duration - silence_duration)
                if net_speech_duration < 0.4 and len(text) < 20:
                    text = ""
            
            last_infer_duration = time.perf_counter() - start_infer_time
            detected_lang = lang_param if lang_param else (getattr(info, "language", "unknown") if hasattr(info, "language") else "unknown")

            if text:
                if sentence_ended:
                    # ONLY cache raw debug audio when a sentence ends with real speech!
                    global last_debug_audio
                    last_debug_audio = raw_audio_for_debug.copy()

                    await websocket.send_json({
                        "type": "final",
                        "text": text,
                        "duration": last_infer_duration,
                        "language": detected_lang
                    })
                    if context_language != detected_lang:
                        context_text = ""
                    context_language = detected_lang
                    context_text += " " + text
                    if len(context_text) > 500:
                        context_text = context_text[-500:]
                    last_speech_time = time.perf_counter()
                else:
                    await websocket.send_json({
                        "type": "live",
                        "text": text,
                        "duration": last_infer_duration,
                        "language": detected_lang
                    })
            
            if sentence_ended and not text:
                await websocket.send_json({"type": "final", "text": "", "language": detected_lang})
            if sentence_ended:
                if smart_session:
                    context_text = ""
                accumulated_audio = np.array([], dtype=np.float32)
                silence_duration = 0.0
                last_transcribe_time = 0.0
                speech_start_time = None

            if finishing:
                await websocket.send_json({"type": "finished", "id": finish_id})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e) or "STT session timed out"})
            await websocket.close()
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    # Default port 9009
    port = int(os.getenv("STT_PORT", 9009))
    logger.info(f"Starting Orion STT on port {port}")
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
