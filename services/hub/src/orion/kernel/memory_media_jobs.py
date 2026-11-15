"""Videos are embedded in the background: decoding and a request per segment take from seconds to minutes, and the
page and the chat must not wait for them.

A job's state lives in `orion_memory_media_jobs` (the PC's and the phone's API servers are separate processes; the
page may ask either). The process that started the job runs it as an asyncio task, with the decoding in a thread,
and stores every row of the video at the end in one transaction: a job that stops halfway leaves nothing behind.
Until then the file is kept by a link of its own (owner 'memory', id "job:<id>"), dropped when the job ends; a job
whose heartbeat is older than STALE lost its process and shows as interrupted (removing it drops the file).

Per segment (memory_pieces: 30 s windows around 20 s cores) one request with the window's frames (as many as the
context takes at the measured cost of one frame: one probe request) and, when the model hears, one with the
window's sound. Both tracks get a whole-video vector."""
from __future__ import annotations

import asyncio
import logging

from orion.kernel import memory
from orion.kernel import memory_pieces as mp

logger = logging.getLogger(__name__)

STALE = 90
KEEP_FINISHED = 600  # Seconds a finished job is still listed (the page shows how it ended).
_tasks: set[asyncio.Task] = set()
_COLUMNS = "id, group_id, item_id, name, kind, state, done, total, message, cancel, started_at, finished_at"


def _status(row) -> dict:
    state = row["state"]
    if state == "running" and float(row["idle"]) > STALE:
        state = "interrupted"
    return {"id": row["id"], "group_id": row["group_id"], "name": row["name"], "kind": row["kind"], "state": state,
            "done": row["done"], "total": row["total"], "message": row["message"],
            "started_at": row["started_at"].isoformat(),
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None}


