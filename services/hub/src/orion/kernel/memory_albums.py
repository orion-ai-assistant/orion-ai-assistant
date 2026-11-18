"""Albums (docs/hafiza-tasarimi.md §3, §12, §16 step 5): collections the user opens ("Kapadokya 2026", "Ev tadilatı"), kind
'album' in `orion_mem_collection`. An album groups memories without changing them: a memory is a member through a row of
`orion_mem_collection_item` (not `main`: its main collection stays Ben / Yaşadıklarım / Sohbetler / Belgeler / Eski hafıza),
one memory may be in many albums, and removing it from an album never touches the memory (its `group_id`, parts and vectors
stay, so open_memory_files / delete_memory(whole_memory) work as before). The page shows an album's members with the ordinary
list (`collection=<album id>`), search takes the album's name in `in`.

An album is removed softly (`deleted_at`: its memberships stay, so Geri al brings it back whole); a search or a strip does not
list a removed album. Names are unique per user after Turkish folding (a search names an album by it) and may not be a
built-in's name or one of search_memory's `in` words. Behind `memory_v2`. Every table has user_id; every read filters by it.
"""
from __future__ import annotations

from orion.kernel import memory, memory_collections as mc, memory_entities as me, memory_events

MAX_NAME = 80
ALBUM_LOCK = 51730207  # With hashtext(user id): one album created, renamed or restored at a time per user (unique names).
RESERVED = {memory.fold(name) for _, name in mc.BUILTIN} | {*mc.IN_NAMES, "all", "tumu", "chats"}


def _name(value: str) -> str:
    text = " ".join((value or "").split())[:MAX_NAME]
    if not text:
        raise ValueError("Bir ad gerekli.")
    return text


async def _free(conn, user_id: str, name: str, except_id: str | None = None) -> None:
    if memory.fold(name) in RESERVED:
        raise ValueError(f"{name!r} is a reserved name: pick another.")
    for r in await conn.fetch("select id, name from orion_mem_collection where user_id = $1 and deleted_at is null", user_id):
        if r["id"] != except_id and memory.fold(r["name"]) == memory.fold(name):
            raise ValueError(f"There is already a collection called {r['name']!r}.")


async def list_albums(user_id: str, settings) -> list[dict]:
    """The user's albums and projects (not the built-ins): [{"id", "name", "kind", "count" (memories that are live)}]."""
    me.require(settings)
    async with me._db() as conn:
        await mc.ensure_user(conn, user_id)
        rows = await conn.fetch(
            "select c.id, c.name, c.kind, (select count(*) from orion_mem_collection_item i where i.user_id = c.user_id and "
            "i.collection_id = c.id and exists (select 1 from orion_memory m where m.user_id = i.user_id and "
            "m.group_id = i.group_id and not m.whole and not exists (select 1 from orion_mem_trash t where "
            "t.memory_id = m.id))) as n from orion_mem_collection c where c.user_id = $1 and c.kind <> 'builtin' and "
            "c.deleted_at is null", user_id)
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "count": int(r["n"])}
            for r in sorted(rows, key=lambda r: memory.fold(r["name"]))]


async def create_album(user_id: str, settings, name: str) -> dict:
    """A new album. ValueError for an empty, reserved or taken name."""
    me.require(settings)
    name = _name(name)
    async with me._db() as conn:
        await mc.ensure_user(conn, user_id)
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ALBUM_LOCK, user_id)
            await _free(conn, user_id, name)
            album = "a_" + memory.new_group()
            await conn.execute("insert into orion_mem_collection (user_id, id, kind, name) values ($1, $2, 'album', $3)",
                               user_id, album, name)
    return {"id": album, "name": name, "kind": "album", "count": 0}


async def _mine(conn, user_id: str, album_id: str, removed: bool = False):
    row = await conn.fetchrow("select id, name, kind, deleted_at from orion_mem_collection where user_id = $1 and id = $2 "
                              "and kind <> 'builtin'", user_id, album_id)
    if row is None or (row["deleted_at"] is not None) != removed:
        raise LookupError(album_id)
    return row


