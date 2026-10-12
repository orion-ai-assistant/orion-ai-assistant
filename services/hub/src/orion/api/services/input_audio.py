"""Canonicalize new input audio before storage; never retain source media."""
import asyncio
import base64
import binascii
import io
import wave

from fastapi import HTTPException
from orion.contracts.http import Attachment

MAX_BYTES = 20 * 1024 * 1024
MAX_SAMPLES = 16000 * 600
_slots = asyncio.Semaphore(2)


def _canonical(raw):
    try:
        with wave.open(io.BytesIO(raw), "rb") as source:
            frames = source.getnframes()
            return (source.getnchannels() == 1 and source.getsampwidth() == 2
                    and source.getframerate() == 16000 and source.getcomptype() == "NONE"
                    and 0 < frames <= MAX_SAMPLES and len(source.readframes(frames)) == frames * 2)
    except (wave.Error, EOFError):
        return False


def _convert(item):
    if item.isText or not item.mime_type.startswith("audio/"):
        return item
    encoded = item.data.partition(",")[2]
    if len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError("Ses eki en fazla 20 MB olabilir.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Ses eki geçerli base64 değil.") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Ses eki boş veya 20 MB sınırını aşıyor.")
    if not _canonical(raw):
        import av
        output = io.BytesIO()
        count = 0
        # In-memory decoding only. Disallow network/file references embedded
        # in a playlist masquerading as an audio attachment.
        with av.open(io.BytesIO(raw), options={"protocol_whitelist": "pipe", "probesize": "5242880"}) as source:
            if not source.streams.audio:
                raise ValueError("Dosyada çözümlenebilir ses yok.")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            with wave.open(output, "wb") as destination:
                destination.setnchannels(1)
                destination.setsampwidth(2)
                destination.setframerate(16000)
                def append(frame):
                    nonlocal count
                    count += frame.samples
                    if count > MAX_SAMPLES:
                        raise ValueError("Yüklenen ses en fazla 10 dakika olabilir.")
                    destination.writeframesraw(bytes(frame.planes[0])[:frame.samples * 2])
                for frame in source.decode(source.streams.audio[0]):
                    for converted in resampler.resample(frame):
                        append(converted)
                for converted in resampler.resample(None):
                    append(converted)
        if not count:
            raise ValueError("Ses kaydı boş.")
        raw = output.getvalue()
    name = item.name.replace("\\", "/").split("/")[-1]
    name = (name.rsplit(".", 1)[0] if "." in name else name) or "ses"
    return item.model_copy(update={
        "name": name + ".wav", "mime_type": "audio/wav", "isText": False,
        "size": len(raw), "data": "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii"),
    })


async def normalize_input_audio(item: Attachment) -> Attachment:
    if item.isText or not item.mime_type.startswith("audio/"):
        return item
    async with _slots:
        try:
            return await asyncio.to_thread(_convert, item)
        except Exception as exc:
            # Decoder errors must not silently keep the original attachment.
            raise HTTPException(422, "Ses 16 kHz mono WAV’a çevrilemedi: " + str(exc)) from exc
