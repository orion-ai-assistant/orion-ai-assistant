"""Deleting memory on the chat model's request waits for the user: `delete_memory` only records what it would delete
(a pending confirmation) and the chat shows it as a card; the user's "Sil" press (confirm) moves it to the trash
(memory_trash: restorable for the user's trash days; `trash_id` names the removal), "Vazgeç" (decline) keeps
everything. The model never deletes by itself, and it can ask for the outcome with the confirmation id."""
from __future__ import annotations

import json

from orion.kernel import memory, memory_groups

EXPIRES_SECONDS = 3600  # A confirmation nobody answered is void after this long.
LABEL = 120


async def _ensure(conn) -> None:
    # `trash_id` came with the trash; memory._schema_3 adds it to a table made before.
    await conn.execute(
        "create table if not exists orion_memory_pending (token text primary key, user_id text not null, "
        "chat_id text, ids bigint[] not null, whole boolean not null default false, state text not null default "
        "'pending', preview text not null default '[]', deleted bigint[], created_at timestamptz not null default now(), "
        "resolved_at timestamptz, trash_id text)")


async def create(user_id: str, chat_id: str | None, ids: list[int], whole: bool) -> dict | None:
    """Record a delete the user must confirm; None when none of `ids` is this user's (or all are in the trash).
    {"token", "will_delete": [{"id", "label", "kind"}], "parts", "whole_memory"}."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        await _ensure(conn)
        rows = [dict(r) for r in await conn.fetch(
            "select id, text, kind, group_id, source_chat_title from orion_memory where user_id = $1 "
            f"and id = any($2::bigint[]) and not whole and {memory.LIVE} order by id", user_id, [int(i) for i in ids])]
        if not rows:
            return None
        groups = sorted({memory.group_key(r) for r in rows})
        parts = len(rows)
        if whole:
            real = [g for g in groups if not g.startswith("r")]
            loose = [int(g[1:]) for g in groups if g.startswith("r") and g[1:].isdigit()]
            parts = await conn.fetchval(
                f"select count(*) from orion_memory where user_id = $1 and not whole and {memory.LIVE} and "
                "(group_id = any($2::text[]) or (group_id is null and id = any($3::bigint[])))", user_id, real, loose)
        preview = [{"id": r["id"], "kind": r["kind"],
                    "label": " ".join((r["text"] or r["source_chat_title"] or "").split())[:LABEL]} for r in rows]
        token = memory.new_group()
        await conn.execute(
            "insert into orion_memory_pending (token, user_id, chat_id, ids, whole, preview) values ($1, $2, $3, $4, $5, $6)",
            token, user_id, chat_id, [r["id"] for r in rows], bool(whole), json.dumps(preview, ensure_ascii=False))
        return {"token": token, "will_delete": preview, "parts": int(parts or 0), "whole_memory": bool(whole)}
    finally:
        await conn.close()


async def status(user_id: str, token: str) -> dict | None:
    """{"state": pending | deleted | declined | expired, "will_delete", "deleted", "trash_id", "trash"} of this
    user's confirmation. "deleted" means moved to the trash: `trash_id` restores it (memory_trash.restore), and
    `trash` says where it is now: "in" (the trash), "restored" or "gone" (deleted for good)."""
    conn = await memory._connect()
    try:
        await _ensure(conn)
        row = await conn.fetchrow(
            "select state, preview, deleted, whole, trash_id, created_at < now() - $3 * interval '1 second' as old "
            "from orion_memory_pending where token = $1 and user_id = $2", token, user_id, EXPIRES_SECONDS)
        where = None
        if row is not None and row["trash_id"]:
            now = await conn.fetchval(  # Any of its parts live: restored; else in the trash; else gone.
                "select bool_or(t.memory_id is null) from orion_memory m left join orion_mem_trash t "
                "on t.memory_id = m.id where m.user_id = $1 and m.id = any($2::bigint[])",
                user_id, list(row["deleted"] or []))
            where = "gone" if now is None else ("restored" if now else "in")
    finally:
        await conn.close()
    if row is None:
        return None
    state = "expired" if row["state"] == "pending" and row["old"] else row["state"]
    return {"state": state, "will_delete": json.loads(row["preview"]), "whole_memory": row["whole"],
            "deleted": list(row["deleted"] or []), "trash_id": row["trash_id"], "trash": where}


async def resolve(user_id: str, token: str, accept: bool) -> dict | None:
    """The user's answer. Accepting moves the parts (or their whole memories) to the trash exactly once, as one
    removal; declining keeps everything. An answer to an already answered (or expired) confirmation changes nothing
    and returns its state."""
    from orion.kernel import memory_trash
    conn = await memory._connect()
    try:
        await _ensure(conn)
        row = await conn.fetchrow(
            "update orion_memory_pending set state = $3, resolved_at = now() where token = $1 and user_id = $2 "
            "and state = 'pending' and created_at >= now() - $4 * interval '1 second' returning ids, whole",
            token, user_id, "claimed" if accept else "declined", EXPIRES_SECONDS)
    finally:
        await conn.close()
    if row is None:
        return await status(user_id, token)
    if accept:
        if row["whole"]:
            conn = await memory._connect()
            try:
                groups = [r["g"] for r in await conn.fetch(
                    f"select distinct {memory_groups.GROUP_KEY} as g from orion_memory where user_id = $1 "
                    "and id = any($2::bigint[])", user_id, list(row["ids"]))]
            finally:
                await conn.close()
            removed = await memory_trash.trash_groups(user_id, groups)
        else:
            removed = await memory_trash.trash_items(user_id, list(row["ids"]))
        conn = await memory._connect()
        try:
            await conn.execute("update orion_memory_pending set state = 'deleted', deleted = $3, trash_id = $4 "
                               "where token = $1 and user_id = $2", token, user_id,
                               [r["id"] for r in removed["removed"]], removed["trash_id"])
        finally:
            await conn.close()
    return await status(user_id, token)
