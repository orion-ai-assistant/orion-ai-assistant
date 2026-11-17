"""The trash (docs/hafiza-tasarimi.md §11.2): removing a memory, a part or a piece moves its rows to the trash, it is
not deleted. A row in the trash keeps everything (text, vectors in every model, media link) and has an entry in
`orion_mem_trash` (`trash_id`: the removal it belongs to, the unit the trash lists, restores and deletes for good;
`deleted_at`). Moving rows in and out is one set-based insert / delete of those small entries, never an update of
`orion_memory` rows (that would write every row into every index again). Every read for the
page, the map, the counts and the chat model skips it (memory.LIVE). After the user's `memory_trash_days` (30 by
default) a periodic job deletes it for good; the user can restore it before, or delete it for good at once.

What one removal takes: the asked rows and their copies in every vector space (one content, one removal). An item's
whole vectors (the mean of its pieces) follow its pieces: while some pieces are live the whole is the mean of the live
ones; with none live it is in the trash with them (memory.refresh_wholes).

Deleting for good (purge) is memory.delete_rows on the entry's rows: the rows and their trigram/hash entries, every
model's vectors, the media link (the file goes with its last link), the whole vectors, and the neighbours of a deleted
text piece are embedded again; the delete confirmations' previews that named them are emptied."""
from __future__ import annotations

import asyncio
import logging

from orion.kernel import memory

logger = logging.getLogger(__name__)

PURGE_LOCK = 51730204  # One purge run at a time across Hub processes.
PURGE_EVERY = 3600  # Seconds between purge runs.
PURGE_START = 120  # Seconds after the Hub starts before the first run.
LIST_ROWS = 40  # Rows of one removal read for its card (counts come from SQL): a long text has thousands.
GROUP_KEY = "coalesce(group_id, 'r' || id::text)"
MG = "coalesce(m.group_id, 'r' || m.id::text)"  # The same keys for a query with a join (alias m).
MC = "coalesce(m.copied_from, m.id)"


async def trash_rows(conn, user_id: str, where: str, *args) -> dict:
    """Move this user's live rows matching `where` (SQL after the user's condition, parameters from $2) and their
    copies in other spaces to the trash, as one removal, inside the caller's transaction. {"trash_id", "rows":
    [{id, text, group_id, item_id, whole}]}; trash_id None when nothing matched."""
    trash_id = memory.new_group()
    n = len(args) + 2
    rows = [dict(r) for r in await conn.fetch(
        f"with hit as (select id, text, group_id, item_id, whole from orion_memory where user_id = $1 and {memory.LIVE} "
        f"and {memory.CONTENT_KEY} in (select {memory.CONTENT_KEY} from orion_memory where user_id = $1 "
        f"and {memory.LIVE} and ({where}))), "
        f"put as (insert into orion_mem_trash (memory_id, user_id, trash_id) select id, $1, ${n} from hit) "
        f"select * from hit", user_id, *args, trash_id)]
    if not rows:
        return {"trash_id": None, "rows": []}
    items = sorted({r["item_id"] for r in rows if r.get("item_id")})
    if items:
        await memory.refresh_wholes(conn, user_id, items)
    groups = sorted({r["group_id"] for r in rows if r.get("group_id")})
    if groups:  # A video still being embedded into a memory now wholly in the trash stops (it would come back alone).
        await conn.execute(
            "update orion_memory_media_jobs j set cancel = true where j.user_id = $1 and j.state = 'running' "
            f"and j.group_id = any($2::text[]) and not exists (select 1 from orion_memory m where m.user_id = $1 "
            f"and m.group_id = j.group_id and {memory.live('m')})", user_id, groups)
    return {"trash_id": trash_id, "rows": rows}


async def _trash(user_id: str, where: str, *args) -> dict:
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            return await trash_rows(conn, user_id, where, *args)
    finally:
        await conn.close()


def _answer(found: dict, asked: set[int] | None = None) -> dict:
    """{"trash_id", "removed": [{id, text}]} of the parts (not whole vectors), only the asked ids when given."""
    return {"trash_id": found["trash_id"],
            "removed": [{"id": r["id"], "text": r["text"]} for r in found["rows"]
                        if not r.get("whole") and (asked is None or r["id"] in asked)]}


