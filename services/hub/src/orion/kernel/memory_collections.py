"""Collections (docs/hafiza-tasarimi.md §3, §13): the four fixed ones (Ben, Yaşadıklarım, Sohbetler, Belgeler); the user does not
make others. Belgeler, Yaşadıklarım and Sohbetler are membership rows of `orion_mem_collection_item` (`main`, one per memory,
never a column of the vector table: sorting would rewrite every vector row and its HNSW entries); Ben is not stored but
defined (ben_sql): the facts about the user and about the people close to them, and the events the user took part in. Behind
the per-user setting `memory_v2`: with it off nothing here runs and the memory behaves as before.

The first time a user's memory is used with `memory_v2` on, their collections are made and their files sorted into Belgeler, once
(ensure_user), after a dump of the memory tables. Every other old memory stays in no collection: it is listed under "Tüm hafıza"
(the whole memory) and found by a search over the whole memory. Group ids, parts and vectors stay as they are. revert() undoes
it (memberships and collections gone); the rows were never touched.

Source links (`orion_mem_source`): where a fact or an event came from (a chat and its first/last message, an item, or
the user typing it on the page). Nothing is written without one (§16); add_source / sources_of are the only way in."""
from __future__ import annotations

import logging

from orion.kernel import memory

logger = logging.getLogger(__name__)

BUILTIN = (("ben", "Ben"), ("yasadiklarim", "Yaşadıklarım"), ("sohbetler", "Sohbetler"), ("belgeler", "Belgeler"))
IN_NAMES = {"me": "ben", "experiences": "yasadiklarim", "chats": "sohbetler", "documents": "belgeler"}  # search_memory `in`
CHAT_PREFIX = "chat:"  # A collection name "chat:<chat id>": only the windows of that one chat (Sohbetler, narrowed).
USER_LOCK = 51730203  # With the user's hash: one sorting of one user's memory at a time, across Hub processes.


def enabled(settings) -> bool:
    return bool(getattr(settings, "memory_v2", False))


async def _sorted(conn, user_id: str) -> bool:
    return bool(await conn.fetchval("select 1 from orion_mem_collection where user_id = $1 and id = 'belgeler'",
                                    user_id))


async def ensure_user(conn, user_id: str) -> None:
    """Make this user's collections and sort their existing files into Belgeler, once. A primary-key lookup when it was done."""
    if await _sorted(conn, user_id):
        return
    await conn.execute("select pg_advisory_lock($1, hashtext($2))", USER_LOCK, user_id)
    try:
        if await _sorted(conn, user_id):
            return
        if await conn.fetchval("select exists (select 1 from orion_memory where user_id = $1)", user_id):
            from orion.kernel import memory_backup
            await memory_backup.backup("collections")
        async with conn.transaction():
            await conn.executemany(
                "insert into orion_mem_collection (user_id, id, kind, name) values ($1, $2, 'builtin', $3) "
                "on conflict do nothing", [(user_id, cid, name) for cid, name in BUILTIN])
            files = await conn.fetch(
                "select distinct group_id as g from orion_memory where user_id = $1 and source_kind = 'file' and "
                "group_id is not null", user_id)
            await conn.execute(
                "insert into orion_mem_collection_item (user_id, collection_id, group_id, main) "
                "select $1, 'belgeler', g, true from unnest($2::text[]) as x(g) on conflict do nothing",
                user_id, [r["g"] for r in files])
        logger.info("Memory: collections made for a user, %d files sorted into Belgeler", len(files))
    finally:
        await conn.execute("select pg_advisory_unlock($1, hashtext($2))", USER_LOCK, user_id)


async def revert(user_id: str) -> dict:
    """Undo the sorting: the memberships and the collections go. Nothing else changes (the memories were never
    touched). {"memories": memberships removed, "collections"}."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            from orion.kernel import memory_chat_index
            await memory_chat_index.forget_user(conn, user_id)  # Derived windows of the chats: rebuilt when it is on again.
            from orion.kernel import memory_extract
            await memory_extract.forget_user(conn, user_id)  # Runs and candidates of the extraction (derived, shadow).
            from orion.kernel import memory_profile
            await memory_profile.forget_user(conn, user_id)  # The built profile block (derived); pinned lines stay.
            rows = await conn.execute("delete from orion_mem_collection_item where user_id = $1", user_id)
            gone = await conn.execute("delete from orion_mem_collection where user_id = $1", user_id)
    finally:
        await conn.close()
    return {"memories": int(rows.split()[-1]), "collections": int(gone.split()[-1])}


async def list_collections(conn, user_id: str) -> list[dict]:
    """This user's collections: the four fixed ones in their order. No counts (they would read every row of the user)."""
    have = {r["id"] for r in await conn.fetch(
        "select id from orion_mem_collection where user_id = $1 and deleted_at is null", user_id)}
    return [{"id": cid, "kind": "builtin", "name": name} for cid, name in BUILTIN if cid in have]


