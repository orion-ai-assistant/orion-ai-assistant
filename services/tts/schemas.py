from pydantic import BaseModel
from typing import Optional

class SpeechRequest(BaseModel):
    model: str
    input: str
    voice: Optional[str] = ""
    response_format: Optional[str] = "wav"
    speed: Optional[float] = 1.0
    language: Optional[str] = "Auto"
    seed: Optional[int] = -1
    
    # Advanced Parameters
    instructions: Optional[str] = None
    guidance_scale: Optional[float] = None  # Not sent: the engine's own (OmniVoice 2.0, VoxCPM2 3.0)
    steps: Optional[int] = None
    creativity: Optional[float] = None  # The Router's "temperature" (0-2) when it means guidance: VoxCPM2 maps 0-2 to 2-4
    temperature: Optional[float] = None  # VoxCPM2 only: randomness of the generation (server default when not sent)
    stream: Optional[bool] = False
