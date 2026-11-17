"""Moving memories to the active embedding model: rows of another vector space are embedded again into the active
one (text from its text with the model's document prefix, images and audio from the stored file), as copies that
keep their group, date and source and name their original in `copied_from`. What the active model cannot take and
what already has a copy there are skipped and counted.

One job per user at a time. Its state lives in `orion_memory_jobs` (the PC's and the phone's API servers are
separate processes, so the page may ask either one); the process that started it runs it as an asyncio task,
updates a heartbeat after every row and stops at the next row once `cancel` is set. A job whose heartbeat is
older than STALE seconds lost its process (the Hub stopped) and shows as interrupted; starting again resumes,
since rows with a copy are skipped."""
from __future__ import annotations

import asyncio
import json
import logging

from orion.kernel import memory
from orion.kernel.memory import CONTENT_KEY

logger = logging.getLogger(__name__)

STALE = memory.JOB_STALE_SECONDS
SPACE_CHECK_EVERY = 25  # Rows between checks that the active model is still the one the job embeds with.
_tasks: set[asyncio.Task] = set()

COPY_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title, kind, media_id, "
    "media_mime, media_seconds, group_id, copied_from, source_chat_id, source_message_id, created_at, item_id, whole, "
    "span_start, span_end, track, caption, item_order, source_message_last, folded, caption_folded, lossless) "
    "values ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, "
    "$22, $23, $24, $25, $26) returning id")
_HAS_COPY = ("exists (select 1 from orion_memory c where c.user_id = r.user_id and c.vector_space = $3 "
             "and coalesce(c.copied_from, c.id) = coalesce(r.copied_from, r.id))")


async def job_status(conn, user_id: str) -> dict | None:
    row = await conn.fetchrow("select *, extract(epoch from now() - updated_at) as idle from orion_memory_jobs "
                              "where user_id = $1", user_id)
    if row is None:
        return None
    state = row["state"]
    if state == "running" and float(row["idle"]) > STALE:
        state = "interrupted"
    unsupported = row["unsupported"]
    return {"state": state, "source_space": row["source_space"], "target_space": row["target_space"],
            "total": row["total"], "done": row["done"], "copied": row["copied"], "existing": row["existing"],
            "unsupported": json.loads(unsupported) if isinstance(unsupported, str) else dict(unsupported or {}),
            "failed": row["failed"], "message": row["message"], "cancel": row["cancel"],
            "started_at": row["started_at"].isoformat(),
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None}


async def status(user_id: str) -> dict | None:
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        return await job_status(conn, user_id)
    finally:
        await conn.close()


