import os
import pyaudio
from google import genai
from google.genai import types

SAMPLE_RATE = 24000
CHANNELS = 1
FORMAT = pyaudio.paInt16


def stream_orion_voice():
    YOUR_API_KEY = "AIzaSyDowq5HmNBGRj08Pstu38pHJw-43RBTmWc"
    client = genai.Client(api_key=YOUR_API_KEY)
    model_id = "gemini-2.5-flash-preview-tts"

    text_content = "Selam . Bu uzun bir cümle ve ben bu cümleyi bitirmeyi beklemeden, kelimeleri işledikçe sana ses paketleri halinde anında göndermeye başlıyorum.Özetle: Sadece metni sese çeviren standart TTS modelinde (AI Studio üzerinde) gerçek stream şu an sorunlu. Ama üstteki kodda olduğu gibi Live API (aio.live.connect) tünelini kullanırsan, AI Studio üzerinden kusursuz ve gecikmesiz bir ses akışı elde edebilirsin. Orion'un mimarisini bu Live API (WebSocket) altyapısına geçirmek ister misin?"

    generate_content_config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name="Zephyr"
                )
            )
        )
    )

    print(f"[{model_id}] üzerinden ses akışı (stream) başlatılıyor...\n")

    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, output=True)

    try:
        response_stream = client.models.generate_content_stream(
            model=model_id,
            contents=text_content,
            config=generate_content_config,
        )

        chunk_count = 0
        for chunk in response_stream:
            if not chunk.candidates:
                continue

            for candidate in chunk.candidates:
                if not candidate.content.parts:
                    continue

                for part in candidate.content.parts:
                    if not part.inline_data or not part.inline_data.data:
                        continue

                    chunk_count += 1
                    audio_data = part.inline_data.data
                    print(f"[{chunk_count}. Paket] {len(audio_data)} byte ses verisi geldi!")

                    try:
                        stream.write(audio_data)
                    except Exception as play_error:
                        print(f"Ses çalma hatası: {play_error}")

    except Exception as e:
        print(f"Bir hata oluştu: {e}")

    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


if __name__ == "__main__":
    stream_orion_voice()