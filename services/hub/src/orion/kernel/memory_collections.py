"""Collections (docs/hafiza-tasarimi.md §3, §13): every memory sits in one main collection (`orion_memory.collection_id`)
and may also be a member of projects/albums (`orion_mem_collection_item`). Behind the per-user setting `memory_v2`:
with it off nothing here runs and the memory behaves as before.

The first time a user's memory is used with `memory_v2` on, their collections are made and their existing memories
sorted, once (ensure_user): after a dump of the memory tables, the four built-in collections and the project
"Eski hafıza"; a memory whose first row came from a file goes to Belgeler, every other one to Eski hafıza. Group ids,
parts and vectors stay as they are. revert() undoes it (the new column emptied, the collections gone); the rows never
moved. Rows with no collection (saved before, or while the setting was off) are searched as before by a search over
the whole memory.

Source links (`orion_mem_source`): where a fact or an event came from (a chat and its first/last message, an item, or
the user typing it on the page). Nothing is written without one (§16); add_source / sources_of are the only way in."""
from __future__ import annotations

import logging

from orion.kernel import memory

logger = logging.getLogger(__name__)

BUILTIN = (("ben", "Ben"), ("yasadiklarim", "Yaşadıklarım"), ("sohbetler", "Sohbetler"), ("belgeler", "Belgeler"))
OLD_NAME = "Eski hafıza"
IN_NAMES = {"me": "ben", "experiences": "yasadiklarim", "chats": "sohbetler", "documents": "belgeler"}  # search_memory `in`
BATCH = 500  # Memories sorted per statement while a user's existing memory is sorted.
USER_LOCK = 51730203  # With the user's hash: one sorting of one user's memory at a time, across Hub processes.


def enabled(settings) -> bool:
    return bool(getattr(settings, "memory_v2", False))


async def _sorted(conn, user_id: str) -> bool:
    return bool(await conn.fetchval("select 1 from orion_mem_collection where user_id = $1 and id = 'belgeler'",
                                    user_id))


async def ensure_user(conn, user_id: str) -> None:
    """Make this user's collections and sort their existing memories into Belgeler / Eski hafıza, once. A
    primary-key lookup when it was done."""
    if await _sorted(conn, user_id):
        return
    await conn.execute("select pg_advisory_lock($1, hashtext($2))", USER_LOCK, user_id)
    try:
        if await _sorted(conn, user_id):
            return
        if await conn.fetchval("select exists (select 1 from orion_memory where user_id = $1)", user_id):
            from orion.kernel import memory_backup
            await memory_backup.backup("collections")
        old = "p_" + memory.new_group()
        # Each memory by its first row: a group key and where that row came from (one pass over the user's rows).
        firsts = await conn.fetch(
            "select distinct on (g) g, source_kind from (select coalesce(group_id, 'r' || id::text) as g, id, "
            "source_kind from orion_memory where user_id = $1 and collection_id is null) t order by g, id", user_id)
        async with conn.transaction():
            await conn.executemany(
                "insert into orion_mem_collection (user_id, id, kind, name) values ($1, $2, 'builtin', $3) "
                "on conflict do nothing", [(user_id, cid, name) for cid, name in BUILTIN])
            await conn.execute("insert into orion_mem_collection (user_id, id, kind, name) values ($1, $2, 'project', $3)",
                               user_id, old, OLD_NAME)
            for target in ("belgeler", old):
                keys = [r["g"] for r in firsts if (r["source_kind"] == "file") == (target == "belgeler")]
                for start in range(0, len(keys), BATCH):
                    part = keys[start:start + BATCH]
                    groups = [k for k in part if not k.startswith("r")]
                    loose = [int(k[1:]) for k in part if k.startswith("r") and k[1:].isdigit()]
                    await conn.execute(
                        "update orion_memory set collection_id = $2 where user_id = $1 and collection_id is null "
                        "and (group_id = any($3::text[]) or (group_id is null and id = any($4::bigint[])))",
                        user_id, target, groups, loose)
        logger.info("Memory: collections made for a user, %d memories sorted (%d to Belgeler)", len(firsts),
                    sum(1 for r in firsts if r["source_kind"] == "file"))
    finally:
        await conn.execute("select pg_advisory_unlock($1, hashtext($2))", USER_LOCK, user_id)


