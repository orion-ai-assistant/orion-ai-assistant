"""How a long memory item (a text, an audio file, a video) is cut into pieces, and what is embedded for each.

Every piece owns a *core* (the cores tile the item without overlap: what the page shows and the times it names)
and is embedded with a *window*: its core plus some context on each side, so a thought or a scene that crosses a
cut is still seen whole by one vector. Neighbouring windows overlap by about a third (CONTEXT_SHARE on each side).
An item of more than one piece also gets one *whole* vector: the normalized mean of its piece vectors (each core
counted once), so a question about the item as a whole finds it even when no single piece says it all.

Measured with llama.cpp b12378 (docs/bilesenler.md, "Video ve uzun kayıtlar"): a video frame of at most 448 px
costs EmbeddingGemma 2 ~79 tokens and Jina v5 ~277; several images in one request give one vector close to the
mean of the single ones (cos 0.94-0.99); audio is 25 tokens/s (Gemma; Jina takes no audio); frames and audio in one
request give a vector that is mostly the frames (cos 0.98 to the frames alone, 0.73 to the audio alone), so a video
segment's sound gets its own vector. No ffmpeg on an Orion PC: llama.cpp's own video input fails ("Failed to load
image or audio file"), so the Hub decodes video itself with PyAV."""
from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass

CONTEXT_SHARE = 1 / 6  # Context on each side, as a share of a window: windows overlap by a third.

# Text: a core of ~300 tokens embedded with ~75 tokens of each neighbour (450-token windows, a third shared).
TEXT_CORE_TOKENS = 300
TEXT_CONTEXT_TOKENS = 75

# Audio: 25 tokens/s (EmbeddingGemma 2). 60 s windows fit the 2048 context Orion starts the server with.
AUDIO_TOKENS_PER_SECOND = 25
AUDIO_WINDOW_MAX = 60.0
AUDIO_WINDOW_MIN = 10.0

# Video: 30 s windows (20 s cores, 5 s context each side); frames of at most 448 px (larger frames cost more
# tokens, smaller ones no fewer), at most one a second, as many as the context takes (frames_per_window).
VIDEO_WINDOW = 30.0
VIDEO_FRAME_SIZE = 448
VIDEO_MAX_FPS = 1.0
VIDEO_MAX_FRAMES = 1800  # Frames decoded for one video at most (a longer one is sampled more sparsely).
MAX_VIDEO_SECONDS = 30 * 60
TOKEN_MARGIN = 64  # Start/end tokens and the request's own text around the media.
FALLBACK_FRAME_TOKENS = 300  # A frame's cost when the server does not say (more than any model measured).
SAMPLE_RATE = 16000


@dataclass
class Span:
    """A piece of a timed item: its core [start, end) and the window [window_start, window_end) it is embedded with."""
    start: float
    end: float
    window_start: float
    window_end: float


def spans(total: float, window: float) -> list[Span]:
    """Cores tiling [0, total) and their windows. An item no longer than a window is one piece; a tail core shorter
    than a second joins the core before it."""
    if total <= window:
        return [Span(0.0, total, 0.0, total)]
    context = window * CONTEXT_SHARE
    core = window - 2 * context
    found, start = [], 0.0
    while start < total - 1e-6:
        end = min(total, start + core)
        if total - end < 1.0:
            end = total
        found.append(Span(start, end, max(0.0, start - context), min(total, end + context)))
        start = end
    return found


def audio_window(context_tokens: int | None) -> float:
    """The longest audio window the embedding server's context takes (25 tokens/s), at most a minute."""
    if not context_tokens:
        return AUDIO_WINDOW_MAX
    seconds = (int(context_tokens) - TOKEN_MARGIN) / AUDIO_TOKENS_PER_SECOND
    return max(AUDIO_WINDOW_MIN, min(AUDIO_WINDOW_MAX, math.floor(seconds)))


def frames_per_window(context_tokens: int | None, frame_tokens: int | None, window: float = VIDEO_WINDOW) -> int:
    """How many frames one window's request can carry: what the context holds at the measured cost of one frame,
    at most one a second, at least one."""
    cost = frame_tokens or FALLBACK_FRAME_TOKENS
    fit = (int(context_tokens or 2048) - TOKEN_MARGIN) // max(1, int(cost))
    return max(1, min(int(window * VIDEO_MAX_FPS), fit))


def frame_times(total: float, step: float) -> list[float]:
    """Times to take a frame at: every `step` seconds from the middle of the first step (not the black first frame),
    thinned so a long video stays within VIDEO_MAX_FRAMES."""
    step = max(step, total / VIDEO_MAX_FRAMES, 1.0 / VIDEO_MAX_FPS)
    count = max(1, int(total / step))
    return [min(total, (k + 0.5) * step) for k in range(count)]


def pick(items: list, count: int) -> list:
    """`count` items spread evenly over `items` (all of them when there are not more)."""
    if len(items) <= count:
        return list(items)
    return [items[int((k + 0.5) * len(items) / count)] for k in range(count)]


def window_frames(times: list[float], frames: list[bytes], span: Span, count: int) -> list[bytes]:
    inside = [frame for t, frame in zip(times, frames) if span.window_start <= t < span.window_end or
              (t == span.window_end == times[-1])]
    if not inside:  # A window between two sparse samples: the nearest frame.
        middle = (span.window_start + span.window_end) / 2
        inside = [min(zip(times, frames), key=lambda tf: abs(tf[0] - middle))[1]]
    return pick(inside, count)


