import asyncio
import base64
import io
import wave
from contextlib import suppress
from collections.abc import Awaitable, Callable

from orion.worker.services.router import stream_tts


async def relay_tts(context, settings, text: str, should_stop: Callable[[], Awaitable[bool]], arrival_ms: Callable[[], int]):
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
        stream = stream_tts(text, settings, context.voice)
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
                audio, format=fmt, sample_rate=info.get("sample_rate"), text=text,
                arrival_ms=first_arrival, streamed=pcm_started, status=status,
            )

        try:
            async for chunk, info in stream:
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
