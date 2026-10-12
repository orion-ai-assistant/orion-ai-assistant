import asyncio
import base64
import io
import wave
import struct
from contextlib import suppress
from collections.abc import Awaitable, Callable

from orion.worker.services.router import stream_tts


def _batch_wav_pcm(data):
    """Normalize a completed sentence WAV to the existing playback PCM contract."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.getnframes()
        if width != 2 or channels not in (1, 2) or rate <= 0:
            raise RuntimeError("TTS batch requires mono/stereo PCM16 WAV")
        if frames * 24000 // rate * 2 > 24_000_000:
            raise RuntimeError("TTS batch exceeds size limit")
        raw = wav.readframes(frames)
    if len(raw) != frames * channels * 2 or not frames:
        raise RuntimeError("TTS returned an incomplete or empty WAV")
    if channels == 1 and rate == 24000:
        return raw
    samples = struct.unpack(f"<{frames * channels}h", raw)
    mono = samples if channels == 1 else [
        (samples[i] + samples[i + 1]) / 2 for i in range(0, len(samples), 2)
    ]
    count = frames * 24000 // rate
    result = bytearray(count * 2)
    for i in range(count):
        source = i * rate / 24000
        left = min(int(source), frames - 1)
        right = min(left + 1, frames - 1)
        value = round(mono[left] + (mono[right] - mono[left]) * (source - left))
        struct.pack_into("<h", result, i * 2, max(-32768, min(32767, value)))
    return bytes(result)


async def relay_tts(context, settings, text: str, should_stop: Callable[[], Awaitable[bool]], arrival_ms: Callable[[], int]):
    async def single():
        yield 0, text
    return await _relay(context, settings, should_stop, arrival_ms, single(), adaptive=False)


async def relay_tts_batches(context, settings, should_stop, arrival_ms, batches):
    return await _relay(context, settings, should_stop, arrival_ms, batches, adaptive=True)


async def _relay(context, settings, should_stop, arrival_ms, batches, adaptive):
    """Relay one utterance and retain a playable recording for every session."""
    user_stopped = False

    async def synthesize():
        chunks = []
        relayed_chunks = []
        total = 0
        sequence = 0
        pcm_started = False
        info = None
        first_arrival = None
        pending = b""
        last_sentence = None
        texts = []
        async def audio_chunks():
            async for sentence_index, batch in batches:
                texts.append(batch)
                stream = stream_tts(batch, settings, context.voice)
                buffered = bytearray()
                batch_info = None
                try:
                    async for chunk, info in stream:
                        batch_info = info
                        if adaptive and info["format"] != "pcm":
                            if info["format"] != "wav":
                                raise RuntimeError("Early speech requires PCM or WAV sentence audio")
                            buffered.extend(chunk)
                            if len(buffered) > 24_000_000:
                                raise RuntimeError("TTS batch exceeds size limit")
                        else:
                            yield chunk, info, sentence_index
                finally:
                    await stream.aclose()
                if adaptive and batch_info and batch_info["format"] == "wav":
                    pcm = await asyncio.to_thread(_batch_wav_pcm, buffered)
                    info = {"format": "pcm", "sample_rate": 24000, "channels": 1,
                            "bits_per_sample": 16, "encoding": "pcm_s16le"}
                    for offset in range(0, len(pcm), 4096):
                        yield pcm[offset:offset + 4096], info, sentence_index
                if pending:
                    raise RuntimeError("Incomplete PCM sample at speech batch boundary")
                if adaptive:
                    await context.emit_audio_stream("audio_batch_done", {
                        "sentence_index": sentence_index,
                        "chunk_count": sequence, "total_bytes": total,
                    })
        stream = audio_chunks()
        status = "failed"
        async def recording(status):
            if not info or not total:
                return None
            audio = b"".join(relayed_chunks if pcm_started else chunks)
            fmt = info["format"]
            if fmt == "pcm":
                audio = audio[:len(audio) & ~1]
                if not audio:
                    return None
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(info["channels"])
                    wav.setsampwidth(2)
                    wav.setframerate(info["sample_rate"])
                    wav.writeframes(audio)
                audio, fmt = output.getvalue(), "wav"
            return await context.emit_audio(
                audio, format=fmt, sample_rate=info.get("sample_rate"), text=" ".join(texts),
                arrival_ms=first_arrival, streamed=pcm_started, status=status,
            )

        try:
            async for chunk, info, sentence_index in stream:
                if last_sentence != sentence_index and pending:
                    raise RuntimeError("Incomplete PCM sample at speech batch boundary")
                last_sentence = sentence_index
                if first_arrival is None:
                    first_arrival = arrival_ms()
                total += len(chunk)
                if total > 24_000_000:
                    raise RuntimeError("TTS recording exceeds size limit")
                chunks.append(chunk)
                if info["format"] == "pcm":
                    if not pcm_started:
                        await context.emit_audio_stream("audio_start", {**info, "arrival_ms": first_arrival})
                        pcm_started = True
                    pending += chunk
                    aligned = len(pending) & ~1
                    if aligned:
                        await context.emit_audio_stream("audio_chunk", {
                            "chunk": base64.b64encode(pending[:aligned]).decode("ascii"),
                            "sequence": sequence,
                            **({"sentence_index": sentence_index} if adaptive else {}),
                        })
                        relayed_chunks.append(pending[:aligned])
                        sequence += 1
                        pending = pending[aligned:]
            if pending:
                raise RuntimeError("Incomplete PCM sample")
            if not info or not total:
                raise RuntimeError("TTS returned no audio")
            status = "completed"
            return await recording(status)
        except asyncio.CancelledError:
            status = "stopped"
            if user_stopped:
                # Only samples already relayed are retained. Never synthesize new text.
                return await recording(status) if pcm_started else None
            raise
        except Exception:
            if adaptive and pcm_started:
                await recording("failed")
            raise
        finally:
            await stream.aclose()
            if pcm_started:
                await context.emit_audio_stream("audio_done", {
                    "status": status, "total_bytes": sum(map(len, relayed_chunks)), "chunk_count": sequence,
                })

    async def watch_stop():
        while not await should_stop():
            await asyncio.sleep(0.05)

    if await should_stop():
        return None
    synthesis = asyncio.create_task(synthesize())
    watcher = asyncio.create_task(watch_stop())
    try:
        done, _ = await asyncio.wait([synthesis, watcher], return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher  # Redis errors must also cancel synthesis.
            user_stopped = True
            synthesis.cancel()
            return await synthesis
        return await synthesis
    finally:
        for task in (synthesis, watcher):
            if not task.done():
                task.cancel()
        for task in (synthesis, watcher):
            with suppress(asyncio.CancelledError):
                await task
