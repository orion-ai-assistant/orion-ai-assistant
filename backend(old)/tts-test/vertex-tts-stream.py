"""
Streaming TTS - ses chunk'ları geldikçe çalar
Kullanım: python tts.py
Çıkmak için: q
"""

import asyncio
import pyaudio
from google import genai
from google.genai import types

SAMPLE_RATE = 24000
CHANNELS    = 1
FORMAT      = pyaudio.paInt16
TTS_MODEL   = "gemini-2.5-flash-preview-tts" # "gemini-2.5-flash-tts" de var ama farkları ne anlamadım. sanırım preview olan daha iyi. mesela sonda göt falan dedyince onu da söylüyor. ( Read as 18 years old and sweet turkish: [laugh] ne ya fatih mal mısın fatih göt)
TTS_VOICE   = "Zephyr"

client = genai.Client(
    vertexai=True,
    project="orionai-assistant-2604",
    location="us-central1",
)

pya = pyaudio.PyAudio()


def speak_stream_sync(text: str):
    """Tamamen sync — thread içinde çalışır, iç içe to_thread yok."""
    stream = pya.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        output=True,
    )
    try:
        chunk_count = 0
        for chunk in client.models.generate_content_stream(
            model=TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=TTS_VOICE
                        )
                    )
                ),
            ),
        ):
            for candidate in chunk.candidates:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        chunk_count += 1
                        print(f"[{chunk_count}] ses paketi geldi ({len(part.inline_data.data)} byte)")
                        stream.write(part.inline_data.data)
    finally:
        stream.stop_stream()
        stream.close()


async def main():
    print("Streaming TTS hazır. Çıkmak için 'q' yaz.\n")
    while True:
        text = await asyncio.to_thread(input, "Metin > ")
        if text.strip().lower() == "q":
            break
        if text.strip():
            await asyncio.to_thread(speak_stream_sync, text)


if __name__ == "__main__":
    asyncio.run(main())
