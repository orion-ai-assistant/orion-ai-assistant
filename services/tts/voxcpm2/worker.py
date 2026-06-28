import os
import torch
import voxcpm
import soundfile as sf
import io
import argparse
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

# Log yapılandırması
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voxcpm-worker")

app = FastAPI(title="Orion VoxCPM Worker")

# Global model değişkeni
model = None
MODEL_INFO = "Not Loaded"

class SpeechRequest(BaseModel):
    input: str
    voice: Optional[str] = "" # Control Instruction veya ses tipi
    model: Optional[str] = "voxcpm2"
    response_format: Optional[str] = "wav"
    speed: Optional[float] = 1.0

@app.post("/v1/audio/speech")
async def text_to_speech(request: SpeechRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    try:
        logger.info(f"İstek alındı: {request.input[:50]}...")
        
        # VoxCPM üretimi
        # request.voice kısmını 'control_instruction' olarak kullanıyoruz
        wav, _, _ = model.tts_model.generate(
            target_text=request.input,
            control_instruction=request.voice,
            cfg_value=2.0,
            inference_timesteps=10
        )
        
        audio_data = wav.detach().cpu().numpy().flatten()
        sample_rate = model.tts_model.sample_rate
        
        buffer = io.BytesIO()
        sf.write(buffer, audio_data, sample_rate, format='WAV')
        buffer.seek(0)
        
        return StreamingResponse(buffer, media_type="audio/wav")
        
    except Exception as e:
        logger.error(f"Hata oluştu: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok", "model": MODEL_INFO}

def start_worker():
    global model, MODEL_INFO
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", type=str, default=os.getenv("MODEL_FILE", "voxcpm2"), help="Model path or ID")
    parser.add_argument("--port", type=int, default=8000, help="API Port")
    parser.add_argument("--vae-device", type=str, default=os.getenv("VAE_DEVICE", None), help="VAE Device")
    args = parser.parse_args()

    # Model yolunu belirle
    MODEL_PATH = args.model_id
    if not MODEL_PATH.startswith("/") and not os.path.exists(MODEL_PATH):
        # Eğer relatif yol ise ve direkt bulunamazsa /app/models/ altında ara
        potential_path = f"/app/models/{MODEL_PATH}"
        if os.path.exists(potential_path):
            MODEL_PATH = potential_path

    MODEL_INFO = MODEL_PATH
    
    if os.path.exists(MODEL_PATH):
        logger.info(f"Yerel model yükleniyor: {MODEL_PATH}...")
        model = voxcpm.VoxCPM.from_pretrained(MODEL_PATH, optimize=False, vae_device=args.vae_device, load_denoiser=False)
    else:
        logger.warning(f"Yerel model bulunamadı ({MODEL_PATH}), HuggingFace üzerinden deneniyor: openbmb/VoxCPM2")
        model = voxcpm.VoxCPM.from_pretrained("openbmb/VoxCPM2", optimize=False, vae_device=args.vae_device, load_denoiser=False)
        MODEL_INFO = "openbmb/VoxCPM2"

    logger.info("Model başarıyla yüklendi. API başlatılıyor...")
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    start_worker()