def mean_vector(vectors: list[list[float]]) -> list[float]:
    """The normalized mean of unit vectors: the whole item's vector."""
    size = len(vectors[0])
    total = [0.0] * size
    for vector in vectors:
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        for i, v in enumerate(vector):
            total[i] += v / norm
    norm = math.sqrt(sum(v * v for v in total)) or 1.0
    return [v / norm for v in total]


# --- Text -----------------------------------------------------------------------------------------------------------

def _head(text: str, tokens: int) -> str:
    from orion.worker.services.router import _cut_by_tokens
    return text[:_cut_by_tokens(text, tokens, (". ", "\n", " "))].strip()


def _tail(text: str, tokens: int) -> str:
    from orion.worker.services.router import EMBED_TOKENS_PER_CHAR, estimate_tokens
    if estimate_tokens(text) <= tokens:
        return text.strip()
    start = max(0, len(text) - int(tokens / EMBED_TOKENS_PER_CHAR))
    while start < len(text) and estimate_tokens(text[start:]) > tokens:
        start += max(1, (len(text) - start) // 10)
    space = text.find(" ", start)
    return text[space + 1 if 0 <= space < start + 40 else start:].strip()


def text_pieces(text: str, core_tokens: int = TEXT_CORE_TOKENS,
                context_tokens: int = TEXT_CONTEXT_TOKENS) -> list[tuple[str, str]]:
    """(core, window) of each piece of a text: cores are whole paragraphs packed to ~core_tokens (chunk_text), the
    window adds the end of the previous core and the start of the next one."""
    from orion.worker.services.router import chunk_text
    cores = chunk_text(text, core_tokens) or ([text.strip()] if text.strip() else [])
    pieces = []
    for index, core in enumerate(cores):
        before = _tail(cores[index - 1], context_tokens) if index else ""
        after = _head(cores[index + 1], context_tokens) if index + 1 < len(cores) else ""
        pieces.append((core, "\n\n".join(part for part in (before, core, after) if part)))
    return pieces


# --- Audio ----------------------------------------------------------------------------------------------------------

def decode_pcm(raw: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """The first audio track of anything PyAV reads, as 16-bit mono PCM at `rate`."""
    import av
    pcm = bytearray()
    with av.open(io.BytesIO(raw)) as source:
        if not source.streams.audio:
            return b""
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

        def take(frames):
            for frame in frames:
                pcm.extend(bytes(frame.planes[0])[:frame.samples * 2])
        for frame in source.decode(audio=0):
            take(resampler.resample(frame))
        take(resampler.resample(None))
    return bytes(pcm)


def wav_bytes(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return out.getvalue()


def pcm_slice(pcm: bytes, start: float, end: float, rate: int = SAMPLE_RATE) -> bytes:
    first = int(start * rate) * 2
    return pcm[first:max(first, int(end * rate) * 2)]


# --- Video ----------------------------------------------------------------------------------------------------------

def video_info(raw: bytes) -> dict:
    """{"seconds", "width", "height", "audio"} of a video PyAV reads; ValueError when there is no picture."""
    import av
    try:
        source = av.open(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("Video okunamadı.") from exc
    try:
        with source:
            if not source.streams.video:
                raise ValueError("Bu dosyada görüntü yok.")
            stream = source.streams.video[0]
            seconds = None
            if stream.duration is not None and stream.time_base is not None:
                seconds = float(stream.duration * stream.time_base)
            if not seconds and source.duration:
                seconds = source.duration / 1_000_000
            return {"seconds": float(seconds or 0.0), "width": stream.codec_context.width,
                    "height": stream.codec_context.height, "audio": bool(source.streams.audio)}
    except Exception as exc:
        if str(exc) == "Bu dosyada görüntü yok.":
            raise
        raise ValueError("Video okunamadı.") from exc


def _jpeg(frame, size: int) -> bytes:
    image = frame.to_image()
    image.thumbnail((size, size))
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()


def video_frames(raw: bytes, times: list[float], size: int = VIDEO_FRAME_SIZE) -> tuple[list[float], list[bytes], float]:
    """A JPEG frame (at most `size` px) at or right after each of `times` (sorted), decoding the video once.
    Returns (the times found, their frames, the last frame's time: the real length when the header had none)."""
    import av
    found_times, frames, last = [], [], 0.0
    wanted = list(times)
    with av.open(io.BytesIO(raw)) as source:
        stream = source.streams.video[0]
        stream.thread_type = "AUTO"
        previous = None
        for frame in source.decode(video=0):
            if frame.time is None:
                continue
            last = float(frame.time)
            while wanted and wanted[0] <= last:
                found_times.append(wanted.pop(0))
                frames.append(_jpeg(frame, size))
            previous = frame
            if not wanted:
                break
        if wanted and previous is not None:  # Times past the last frame (a header longer than the picture).
            found_times.append(last)
            frames.append(_jpeg(previous, size))
    return found_times, frames, last


def frame_at(raw: bytes, at: float, size: int = 640) -> bytes:
    """One JPEG frame at `at` seconds (a thumbnail): seek to the keyframe before it and decode up to it."""
    import av
    with av.open(io.BytesIO(raw)) as source:
        stream = source.streams.video[0]
        if at > 0 and stream.time_base:
            try:
                source.seek(int(at / stream.time_base), stream=stream, backward=True)
            except Exception:
                source.seek(0)
        chosen = None
        for frame in source.decode(video=0):
            chosen = frame
            if frame.time is None or frame.time >= at:
                break
        if chosen is None:
            raise ValueError("Videoda kare yok.")
        return _jpeg(chosen, size)