async def trash_items(user_id: str, ids: list[int]) -> dict:
    """Remove these rows (parts) and their copies in other spaces."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return {"trash_id": None, "removed": []}
    return _answer(await _trash(user_id, "id = any($2::bigint[])", ids), set(ids))


async def trash_whole_items(user_id: str, item_ids: list[str]) -> dict:
    """Remove these items entirely (every piece, its whole vectors, every space)."""
    item_ids = [str(i)[:64] for i in item_ids if i and not str(i).startswith("r")][:100]
    if not item_ids:
        return {"trash_id": None, "removed": []}
    return _answer(await _trash(user_id, "item_id = any($2::text[])", item_ids))


async def trash_groups(user_id: str, groups: list[str]) -> dict:
    """Remove whole memories (every part, in every space)."""
    groups = [str(g)[:64] for g in groups][:200]
    if not groups:
        return {"trash_id": None, "removed": []}
    return _answer(await _trash(user_id, f"{GROUP_KEY} = any($2::text[])", groups))


async def trash(user_id: str, ids: list[int] = (), item_ids: list[str] = (), groups: list[str] = ()) -> dict:
    """One removal of rows, items and whole memories together (the page's delete): one trash entry, one undo.
    {"trash_id", "removed"}."""
    ids = [int(i) for i in ids][:500]
    item_ids = [str(i)[:64] for i in item_ids if i and not str(i).startswith("r")][:100]
    groups = [str(g)[:64] for g in groups][:200]
    if not (ids or item_ids or groups):
        return {"trash_id": None, "removed": []}
    found = await _trash(user_id, f"id = any($2::bigint[]) or item_id = any($3::text[]) or {GROUP_KEY} = any($4::text[])",
                         ids, item_ids, groups)
    return _answer(found)


async def restore(user_id: str, trash_ids: list[str]) -> dict:
    """Bring these removals back as they were (same ids, groups, vectors); the items' whole vectors follow.
    {"restored": [ids of parts], "groups": [...]}."""
    trash_ids = [str(t)[:64] for t in trash_ids][:200]
    if not trash_ids:
        return {"restored": [], "groups": []}
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            rows = [dict(r) for r in await conn.fetch(
                "with gone as (delete from orion_mem_trash where user_id = $1 and trash_id = any($2::text[]) "
                "returning memory_id) "
                f"select m.id, m.item_id, m.whole, {MG} "
                "as g from orion_memory m join gone on gone.memory_id = m.id", user_id, trash_ids)]
            items = sorted({r["item_id"] for r in rows if r.get("item_id")})
            if items:
                await memory.refresh_wholes(conn, user_id, items)
    finally:
        await conn.close()
    return {"restored": [r["id"] for r in rows if not r["whole"]], "groups": sorted({r["g"] for r in rows})}


async def purge(user_id: str, trash_ids: list[str] | None = None, settings=None) -> dict:
    """Delete these removals for good (None: the whole trash): see the module's doc. {"deleted": [ids of parts],
    "entries": n}."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        if trash_ids is None:
            trash_ids = [r["trash_id"] for r in await conn.fetch(
                "select distinct trash_id from orion_mem_trash where user_id = $1", user_id)]
        trash_ids = [str(t)[:64] for t in trash_ids if t]
        if not trash_ids:
            return {"deleted": [], "entries": 0}
        async with conn.transaction():
            ids = [r["id"] for r in await conn.fetch(
                "select m.id from orion_memory m join orion_mem_trash t on t.memory_id = m.id "
                "where t.user_id = $1 and t.trash_id = any($2::text[]) and not m.whole", user_id, trash_ids)]
            neighbours = await memory._neighbours_of(conn, user_id, ids) if ids else []
            rows = await memory.delete_rows(
                conn, user_id, "id in (select memory_id from orion_mem_trash where user_id = $1 "
                "and trash_id = any($2::text[]))", trash_ids)
            await conn.execute("delete from orion_mem_trash where user_id = $1 and trash_id = any($2::text[])",
                               user_id, trash_ids)
            gone = [r["id"] for r in rows]
            if gone:
                await _scrub_previews(conn, user_id, gone)
    finally:
        await conn.close()
    if neighbours:
        memory._schedule_reembed(user_id, neighbours, settings)
    return {"deleted": [r["id"] for r in rows if not r.get("whole")], "entries": len(trash_ids)}


async def _scrub_previews(conn, user_id: str, ids: list[int]) -> None:
    """Delete confirmations keep a label of what they named: those of deleted rows lose it (only the ids stay)."""
    if await conn.fetchval("select to_regclass('orion_memory_pending') is not null"):
        await conn.execute("update orion_memory_pending set preview = '[]' where user_id = $1 "
                           "and ids && $2::bigint[]", user_id, ids)


def days_of(settings) -> int:
    return max(1, int(getattr(settings, "memory_trash_days", 30) or 30))


