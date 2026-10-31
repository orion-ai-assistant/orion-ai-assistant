import asyncio
import base64
import io
import logging
import time
import wave
import struct
from contextlib import suppress
from collections.abc import Awaitable, Callable

from orion.worker.services.router import stream_tts

# A speech part that fails is asked for once more after this pause; two parts in a row that fail
# even then mean the service is down, and the rest of the reply stays text.
PART_ATTEMPTS = 2
RETRY_DELAY = 1.0
MAX_FAILED_IN_A_ROW = 2


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


class PartialSpeech(Exception):
    """A later part of the speech failed after the first had already played.

    ``audio`` is the recording of what was spoken (status "failed"); the caller keeps it in the chat.
    """

    def __init__(self, error, audio):
        super().__init__(str(error))
        self.error = error
        self.audio = audio


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
        failed = []  # errors of the parts that could not be spoken even after a second try

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
                    if adaptive and chunk_info["format"] != "pcm":
                        if chunk_info["format"] != "wav":
                            raise RuntimeError("Early speech requires PCM or WAV sentence audio")
                        buffered.extend(chunk)
                        if len(buffered) > 24_000_000:
                            raise RuntimeError("TTS batch exceeds size limit")
                    elif adaptive:
                        data = carry + chunk
                        even = len(data) & ~1
                        carry = data[even:]
                        if even:
                            yield data[:even], chunk_info, sentence_index
                    else:
                        yield chunk, chunk_info, sentence_index
            finally:
                await stream.aclose()
            if adaptive and batch_info and batch_info["format"] == "wav":
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
            failed_in_a_row = 0
            async for sentence_index, batch in batches:
                spoken = False
                hopeless = False
                heard = 0  # pieces of this part that already reached the listener
                for attempt in range(1, PART_ATTEMPTS + 1 if adaptive else 2):
                    try:
                        async for item in one_batch(batch, sentence_index):
                            heard += 1
                            yield item
                        spoken = True
                        break
                    except Exception as error:
                        if not adaptive:
                            raise
                        logging.warning("Speech part %s failed (attempt %d of %d, %d pieces already played): %r",
                                        sentence_index, attempt, PART_ATTEMPTS, heard, error)
                        # A quota or access error will not clear within a second; do not ask again.
                        hopeless = getattr(error, "status", None) in (401, 403, 429)
                        # A part that was already partly heard is not asked for again: its start would play twice.
                        if heard or attempt == PART_ATTEMPTS or hopeless:
                            failed.append(error)
                            break
                        await asyncio.sleep(RETRY_DELAY)
                if spoken or heard:
                    texts.append(batch)
                if spoken:
                    failed_in_a_row = 0
                else:
                    failed_in_a_row += 1
                if adaptive and pcm_started:
                    # Lets a playing device flush what it has, also after a part that failed half way.
                    await context.emit_audio_stream("audio_batch_done", {
                        "sentence_index": sentence_index,
                        "chunk_count": sequence, "total_bytes": total,
                    })
                if not spoken and (hopeless or failed_in_a_row >= MAX_FAILED_IN_A_ROW):
                    return  # The service is not answering; the rest stays text.

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
            if adaptive and pcm_started:
                # What was already spoken stays playable and is kept in the chat.
                audio = await recording("failed")
                if audio:
                    raise PartialSpeech(error, audio) from error
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
