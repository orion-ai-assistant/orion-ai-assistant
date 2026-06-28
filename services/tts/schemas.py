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
    guidance_scale: Optional[float] = 2.0
    steps: Optional[int] = None
    stream: Optional[bool] = False