async def list_trash(user_id: str, settings, limit: int = 50, offset: int = 0) -> dict:
    """The user's removals, newest first: {"entries": [{"trash_id", "deleted_at", "purge_at", "days_left", "scope"
    ("memory": the whole memory is in the trash; "part": its other parts are still there), "group_id", "card" (the
    removed rows as a memory card: kind, counts, title, text, media previews)}], "total", "days", "more"}."""
    from orion.kernel import memory_groups
    days = days_of(settings)
    space = await memory.current_space(settings)
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        heads = [dict(r) for r in await conn.fetch(
            f"select t.trash_id, min(t.deleted_at) as deleted_at, min({MG}) as g, "
            f"min(t.deleted_at) + make_interval(days => $2) as purge_at, "
            f"count(distinct {MC}) filter (where not m.whole) as parts "
            f"from orion_mem_trash t join orion_memory m on m.id = t.memory_id where t.user_id = $1 "
            f"group by t.trash_id order by min(t.deleted_at) desc, t.trash_id limit $3 offset $4",
            user_id, days, limit + 1, offset)]
        more = len(heads) > limit
        heads = heads[:limit]
        total = await conn.fetchval("select count(distinct t.trash_id) from orion_mem_trash t "
                                    "join orion_memory m on m.id = t.memory_id where t.user_id = $1", user_id)
        ids = [h["trash_id"] for h in heads]
        rows: dict[str, list[dict]] = {}
        columns = memory_groups._ROW.replace("id, text,", f"id, left(text, {memory_groups.CARD_TEXT}) as text,", 1)
        for r in await conn.fetch(
                f"select * from (select {columns}, tid as trash_id, row_number() over (partition by tid "
                f"order by whole, id) as k from (select m.*, t.trash_id as tid from orion_memory m "
                f"join orion_mem_trash t on t.memory_id = m.id where t.user_id = $1 and t.trash_id = any($2::text[])) x"
                f") y where k <= {LIST_ROWS}", user_id, ids):
            rows.setdefault(r["trash_id"], []).append(dict(r))
        live = {r["g"] for r in await conn.fetch(
            f"select distinct {GROUP_KEY} as g from orion_memory where user_id = $1 and {memory.LIVE} "
            f"and {GROUP_KEY} = any($2::text[])", user_id, [h["g"] for h in heads])}
        now = await conn.fetchval("select now()")
    finally:
        await conn.close()
    entries = []
    for h in heads:
        found = rows.get(h["trash_id"])
        if not found:
            continue
        card = memory_groups.card(h["g"], found, space, space)
        card["part_count"] = int(h["parts"] or card["part_count"])
        left = (h["purge_at"] - now).total_seconds() / 86400
        entries.append({"trash_id": h["trash_id"], "deleted_at": h["deleted_at"].isoformat(),
                        "purge_at": h["purge_at"].isoformat(), "days_left": max(0, int(-(-left // 1))),
                        "scope": "part" if h["g"] in live else "memory", "group_id": h["g"], "card": card})
    return {"entries": entries, "total": int(total or 0), "days": days, "more": more}


async def purge_expired(settings_of=None) -> int:
    """Delete for good every removal older than its user's trash days; how many removals went. One run at a time
    (an advisory lock: several Hub processes start this job)."""
    if settings_of is None:
        from orion.kernel.config import get_runtime_settings

        async def settings_of(user_id):
            return await get_runtime_settings(None, user_id)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        if not await conn.fetchval("select pg_try_advisory_lock($1)", PURGE_LOCK):
            return 0
        try:
            await conn.execute("delete from orion_mem_trash t where not exists "
                               "(select 1 from orion_memory m where m.id = t.memory_id)")  # Rows gone by another path.
            users = [r["user_id"] for r in await conn.fetch("select distinct user_id from orion_mem_trash")]
            due: dict[str, tuple[list[str], object]] = {}
            for user_id in users:
                settings = await settings_of(user_id)
                expired = [r["trash_id"] for r in await conn.fetch(
                    "select trash_id from orion_mem_trash where user_id = $1 group by trash_id "
                    "having min(deleted_at) < now() - make_interval(days => $2)", user_id, days_of(settings))]
                if expired:
                    due[user_id] = (expired, settings)
        finally:
            await conn.execute("select pg_advisory_unlock($1)", PURGE_LOCK)
    finally:
        await conn.close()
    count = 0
    for user_id, (expired, settings) in due.items():
        try:
            count += (await purge(user_id, expired, settings))["entries"]
        except Exception:
            logger.exception("Memory trash: removals of a user could not be deleted for good")
    if count:
        logger.info("Memory trash: %d removals older than their trash days deleted for good", count)
    return count


async def purge_loop() -> None:
    """The periodic job (started by the Hub): purge_expired every PURGE_EVERY seconds."""
    await asyncio.sleep(PURGE_START)
    while True:
        try:
            await purge_expired()
        except asyncio.CancelledError:
            raise
        except memory.MemoryUnavailable:
            pass  # No database or no pgvector: nothing to purge.
        except Exception:
            logger.exception("Memory trash: purge run failed")
        await asyncio.sleep(PURGE_EVERY)