async def rename_album(user_id: str, settings, album_id: str, name: str) -> dict:
    me.require(settings)
    name = _name(name)
    async with me._db() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ALBUM_LOCK, user_id)
            row = await _mine(conn, user_id, album_id)
            await _free(conn, user_id, name, album_id)
            await conn.execute("update orion_mem_collection set name = $3 where user_id = $1 and id = $2", user_id, album_id,
                               name)
    return {"id": album_id, "name": name, "kind": row["kind"]}


async def remove_album(user_id: str, settings, album_id: str) -> dict:
    """The album goes away, its memories stay where they are; restore_album brings it back with its members."""
    me.require(settings)
    async with me._db() as conn:
        await _mine(conn, user_id, album_id)
        await conn.execute("update orion_mem_collection set deleted_at = now() where user_id = $1 and id = $2", user_id,
                           album_id)
    return {"removed": album_id}


async def restore_album(user_id: str, settings, album_id: str) -> dict:
    """Bring a removed album back. ValueError when its name was taken meanwhile."""
    me.require(settings)
    async with me._db() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ALBUM_LOCK, user_id)
            row = await _mine(conn, user_id, album_id, removed=True)
            await _free(conn, user_id, row["name"], album_id)
            await conn.execute("update orion_mem_collection set deleted_at = null where user_id = $1 and id = $2", user_id,
                               album_id)
    return {"id": album_id, "name": row["name"], "kind": row["kind"]}


async def set_members(user_id: str, settings, album_id: str, add: list[str] | None = None,
                      remove: list[str] | None = None) -> dict:
    """Put memories (group keys) into the album / take them out. Adding needs the memories to be this user's and not
    removed (LookupError otherwise); taking out only deletes the album's own membership (never a main collection).
    {"added", "removed"}."""
    me.require(settings)
    async with me._db() as conn:
        async with conn.transaction():
            await _mine(conn, user_id, album_id)
            keys = await memory_events.groups_exist(conn, user_id, add or []) if add else []
            added = 0
            for key in keys:
                done = await conn.execute(
                    "insert into orion_mem_collection_item (user_id, collection_id, group_id, main) values ($1, $2, $3, false) "
                    "on conflict do nothing", user_id, album_id, key)
                added += int(done.split()[-1])
            gone = [str(g)[:64] for g in remove or []]
            taken = await conn.execute(
                "delete from orion_mem_collection_item where user_id = $1 and collection_id = $2 and not main and "
                "group_id = any($3::text[])", user_id, album_id, gone) if gone else "DELETE 0"
    return {"added": added, "removed": int(taken.split()[-1])}


async def albums_of(user_id: str, settings, group: str) -> list[dict]:
    """Every album / project of the user, each with `member` (is this memory in it): the page's "Albümlere ekle" list."""
    me.require(settings)
    async with me._db() as conn:
        await mc.ensure_user(conn, user_id)
        rows = await conn.fetch(
            "select c.id, c.name, c.kind, exists (select 1 from orion_mem_collection_item i where i.user_id = c.user_id and "
            "i.collection_id = c.id and i.group_id = $2) as member from orion_mem_collection c where c.user_id = $1 and "
            "c.kind <> 'builtin' and c.deleted_at is null", user_id, group[:64])
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "member": r["member"]}
            for r in sorted(rows, key=lambda r: memory.fold(r["name"]))]


async def album_id_of(conn, user_id: str, name: str) -> str:
    """The id of the user's album / project called `name` (or having that id): LookupError listing what there is."""
    mine = [c for c in await mc.list_collections(conn, user_id) if c["kind"] != "builtin"]
    found = next((c["id"] for c in mine if c["id"] == name or memory.fold(c["name"]) == memory.fold(name)), None)
    if found is None:
        raise LookupError("There is no album or project called " + repr(name) + ". There are: " +
                          (", ".join(c["name"] for c in mine) or "none (only the user opens albums)") + ".")
    return found