async def jobs(user_id: str) -> list[dict]:
    """This user's running jobs and those that ended in the last KEEP_FINISHED seconds, newest first."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        rows = await conn.fetch(
            f"select {_COLUMNS}, extract(epoch from now() - updated_at) as idle from orion_memory_media_jobs "
            f"where user_id = $1 and (state = 'running' or finished_at > now() - interval '{KEEP_FINISHED} seconds') "
            "order by started_at desc limit 20", user_id)
        return [_status(r) for r in rows]
    finally:
        await conn.close()


async def start(user_id: str, group: str, item: dict, raw: bytes, settings, held: bool = False) -> dict:
    """Keep the video and start embedding it into the memory `group`; returns the job. `item` as memory.insert_item
    takes it (media_id is the file's sha256). `held`: the file is in the media store already (a chat's).
    ValueError (Turkish) for a file that is not a usable video."""
    import hashlib
    from orion.kernel.media_store import hold_media, link_media
    info = await asyncio.to_thread(mp.video_info, raw)
    if info["seconds"] > mp.MAX_VIDEO_SECONDS:
        raise ValueError(f"Video çok uzun: en fazla {mp.MAX_VIDEO_SECONDS // 60} dakika olabilir.")
    target, space, local = await memory.embedding_target(settings)
    support = memory.media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
    if not support["video"]:
        raise ValueError(memory.file_refusal("video", support))
    job_id = memory.new_group()
    media_id = item["media_id"]
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            if not held:
                await hold_media(conn, media_id, item["mime"], raw)
            await link_media(media_id, "memory", f"job:{job_id}", conn=conn)
            await conn.execute(
                "insert into orion_memory_media_jobs (id, user_id, group_id, item_id, name, kind, state) "
                "values ($1, $2, $3, $4, $5, 'video', 'running')", job_id, user_id, group, item["item_id"],
                item.get("name") or "")
    finally:
        await conn.close()
    if media_id != hashlib.sha256(raw).hexdigest():
        logger.warning("Memory video %s: media id is not the file's sha256", media_id[:12])
    task = asyncio.create_task(run(job_id, user_id, group, item, raw, info, target, space, support, settings),
                               name=f"memory-video-{job_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": job_id, "group_id": group, "name": item.get("name") or "", "kind": "video", "state": "running",
            "done": 0, "total": 0, "message": None}


async def cancel(user_id: str, job_id: str) -> bool:
    """Stop a running job at its next segment; a job whose process is gone ends here (its file goes)."""
    from orion.kernel.media_store import unlink_media
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        row = await conn.fetchrow(
            "select id, state, extract(epoch from now() - updated_at) as idle, group_id, item_id from "
            "orion_memory_media_jobs where id = $1 and user_id = $2", job_id, user_id)
        if row is None:
            return False
        if row["state"] == "running" and float(row["idle"]) <= STALE:
            await conn.execute("update orion_memory_media_jobs set cancel = true where id = $1", job_id)
            return True
        media = await conn.fetch("select media_id from orion_media_links where owner_kind = 'memory' "
                                 "and owner_id = $1", f"job:{job_id}")
        async with conn.transaction():
            for m in media:
                await unlink_media(m["media_id"], "memory", f"job:{job_id}", conn=conn)
            await conn.execute("update orion_memory_media_jobs set state = case when state = 'running' then "
                               "'cancelled' else state end, finished_at = coalesce(finished_at, now()), "
                               "updated_at = now() - interval '1 day' where id = $1", job_id)
        return True
    finally:
        await conn.close()


async def frame_cost(frame: bytes, target) -> int | None:
    """Tokens one frame takes with the active model (the server's usage of a one-frame request), None when the
    server does not say."""
    from orion.worker.services.router import generate_embeddings_usage, image_part
    try:
        _, tokens = await generate_embeddings_usage([image_part(frame, "image/jpeg")], target)
        return tokens
    except Exception as exc:
        logger.info("Memory video: frame cost unknown (%s)", exc)
        return None


def plan(raw: bytes, info: dict, context: int | None) -> dict:
    """Decode what a video's segments need (in a thread): frame times on one grid for all windows, the frames, and
    the sound as PCM."""
    seconds = info["seconds"]
    times = mp.frame_times(seconds or mp.MAX_VIDEO_SECONDS, 1.0 / mp.VIDEO_MAX_FPS)
    found, frames, last = mp.video_frames(raw, times)
    if not seconds or last > seconds:
        seconds = max(seconds, last)
    pcm = mp.decode_pcm(raw) if info["audio"] else b""
    return {"seconds": seconds, "times": found, "frames": frames, "pcm": pcm}


async def run(job_id: str, user_id: str, group: str, item: dict, raw: bytes, info: dict, target, space: str,
              support: dict, settings=None) -> None:
    from orion.kernel.media_store import unlink_media
    from orion.worker.services.router import audio_part, image_part
    conn = await memory._connect()
    state, message = "done", None
    try:
        await memory.ensure_memory_table(conn)
        decoded = await asyncio.to_thread(plan, raw, info, memory.embedding_context())
        if not decoded["frames"]:
            raise ValueError("Videodan kare çıkarılamadı.")
        seconds = decoded["seconds"]
        segments = mp.spans(seconds, mp.VIDEO_WINDOW)
        hear = bool(support.get("audio")) and len(decoded["pcm"]) >= mp.SAMPLE_RATE // 5
        cost = await frame_cost(decoded["frames"][0], target)
        count = mp.frames_per_window(memory.embedding_context(), cost)
        await conn.execute("update orion_memory_media_jobs set total = $2, updated_at = now() where id = $1",
                           job_id, len(segments))
        name = memory.media_name(item)
        base = f"Video {memory.clock(seconds)}" + (f" ({name})" if name else "")
        tail = f" — {item['text']}" if item.get("text") else ""
        pieces = []
        for index, span in enumerate(segments):
            times = memory.span_label(span.start, span.end) if len(segments) > 1 else ""
            frames = mp.window_frames(decoded["times"], decoded["frames"], span, count)
            vector = await memory._embed_media([image_part(f, "image/jpeg") for f in frames], target)
            pieces.append({"text": f"{base} {times} görüntü".replace("  ", " ") + tail, "vector": vector,
                           "span_start": span.start, "span_end": span.end, "track": "frames", "seconds": seconds})
            if hear:
                wav = mp.wav_bytes(mp.pcm_slice(decoded["pcm"], span.window_start, span.window_end))
                pieces.append({"text": f"{base} {times} ses".replace("  ", " ") + tail,
                               "vector": await memory._embed_media([audio_part(wav, "wav")], target),
                               "span_start": span.start, "span_end": span.end, "track": "audio", "seconds": seconds})
            cancelled = await conn.fetchval("update orion_memory_media_jobs set done = $2, updated_at = now() "
                                            "where id = $1 returning cancel", job_id, index + 1)
            if cancelled:
                state = "cancelled"
                break
        if state == "done":
            if settings is not None and await memory.current_space(settings) not in (None, space):
                raise memory.MemoryUnavailable("Etkin embedding modeli değişti; video kaydedilmedi.")
            async with conn.transaction():
                stored = await memory.insert_item(conn, user_id, space, group, item, pieces)
            message = f"{stored} vektör"
    except asyncio.CancelledError:
        state = "interrupted"
        raise
    except (memory.MemoryUnavailable, ValueError) as exc:
        state, message = "failed", str(exc)
    except Exception as exc:
        logger.exception("Memory video job %s failed", job_id)
        state, message = "failed", "Embedding modeli bu videoyu işleyemedi."
    finally:
        try:
            if state != "interrupted":
                await unlink_media(item["media_id"], "memory", f"job:{job_id}", conn=conn)
            await conn.execute(
                "update orion_memory_media_jobs set state = $2, message = $3, updated_at = now(), finished_at = now() "
                "where id = $1", job_id, state, message)
        finally:
            await conn.close()
    logger.info("Memory video job %s (%s): %s %s", job_id, item.get("name") or "", state, message or "")