async def start(user_id: str, source_space: str, settings) -> dict:
    """Start moving `source_space`'s memories into the active model's space; returns the job's status. ValueError
    with a Turkish sentence when there is nothing to start (same space, a job already running)."""
    target, space, local = await memory.embedding_target(settings)
    if not source_space or source_space == space:
        raise ValueError("Bu kayıtlar zaten etkin modelde.")
    support = memory.media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        claimed = await conn.fetchval(
            "insert into orion_memory_jobs (user_id, source_space, target_space, state) values ($1, $2, $3, 'running') "
            "on conflict (user_id) do update set source_space = excluded.source_space, "
            "target_space = excluded.target_space, state = 'running', total = 0, done = 0, copied = 0, existing = 0, "
            "unsupported = '{}'::jsonb, failed = 0, message = null, cancel = false, started_at = now(), "
            "updated_at = now(), finished_at = null "
            f"where orion_memory_jobs.state <> 'running' or orion_memory_jobs.updated_at < now() - interval '{STALE} seconds' "
            "returning true", user_id, source_space, space)
        if not claimed:
            raise ValueError("Bir aktarım zaten sürüyor.")
    finally:
        await conn.close()
    task = asyncio.create_task(run(user_id, source_space, space, target, local, support, settings),
                               name=f"memory-move-{user_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return await status(user_id)


async def cancel(user_id: str) -> dict | None:
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        # A job whose process is gone cannot see the flag: it ends here.
        await conn.execute(
            "update orion_memory_jobs set cancel = true, state = case when updated_at < now() - interval "
            f"'{STALE} seconds' then 'cancelled' else state end, finished_at = case when updated_at < now() - "
            f"interval '{STALE} seconds' then now() else finished_at end where user_id = $1 and state = 'running'",
            user_id)
        return await job_status(conn, user_id)
    finally:
        await conn.close()


async def _read_media(conn, media_id: str) -> tuple[str, bytes] | None:
    row = await conn.fetchrow("select mime_type, data from orion_media where id = $1", media_id)
    return (row["mime_type"], bytes(row["data"])) if row else None


def _window(row: dict, window: float, total: float) -> tuple[float, float]:
    """The window a timed piece was embedded with: its core and CONTEXT_SHARE of `window` on each side."""
    from orion.kernel.memory_pieces import CONTEXT_SHARE
    start, end = float(row["span_start"]), float(row["span_end"])
    if end - start >= total - 0.01:
        return 0.0, total
    context = window * CONTEXT_SHARE
    return max(0.0, start - context), min(total, end + context)


def _decode(raw: bytes, rows: list[dict], context: int | None) -> dict:
    """What re-embedding one audio or video file's rows needs, decoded once (in a thread)."""
    from orion.kernel import memory_pieces as mp
    from orion.kernel import memory_media_jobs
    if rows[0]["kind"] == "video":
        return memory_media_jobs.plan(raw, mp.video_info(raw), context)
    if all(r.get("span_start") is None for r in rows):
        return {"pieces": memory.audio_pieces(raw)[0]}
    pcm = mp.decode_pcm(raw)
    return {"pcm": pcm, "seconds": len(pcm) / (mp.SAMPLE_RATE * 2)}


async def _media_vectors(conn, rows: list[dict], target) -> list[list[float] | None]:
    """A vector for each row of one image, audio or video file (its pieces, in row order), embedded as it was
    stored: an audio piece with its window, a video segment with its window's frames or sound."""
    from orion.kernel import memory_pieces as mp
    from orion.worker.services.router import audio_part, image_part
    stored = await _read_media(conn, rows[0]["media_id"])
    if stored is None:
        return [None] * len(rows)
    mime, raw = stored
    if rows[0]["kind"] == "image":
        vector = await memory._embed_media([image_part(raw, rows[0].get("media_mime") or mime)], target)
        return [vector] + [None] * (len(rows) - 1)
    context = memory.embedding_context()
    decoded = await asyncio.to_thread(_decode, raw, rows, context)
    if "pieces" in decoded:  # Audio stored before overlapping pieces: one row per <=60 s, in order.
        pieces = decoded["pieces"]
        return [await memory._embed_media([audio_part(pieces[i], "wav")], target) if i < len(pieces) else None
                for i in range(len(rows))]
    total = float(decoded["seconds"])
    vectors = []
    if rows[0]["kind"] == "audio":
        size = mp.audio_window(context)
        for r in rows:
            start, end = _window(r, size, total) if r.get("span_start") is not None else (0.0, total)
            wav = mp.wav_bytes(mp.pcm_slice(decoded["pcm"], start, end))
            vectors.append(await memory._embed_media([audio_part(wav, "wav")], target))
        return vectors
    count = None
    for r in rows:
        start, end = _window(r, mp.VIDEO_WINDOW, total)
        span = mp.Span(float(r["span_start"]), float(r["span_end"]), start, end)
        if r.get("track") == "both":
            from orion.kernel.memory_media_jobs import frame_cost
            pcm = mp.pcm_slice(decoded["pcm"], start, end)
            fit = mp.combined_frames(context, await frame_cost(decoded["frames"][0], target), end - start)
            if not fit or len(pcm) < mp.SAMPLE_RATE // 5:
                vectors.append(None)
                continue
            frames = mp.window_frames(decoded["times"], decoded["frames"], span, fit)
            vectors.append(await memory._embed_media([image_part(f, "image/jpeg") for f in frames]
                                                     + [audio_part(mp.wav_bytes(pcm), "wav")], target))
            continue
        if r.get("track") == "audio":
            pcm = mp.pcm_slice(decoded["pcm"], start, end)
            vectors.append(await memory._embed_media([audio_part(mp.wav_bytes(pcm), "wav")], target)
                           if len(pcm) >= mp.SAMPLE_RATE // 5 else None)
            continue
        if count is None:
            from orion.kernel.memory_media_jobs import frame_cost
            count = mp.frames_per_window(context, await frame_cost(decoded["frames"][0], target))
        frames = mp.window_frames(decoded["times"], decoded["frames"], span, count)
        vectors.append(await memory._embed_media([image_part(f, "image/jpeg") for f in frames], target))
    return vectors


def text_windows(rows: list[dict]) -> dict[int, str]:
    """The window each text piece of an item was embedded with: its core and the end/start of its neighbours."""
    from orion.kernel import memory_pieces as mp
    windows, items = {}, {}
    for r in rows:
        if r["kind"] == "text" and r.get("item_id") and not r.get("whole"):
            items.setdefault(r["item_id"], []).append(r)
    for members in items.values():
        members.sort(key=memory.order_key)
        for r, window in zip(members, mp.windows_of([m["text"] for m in members])):
            windows[r["id"]] = window
    return windows


def _work(rows: list[dict]) -> list[list[dict]]:
    """Units of work in save order: one text row, or all rows of one file in one group (audio pieces, a video's
    segments). Whole vectors are not copied: they are made again from the copied pieces (rebuild_wholes)."""
    units, media = [], {}
    for r in rows:
        if r.get("whole"):
            continue
        if r["kind"] == "text" or not r.get("media_id"):
            units.append([r])
            continue
        key = (r.get("group_id"), r["media_id"])
        if key not in media:
            media[key] = []
            units.append(media[key])
        media[key].append(r)
    return units


async def rebuild_wholes(conn, user_id: str, space: str, items: list[str]) -> int:
    """The whole vectors of `items` in `space`, from their pieces there, for the tracks that have one in another
    space and none here yet (memory.refresh_wholes keeps them right after). Returns how many were made."""
    from orion.kernel.media_store import link_media
    if not items:
        return 0
    rows = [dict(r) for r in await conn.fetch(
        "select id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
        "media_seconds, group_id, copied_from, source_chat_id, source_message_id, item_id, whole, track, caption, "
        "item_order from orion_memory where user_id = $1 and item_id = any($2::text[])",
        user_id, items)]
    made = 0
    for item in items:
        mine = [r for r in rows if r["item_id"] == item]
        for track in {r["track"] for r in mine}:
            here = [r for r in mine if r["track"] == track and r["vector_space"] == space]
            source = next((r for r in mine if r["track"] == track and r["whole"] and r["vector_space"] != space), None)
            pieces = [r for r in here if not r["whole"]]
            if source is None or any(r["whole"] for r in here) or len(pieces) < 2:
                continue
            # The mean is made by the database (no embedding is read out): normalized, as mean_vector makes it.
            vector = await conn.fetchval(
                "select l2_normalize(avg(embedding))::text from orion_memory where user_id = $1 and item_id = $2 "
                "and vector_space = $3 and track is not distinct from $4 and not whole", user_id, item, space, track)
            new_id = await conn.fetchval(
                COPY_SQL, user_id, source["text"], space, vector, source["source_kind"],
                source["source_chat_title"], source["kind"], source["media_id"], source["media_mime"],
                source["media_seconds"], source["group_id"], source["copied_from"] or source["id"],
                source["source_chat_id"], source["source_message_id"], source["created_at"], item, True, None, None,
                track, source["caption"], source["item_order"], source.get("source_message_last"), memory.fold(memory.row_match_text(source)),
                None if source["caption"] is None else memory.fold(source["caption"]), False)
            if source["media_id"]:
                await link_media(source["media_id"], "memory", str(new_id), conn=conn)
            made += 1
    return made


async def run(user_id: str, source: str, space: str, target, local: bool, support: dict, settings) -> None:
    from orion.kernel.media_store import link_media
    conn = await memory._connect()
    counts = {"done": 0, "copied": 0, "failed": 0}
    unsupported: dict[str, int] = {}
    state, message = "done", None
    items: set[str] = set()
    try:
        await memory.ensure_memory_table(conn)
        columns = ("id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
                   "media_seconds, group_id, copied_from, source_chat_id, source_message_id, item_id, whole, "
                   "span_start, span_end, track, caption, item_order, lossless")
        rows = [dict(r) for r in await conn.fetch(
            f"select {columns} from orion_memory r where user_id = $1 and vector_space = $2 and not {_HAS_COPY} "
            "order by id", user_id, source, space)]
        # The windows come from every piece of each text item (also those already copied: a window holds its
        # neighbours), so a move rebuilds exactly the windows the pieces were first embedded with.
        item_ids = sorted({r["item_id"] for r in rows if r["kind"] == "text" and r.get("item_id")})
        whole_items = [dict(r) for r in await conn.fetch(
            f"select {columns} from orion_memory where user_id = $1 and vector_space = $2 and kind = 'text' "
            "and not whole and item_id = any($3::text[])", user_id, source, item_ids)] if item_ids else []
        windows = text_windows(whole_items)
        existing = await conn.fetchval(
            f"select count(distinct {CONTENT_KEY}) from orion_memory r where user_id = $1 and vector_space = $2 "
            f"and {_HAS_COPY} and not whole", user_id, source, space)
        work = _work(rows)
        await conn.execute("update orion_memory_jobs set total = $2, existing = $3, updated_at = now() "
                           "where user_id = $1", user_id, sum(len(unit) for unit in work), int(existing or 0))
        seen: set[int] = set()  # One copy per content even when the source space holds two of it.
        for step, unit in enumerate(work):
            if step and step % SPACE_CHECK_EVERY == 0 and await memory.current_space(settings) != space:
                state, message = "failed", "Etkin embedding modeli değişti; aktarım durdu."
                break
            kind = unit[0]["kind"]
            fresh = [r for r in unit if (r["copied_from"] or r["id"]) not in seen]
            seen.update(r["copied_from"] or r["id"] for r in unit)
            if kind != "text" and not support.get(kind):
                unsupported[kind] = unsupported.get(kind, 0) + len(fresh)
            elif fresh:
                try:
                    if kind == "text":
                        text = windows.get(fresh[0]["id"], fresh[0]["text"])
                        vectors = [(fresh[0], v) for v in (await memory._embed(text, target, "document", local))[:1]]
                    else:
                        vectors = [(r, v) for r, v in zip(fresh, await _media_vectors(conn, fresh, target)) if v]
                    async with conn.transaction():
                        for r, vector in vectors:
                            group = r["group_id"]
                            if not group:
                                group = memory.new_group()
                                await conn.execute("update orion_memory set group_id = $2 where id = $1", r["id"], group)
                            new_id = await conn.fetchval(
                                COPY_SQL, user_id, r["text"], space, memory._vector(vector), r["source_kind"],
                                r["source_chat_title"], r["kind"], r["media_id"], r["media_mime"], r["media_seconds"],
                                group, r["copied_from"] or r["id"], r["source_chat_id"], r["source_message_id"],
                                r["created_at"], r["item_id"], False, r["span_start"], r["span_end"], r["track"],
                                r["caption"], r["item_order"], r.get("source_message_last"), memory.fold(memory.row_match_text(r)),
                                None if r["caption"] is None else memory.fold(r["caption"]), bool(r.get("lossless")))
                            if r["media_id"]:
                                await link_media(r["media_id"], "memory", str(new_id), conn=conn)
                            if r["item_id"]:
                                items.add(r["item_id"])
                    copied = len({id(r) for r, _ in vectors})
                    counts["copied"] += copied
                    counts["failed"] += len(fresh) - copied
                except memory.MemoryUnavailable as exc:
                    state, message = "failed", str(exc)
                    break
                except Exception as exc:
                    logger.warning("Memory move: row %s could not be copied: %s", unit[0]["id"], exc)
                    counts["failed"] += len(fresh)
            counts["done"] += len(unit)
            cancelled = await conn.fetchval(
                "update orion_memory_jobs set done = $2, copied = $3, failed = $4, unsupported = $5::jsonb, "
                "updated_at = now() where user_id = $1 returning cancel", user_id, counts["done"], counts["copied"],
                counts["failed"], json.dumps(unsupported))
            if cancelled:
                state = "cancelled"
                break
    except asyncio.CancelledError:
        # The Hub is stopping: not done, and a new start goes on from here (copied rows are skipped).
        state = "interrupted"
        raise
    except Exception as exc:
        logger.exception("Memory move failed")
        state, message = "failed", f"Aktarım durdu: {exc}"
    finally:
        try:
            if items and state != "interrupted":
                try:
                    async with conn.transaction():
                        await rebuild_wholes(conn, user_id, space, sorted(items))
                except Exception as exc:
                    logger.warning("Memory move: whole vectors not rebuilt: %s", exc)
            await conn.execute(
                "update orion_memory_jobs set state = $2, message = $3, done = $4, copied = $5, failed = $6, "
                "unsupported = $7::jsonb, updated_at = now(), finished_at = now() where user_id = $1",
                user_id, state, message, counts["done"], counts["copied"], counts["failed"], json.dumps(unsupported))
        finally:
            await conn.close()
    logger.info("Memory move %s -> %s for %s: %s, %s, unsupported %s", source, space, user_id, state, counts, unsupported)
