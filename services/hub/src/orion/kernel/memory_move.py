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

STALE = 90
SPACE_CHECK_EVERY = 25  # Rows between checks that the active model is still the one the job embeds with.
_tasks: set[asyncio.Task] = set()

COPY_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title, kind, media_id, "
    "media_mime, media_seconds, group_id, copied_from, source_chat_id, source_message_id, created_at) "
    "values ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15) returning id")
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


async def _media_vectors(conn, rows: list[dict], target) -> list[list[float] | None]:
    """A vector for each row of one image or audio file (an audio row is one <=60 s piece, in row order)."""
    from orion.worker.services.router import audio_part, image_part
    stored = await _read_media(conn, rows[0]["media_id"])
    if stored is None:
        return [None] * len(rows)
    mime, raw = stored
    if rows[0]["kind"] == "image":
        vector = await memory._embed_media([image_part(raw, rows[0].get("media_mime") or mime)], target)
        return [vector] + [None] * (len(rows) - 1)
    pieces, _ = await asyncio.to_thread(memory.audio_pieces, raw)
    return [await memory._embed_media([audio_part(pieces[i], "wav")], target) if i < len(pieces) else None
            for i in range(len(rows))]


def _work(rows: list[dict]) -> list[list[dict]]:
    """Units of work in save order: one text row, or all rows of one file in one group (audio pieces)."""
    units, media = [], {}
    for r in rows:
        if r["kind"] == "text" or not r.get("media_id"):
            units.append([r])
            continue
        key = (r.get("group_id"), r["media_id"])
        if key not in media:
            media[key] = []
            units.append(media[key])
        media[key].append(r)
    return units


async def run(user_id: str, source: str, space: str, target, local: bool, support: dict, settings) -> None:
    from orion.kernel.media_store import link_media
    conn = await memory._connect()
    counts = {"done": 0, "copied": 0, "failed": 0}
    unsupported: dict[str, int] = {}
    state, message = "done", None
    try:
        await memory.ensure_memory_table(conn)
        columns = ("id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
                   "media_seconds, group_id, copied_from, source_chat_id, source_message_id")
        rows = [dict(r) for r in await conn.fetch(
            f"select {columns} from orion_memory r where user_id = $1 and vector_space = $2 and not {_HAS_COPY} "
            "order by id", user_id, source, space)]
        existing = await conn.fetchval(
            f"select count(distinct {CONTENT_KEY}) from orion_memory r where user_id = $1 and vector_space = $2 "
            f"and {_HAS_COPY}", user_id, source, space)
        await conn.execute("update orion_memory_jobs set total = $2, existing = $3, updated_at = now() "
                           "where user_id = $1", user_id, len(rows), int(existing or 0))
        seen: set[int] = set()  # One copy per content even when the source space holds two of it.
        for step, unit in enumerate(_work(rows)):
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
                        vectors = [(fresh[0], v) for v in await memory._embed(fresh[0]["text"], target, "document", local)]
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
                                r["created_at"])
                            if r["media_id"]:
                                await link_media(r["media_id"], "memory", str(new_id), conn=conn)
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
            await conn.execute(
                "update orion_memory_jobs set state = $2, message = $3, done = $4, copied = $5, failed = $6, "
                "unsupported = $7::jsonb, updated_at = now(), finished_at = now() where user_id = $1",
                user_id, state, message, counts["done"], counts["copied"], counts["failed"], json.dumps(unsupported))
        finally:
            await conn.close()
    logger.info("Memory move %s -> %s for %s: %s, %s, unsupported %s", source, space, user_id, state, counts, unsupported)