async def resolve(conn, user_id: str, names: list[str]) -> list[str]:
    """Collection ids for the names a search asked for: 'me', 'experiences', 'chats', 'documents' or a collection's own id
    or name (Turkish folding), or "chat:<chat id>". ValueError naming the unknown ones and what there is."""
    by_name = {memory.fold(name): cid for cid, name in BUILTIN}
    ids, unknown = [], []
    for name in names:
        key = (name or "").strip()
        if key.startswith(CHAT_PREFIX):  # One chat of this user (its id is checked in filter_sql by the user's rows).
            if len(key) > len(CHAT_PREFIX):
                ids.append(key[:120])
            continue
        found = IN_NAMES.get(key.lower()) or next((cid for cid, _ in BUILTIN if cid == key), None)             or by_name.get(memory.fold(key))
        if found:
            ids.append(found)
        elif key:
            unknown.append(key)
    if unknown:
        raise ValueError(f"Unknown collection: {', '.join(unknown)}. There are: me, experiences, chats, documents.")
    return list(dict.fromkeys(ids))


def ben_sql() -> str:
    """The memories of the Ben collection, as a SQL condition on a row of `orion_memory` (after the user's, `$1` = user id):
    the current facts about the user and about the people close to them (an entity with a `relation`: how it relates to the
    user), and the events the user took part in. Defined, not stored, so a relation set later or a person merged is right at once;
    plain lookups by the user's entity ids, never a scan of the memory."""
    self_ids = "select id::text from orion_mem_entity where user_id = $1 and kind = 'self' and merged_into is null"
    return (
        "coalesce(group_id, 'r' || id::text) in ("
        "select f.group_id from orion_mem_fact f join orion_mem_entity en on en.id = f.entity_id and en.user_id = f.user_id "
        "where f.user_id = $1 and f.superseded_by is null and f.group_id is not null and en.merged_into is null and "
        "en.deleted_at is null and (en.kind = 'self' or en.relation is not null) "
        "union select e.group_id from orion_mem_event e join orion_mem_link l on l.user_id = e.user_id and "
        f"l.from_type = 'event' and l.from_id = e.id and l.to_type = 'entity' and l.to_id in ({self_ids}) "
        "where e.user_id = $1 and e.superseded_by is null)")


def filter_sql(collections: list[str] | None, params: list) -> str:
    """The SQL condition (after the user's, `$1` = user id) for rows whose memory is in these collections (main or
    member). Empty for None (the whole memory, as before). Sohbetler is the chats index: its windows by their source
    kind (a literal: their own partial vector index answers it), no membership lookup; "chat:<id>" narrows to one chat."""
    if not collections:
        return ""
    chats = [c[len(CHAT_PREFIX):] for c in collections if c.startswith(CHAT_PREFIX)]
    named = [c for c in collections if not c.startswith(CHAT_PREFIX)]
    every_chat = "sohbetler" in named
    named = [c for c in named if c not in ("sohbetler", "ben")]
    parts = []
    if "ben" in collections:
        parts.append(ben_sql())
    if named:
        params.append(named)
        parts.append(f"coalesce(group_id, 'r' || id::text) in (select i.group_id from orion_mem_collection_item i "
                     f"where i.user_id = $1 and i.collection_id = any(${len(params)}::text[]))")
    if every_chat:
        parts.append(f"source_kind = '{memory.CHAT_INDEX}'")
    elif chats:
        params.append(chats)
        parts.append(f"(source_kind = '{memory.CHAT_INDEX}' and source_chat_id = any(${len(params)}::text[]))")
    return f" and ({' or '.join(parts)})" if len(parts) > 1 else f" and {parts[0]}"


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
