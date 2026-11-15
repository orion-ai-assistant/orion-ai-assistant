import asyncio
import base64
import io
import logging
import time
import wave
from contextlib import suppress
from collections.abc import Awaitable, Callable

from orion.worker.services.pcm_normalizer import PcmNormalizer
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
    # The same filtered conversion as a streamed part (no hiss folded in from above 12 kHz).
    return PcmNormalizer(rate, channels).feed(raw, final=True)


class PartialSpeech(Exception):
    """A later part of the speech failed after the first had already played.

    ``audio`` is the recording of what was spoken (status "failed"); the caller keeps it in the chat.
    """

    def __init__(self, error, audio):
        super().__init__(str(error))
        self.error = error
        self.audio = audio


async def relay_tts_batches(context, settings, should_stop: Callable[[], Awaitable[bool]],
                            arrival_ms: Callable[[], int], batches):
    """Relay one reply, part by part as ``batches`` yields them, and retain a playable recording."""
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
        failed = []  # the error of the part that could not be spoken (speech stops there)

        async def one_batch(batch, sentence_index):
            """Stream one sentence batch; whole PCM samples only, so a failed part never leaves half a sample."""
            stream = stream_tts(batch, settings, context.voice)
            buffered = bytearray()
            batch_info = None
            carry = b""
            asked = time.monotonic()
            first_audio = last_audio = None
            pieces = size = 0
            try:
                async for chunk, chunk_info in stream:
                    batch_info = chunk_info
                    last_audio = time.monotonic()
                    first_audio = first_audio or last_audio
                    pieces += 1
                    size += len(chunk)
                    if chunk_info["format"] != "pcm":
                        if chunk_info["format"] != "wav":
                            raise RuntimeError("Speech requires PCM or WAV sentence audio")
                        buffered.extend(chunk)
                        if len(buffered) > 24_000_000:
                            raise RuntimeError("TTS batch exceeds size limit")
                    else:
                        data = carry + chunk
                        even = len(data) & ~1
                        carry = data[even:]
                        if even:
                            yield data[:even], chunk_info, sentence_index
            finally:
                await stream.aclose()
            if batch_info and batch_info["format"] == "wav":
                pcm = await asyncio.to_thread(_batch_wav_pcm, buffered)
                wav_info = {"format": "pcm", "sample_rate": 24000, "channels": 1,
                            "bits_per_sample": 16, "encoding": "pcm_s16le"}
                for offset in range(0, len(pcm), 4096):
                    yield pcm[offset:offset + 4096], wav_info, sentence_index
            elif carry:
                raise RuntimeError("Incomplete PCM sample at speech batch boundary")
            if first_audio:
                # Shows whether the provider streams (pieces spread over time) or answers in one burst.
                logging.info("Speech part %s: %d chars, first audio after %d ms, %d pieces over %d ms, %d bytes",
                             sentence_index, len(batch), (first_audio - asked) * 1000, pieces,
                             (last_audio - first_audio) * 1000, size)

        async def audio_chunks():
            async for sentence_index, batch in batches:
                spoken = False
                heard = 0  # pieces of this part that already reached the listener
                try:
                    async for item in one_batch(batch, sentence_index):
                        heard += 1
                        yield item
                    spoken = True
                except Exception as error:
                    # One try per part: a failure means the service has a problem right now.
                    logging.warning("Speech part %s failed (%d pieces already played): %r",
                                    sentence_index, heard, error)
                    failed.append(error)
                if spoken or heard:
                    texts.append(batch)
                if pcm_started:
                    # Lets a playing device flush what it has, also after a part that failed half way.
                    await context.emit_audio_stream("audio_batch_done", {
                        "sentence_index": sentence_index,
                        "chunk_count": sequence, "total_bytes": total,
                    })
                if not spoken:
                    return  # The rest of the reply stays text; no later part is even asked for.

        stream = audio_chunks()
        status = "failed"
        stream_ended = False

        async def end_stream(status):
            """Tells the client the live audio is over, once. Sent before the recording is saved and
            published: that takes a while, and the client's stop button stays up until it hears this."""
            nonlocal stream_ended
            if pcm_started and not stream_ended:
                stream_ended = True
                await context.emit_audio_stream("audio_done", {
                    "status": status, "total_bytes": sum(map(len, relayed_chunks)), "chunk_count": sequence,
                })

        async def recording(status):
            await end_stream(status)
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
                audio, format=fmt, sample_rate=info.get("sample_rate"), text=" ".join(texts).replace("\n", " "),
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
                        # Counted before it is published, and published even if the user's stop cancels this
                        # task meanwhile: audio_done's totals must say what the client received (a chunk
                        # published but not counted made the client throw away the whole recording).
                        piece = pending[:aligned]
                        number = sequence
                        relayed_chunks.append(piece)
                        sequence += 1
                        pending = pending[aligned:]
                        await asyncio.shield(context.emit_audio_stream("audio_chunk", {
                            "chunk": base64.b64encode(piece).decode("ascii"),
                            "sequence": number,
                            "sentence_index": sentence_index,
                        }))
            if pending:
                raise RuntimeError("Incomplete PCM sample")
            if failed:
                # Every part that could be spoken was; what failed stays text.
                error = failed[-1]
                audio = await recording("failed")
                if audio:
                    raise PartialSpeech(error, audio) from error
                raise error
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
        except PartialSpeech:
            raise
        except Exception as error:
            if pcm_started:
                # What was already spoken stays playable and is kept in the chat.
                audio = await recording("failed")
                if audio:
                    raise PartialSpeech(error, audio) from error
            raise
        finally:
            await stream.aclose()
            await end_stream(status)

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