async def revert(user_id: str) -> dict:
    """Undo the sorting: the rows lose their collection, the collections and memberships go. Nothing else changes
    (the rows were never moved). {"rows", "collections"}."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            rows = await conn.execute("update orion_memory set collection_id = null where user_id = $1 "
                                      "and collection_id is not null", user_id)
            await conn.execute("delete from orion_mem_collection_item where user_id = $1", user_id)
            gone = await conn.execute("delete from orion_mem_collection where user_id = $1", user_id)
    finally:
        await conn.close()
    return {"rows": int(rows.split()[-1]), "collections": int(gone.split()[-1])}


async def list_collections(conn, user_id: str) -> list[dict]:
    """This user's collections: the built-ins in their order, then projects/albums by name. No counts (they would
    read every row of the user)."""
    rows = await conn.fetch("select id, kind, name from orion_mem_collection where user_id = $1 and deleted_at is null",
                            user_id)
    order = {cid: k for k, (cid, _) in enumerate(BUILTIN)}
    return [{"id": r["id"], "kind": r["kind"], "name": r["name"]}
            for r in sorted(rows, key=lambda r: (order.get(r["id"], len(order)), memory.fold(r["name"])))]


async def resolve(conn, user_id: str, names: list[str]) -> list[str]:
    """Collection ids for the names a search asked for: 'me', 'experiences', 'chats', 'documents' or a collection's
    own id or name (Turkish folding). ValueError naming the unknown ones and what there is."""
    mine = await list_collections(conn, user_id)
    by_name = {memory.fold(c["name"]): c["id"] for c in mine}
    ids, unknown = [], []
    for name in names:
        key = (name or "").strip()
        found = IN_NAMES.get(key.lower()) or next((c["id"] for c in mine if c["id"] == key), None) \
            or by_name.get(memory.fold(key))
        if found:
            ids.append(found)
        elif key:
            unknown.append(key)
    if unknown:
        known = ", ".join(["me", "experiences", "chats", "documents"] +
                          [c["name"] for c in mine if c["kind"] != "builtin"])
        raise ValueError(f"Unknown collection: {', '.join(unknown)}. There are: {known}.")
    return list(dict.fromkeys(ids))


def filter_sql(collections: list[str] | None, params: list) -> str:
    """The SQL condition (after the user's, `$1` = user id) for rows in these collections: their main collection or
    a membership of their memory. Empty for None (the whole memory, as before)."""
    if not collections:
        return ""
    params.append(list(collections))
    n = len(params)
    return (f" and (collection_id = any(${n}::text[]) or group_id in (select i.group_id from orion_mem_collection_item i "
            f"where i.user_id = $1 and i.collection_id = any(${n}::text[])))")


# --- Sources ---------------------------------------------------------------------------------------------------------

TARGETS = ("fact", "event")


async def add_source(conn, user_id: str, target_type: str, target_id: int, *, chat_id: str | None = None,
                     first_message: int | None = None, last_message: int | None = None, item_id: str | None = None,
                     by_user: bool = False) -> int:
    """Link a fact or an event to where it came from; its id. A source must say something: a chat (with its
    messages), an item, or that the user wrote it (ValueError otherwise)."""
    if target_type not in TARGETS:
        raise ValueError(f"source target must be one of {TARGETS}")
    if not (chat_id or item_id or by_user):
        raise ValueError("a source needs a chat, an item or by_user")
    if chat_id and first_message is not None and last_message is not None and last_message < first_message:
        first_message, last_message = last_message, first_message
    return await conn.fetchval(
        "insert into orion_mem_source (user_id, target_type, target_id, chat_id, first_message, last_message, item_id, "
        "by_user) values ($1, $2, $3, $4, $5, $6, $7, $8) returning id", user_id, target_type, int(target_id), chat_id,
        first_message, last_message if last_message is not None else first_message, item_id, bool(by_user))


async def sources_of(conn, user_id: str, target_type: str, target_id: int) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "select id, chat_id, first_message, last_message, item_id, by_user, created_at from orion_mem_source "
        "where user_id = $1 and target_type = $2 and target_id = $3 order by id", user_id, target_type, int(target_id))]


async def sources_from_chat(conn, user_id: str, chat_id: str) -> list[dict]:
    """What was taken from a chat (for the chat-delete card, §11.3)."""
    return [dict(r) for r in await conn.fetch(
        "select id, target_type, target_id, first_message, last_message from orion_mem_source "
        "where user_id = $1 and chat_id = $2 order by id", user_id, chat_id)]
