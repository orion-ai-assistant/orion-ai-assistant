"""The chats index (docs/hafiza-tasarimi.md §6, §7.1, §16 step 3): the readable messages of a chat as windows in the
user's memory, in the Sohbetler collection, so "what did we say about X" is a memory search (`search_memory` with
`in: ["chats"]`, the page's Sohbetler strip, one chat with "chat:<id>").

Behind the per-user `memory_v2` setting (and `memory_index_chats`, on by default): with it off nothing here runs.

What is indexed. The windows are memory_pieces.chat_pieces of the chat's user and assistant messages ("Kullanıcı: ..."
/ "Asistan: ..."): a turn stays whole while it fits ~300 tokens, a longer one splits between its messages, one message
longer than a window splits alone; each window is embedded with the end of the previous and the start of the next
(windows_of), and keeps the first and last message id it covers (`source_message_id`/`source_message_last`: the chat
opens there). Tool results and failed turns are not messages of the transcript, so they are not embedded. A chat is one
memory (group), one item; the rows are `kind 'text'`, `source_kind 'chat_index'`: derived data, rebuilt from the chat,
never edited by hand. Embedding goes through the same path as every memory (memory.embedding_target, memory._embed,
the active model's vector space; a model move copies the windows like any text rows).

How it stays right. A chat is always indexed as a whole, but only what changed is embedded: the new windows are compared
with the stored ones (a window is the same when its core, its neighbours' cores and its message ids are) and the stored
ones up to the first difference stay as they are; from there on the rest is replaced, vectors of windows that did not
change are reused (nothing is embedded again for them). A new turn therefore costs the last window or two, an edit or a
delete in the middle the windows from there. One chat is indexed by one process at a time (an advisory lock); nothing
is written until every vector is made, so a cancelled or failed run leaves the old index.

When. After a turn is saved (after_turn) a background task waits a few seconds, then indexes: the chat never waits for
it. A message deleted (failed turns) or edited asks for the same (changed). Deleting a chat removes its windows
(forget_chat). Old chats, and chats a restart or a stopped Router left behind, are indexed by the user's button
(start_backlog: one job per user, progress, cancel): every chat whose newest message is past `indexed_upto`.

Every table has user_id and every read filters by it."""
from __future__ import annotations

import asyncio
import json
import logging

from orion.kernel import memory
from orion.kernel.memory import CHAT_INDEX

logger = logging.getLogger(__name__)

COLLECTION = "sohbetler"
CHAT_LOCK = 51730205  # With hashtext(chat id): one indexing of one chat at a time, across Hub processes.
DEBOUNCE = 5.0  # Seconds after a turn is saved before its chat is indexed (a quick follow-up turn is one run).
STALE = memory.JOB_STALE_SECONDS
_tasks: dict[str, asyncio.Task] = {}
_again: set[str] = set()
_jobs: set[asyncio.Task] = set()


def enabled(settings) -> bool:
    """The user has the new memory on and has not turned the chats index off."""
    return bool(getattr(settings, "memory_v2", False)) and bool(getattr(settings, "memory_index_chats", True))


def _keys(cores: list[str], firsts: list, lasts: list) -> list[tuple]:
    """What makes a stored window the same as a new one: its core, the cores around it (its window is made of them) and
    the messages it covers."""
    count = len(cores)
    return [(cores[i - 1] if i else None, cores[i], cores[i + 1] if i + 1 < count else None, firsts[i], lasts[i])
            for i in range(count)]


async def _drop(conn, user_id: str, chat_id: str) -> int:
    """The chat's windows and its collection membership gone (the chat itself is untouched)."""
    async with conn.transaction():
        rows = await memory.delete_rows(conn, user_id, "source_chat_id = $2 and source_kind = $3", chat_id, CHAT_INDEX)
        ids = [r["id"] for r in rows]
        if ids:
            await conn.execute("delete from orion_mem_trash where user_id = $1 and memory_id = any($2::bigint[])",
                               user_id, ids)
        groups = sorted({r["group_id"] for r in rows if r.get("group_id")})
        if groups:
            await conn.execute("delete from orion_mem_collection_item where user_id = $1 and collection_id = $2 "
                               "and group_id = any($3::text[])", user_id, COLLECTION, groups)
        await conn.execute("update orion_mem_chat_state set windows = 0, group_id = null, item_id = null "
                           "where user_id = $1 and chat_id = $2", user_id, chat_id)
    return len(rows)


async def index_chat(conn, user_id: str, chat_id: str, settings, target=None, cancelled=None) -> dict:
    """Bring this chat's windows up to date (see the module's text). `target`: (settings, space, local) when the caller
    has it. `cancelled()`: awaited between embeddings, True stops (nothing is written). Returns
    {"state": ok | off | gone | trash | cancelled, "windows", "embedded"}; MemoryUnavailable when no model can embed."""
    if not await conn.fetchval("select 1 from orion_chats where id = $1 and user_id = $2", chat_id, user_id):
        return {"state": "gone"}
    await conn.execute("select pg_advisory_lock($1, hashtext($2))", CHAT_LOCK, chat_id)
    try:
        return await _index_locked(conn, user_id, chat_id, settings, target, cancelled)
    finally:
        await conn.execute("select pg_advisory_unlock($1, hashtext($2))", CHAT_LOCK, chat_id)


async def _index_locked(conn, user_id: str, chat_id: str, settings, target, cancelled) -> dict:
    from orion.kernel.memory_pieces import chat_pieces
    state = await conn.fetchrow("select memory_enabled, group_id, item_id from orion_mem_chat_state "
                                "where user_id = $1 and chat_id = $2", user_id, chat_id)
    if state is not None and not state["memory_enabled"]:
        await _drop(conn, user_id, chat_id)
        return {"state": "off", "windows": 0, "embedded": 0}
    title = await conn.fetchval("select title from orion_chats where id = $1 and user_id = $2", chat_id, user_id)
    messages, ids = await memory._chat_messages(chat_id)
    seen = max(ids) if ids else 0
    kept = [(m, i) for m, i in zip(messages, ids) if not m.get("failed_turn")]
    paragraphs = memory.chat_paragraphs([m for m, _ in kept], [i for _, i in kept])
    split = await asyncio.to_thread(chat_pieces, [text for _, text in paragraphs]) if paragraphs else []
    new = [{"core": core, "window": window, "first": paragraphs[first][0], "last": paragraphs[last][0]}
           for core, window, first, last in split]
    old = [dict(r) for r in await conn.fetch(
        "select m.id, m.text, m.source_message_id as first, m.source_message_last as last, m.item_order, m.group_id, "
        "m.item_id, m.vector_space, coalesce(m.copied_from, m.id) as content, (t.memory_id is not null) as trashed "
        "from orion_memory m left join orion_mem_trash t on t.memory_id = m.id "
        "where m.user_id = $1 and m.source_chat_id = $2 and m.source_kind = $3 and m.kind = 'text' and not m.whole "
        "order by m.item_order, coalesce(m.copied_from, m.id)", user_id, chat_id, CHAT_INDEX)]
    if any(r["trashed"] for r in old):  # The user removed this chat's memory: it stays removed (restore brings it back).
        return {"state": "trash", "windows": 0, "embedded": 0}
    target = target or await memory.embedding_target(settings)
    _, space, local = target
    mine = [r for r in old if r["vector_space"] == space]
    old_keys = _keys([r["text"] for r in mine], [r["first"] for r in mine], [r["last"] for r in mine])
    new_keys = _keys([n["core"] for n in new], [n["first"] for n in new], [n["last"] for n in new])
    same = 0
    while same < min(len(old_keys), len(new_keys)) and old_keys[same] == new_keys[same]:
        same += 1
    keep = {r["content"] for r in mine[:same]}
    gone = [r["id"] for r in old if r["content"] not in keep]
    reuse: dict[tuple, str] = {}
    if len(mine) > same and len(new) > same:  # Windows after the first difference that are still the same: vectors kept.
        later = {r["id"]: key for r, key in zip(mine[same:], old_keys[same:])}
        for row in await conn.fetch("select id, embedding::text as v from orion_memory where user_id = $1 "
                                    "and id = any($2::bigint[])", user_id, list(later)):
            reuse[later[row["id"]]] = row["v"]
    vectors: list = []
    embedded = 0
    for index in range(same, len(new)):
        if cancelled is not None and await cancelled():
            return {"state": "cancelled", "windows": len(old), "embedded": embedded}
        known = reuse.get(new_keys[index])
        if known is not None:
            vectors.append(known)
            continue
        vector = (await memory._embed(new[index]["window"], target[0], "document", local))[0]
        vectors.append(memory._vector(vector))
        embedded += 1
    group = (state["group_id"] if state else None) or next((r["group_id"] for r in old if r["group_id"]), None) \
        or memory.new_group()
    item = (state["item_id"] if state else None) or next((r["item_id"] for r in old if r["item_id"]), None) \
        or memory.new_group()
    order = next((r["item_order"] for r in old if r["item_order"]), None) or memory.item_order()
    async with conn.transaction():
        if gone:
            await memory.delete_rows(conn, user_id, "id = any($2::bigint[])", gone)
        for piece, vector in zip(new[same:], vectors):
            await conn.fetchval(
                memory.ROW_SQL, user_id, piece["core"], space, vector, CHAT_INDEX, title or None, "text", None, None,
                None, group, chat_id, piece["first"], item, False, None, None, None, None, order, piece["last"],
                memory.fold(memory.match_text(piece["core"], title, None, CHAT_INDEX)), None, True)
        member = bool(new) and bool(await conn.fetchval(
            "select 1 from orion_mem_collection where user_id = $1 and id = $2", user_id, COLLECTION))
        if member:
            await conn.execute("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) "
                               "values ($1, $2, $3, true) on conflict do nothing", user_id, COLLECTION, group)
        elif not new:
            await conn.execute("delete from orion_mem_collection_item where user_id = $1 and collection_id = $2 "
                               "and group_id = $3", user_id, COLLECTION, group)
        await conn.execute(
            "insert into orion_mem_chat_state (user_id, chat_id, indexed_upto, group_id, item_id, windows, "
            "vector_space, indexed_at) values ($1, $2, $3, $4, $5, $6, $7, now()) "
            "on conflict (user_id, chat_id) do update set indexed_upto = excluded.indexed_upto, "
            "group_id = excluded.group_id, item_id = excluded.item_id, windows = excluded.windows, "
            "vector_space = excluded.vector_space, indexed_at = now()",
            user_id, chat_id, seen, group if new else None, item if new else None, len(new), space)
    return {"state": "ok", "windows": len(new), "embedded": embedded}


async def index_now(chat_id: str, settings, user_id: str | None = None) -> dict:
    """index_chat on its own connection, for the chat's owner (looked up when not given). Nothing happens unless the
    user's memory_v2 and chats index are on."""
    if not enabled(settings):
        return {"state": "off", "windows": 0, "embedded": 0}
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        user_id = user_id or await conn.fetchval("select user_id from orion_chats where id = $1", chat_id)
        if not user_id:
            return {"state": "gone", "windows": 0, "embedded": 0}
        from orion.kernel import memory_collections
        await memory_collections.ensure_user(conn, user_id)
        return await index_chat(conn, user_id, chat_id, settings)
    finally:
        await conn.close()


def changed(chat_id: str, settings) -> None:
    """The chat's messages changed (a turn saved, a message deleted or edited): index it in the background, soon, and
    once for a burst of changes. Returns at once; never raises. Without a running event loop (a script) nothing happens."""
    if not enabled(settings):
        return
    if chat_id in _tasks and not _tasks[chat_id].done():
        _again.add(chat_id)
        return
    try:
        task = asyncio.get_running_loop().create_task(_after(chat_id, settings), name=f"memory-chat-index-{chat_id[:8]}")
    except RuntimeError:
        return
    _tasks[chat_id] = task
    task.add_done_callback(lambda t, c=chat_id: _tasks.get(c) is t and _tasks.pop(c, None))


after_turn = changed  # The name the turn's end uses.


async def _after(chat_id: str, settings) -> None:
    try:
        while True:
            await asyncio.sleep(DEBOUNCE)
            _again.discard(chat_id)
            try:
                await index_now(chat_id, settings)
            except memory.MemoryUnavailable as exc:
                logger.info("Memory: chat %s not indexed now (%s); the Eski sohbetleri dizinle button catches up",
                            chat_id[:8], exc)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Memory: chat %s could not be indexed", chat_id[:8], exc_info=True)
            if chat_id not in _again:
                return
    finally:
        _again.discard(chat_id)


async def forget_chat(chat_id: str, user_id: str | None = None) -> int:
    """The chat was deleted: its windows, its state and its membership go (for good: they are a copy of the chat).
    `user_id` is the chat's owner; without it every user's rows of that chat id (chat ids are unique). Windows
    removed; never raises (deleting a chat must not fail because of memory)."""
    try:
        conn = await memory._connect()
    except memory.MemoryUnavailable:
        return 0
    try:
        await memory.ensure_memory_table(conn)
        users = [user_id] if user_id else [r["user_id"] for r in await conn.fetch(
            "select user_id from orion_mem_chat_state where chat_id = $1 union "
            "select user_id from orion_mem_extraction_run where chat_id = $1 union "
            "select user_id from orion_memory where source_chat_id = $1 and source_kind = $2", chat_id, CHAT_INDEX)]
        count = 0
        for user in users:
            await conn.execute("select pg_advisory_lock($1, hashtext($2))", CHAT_LOCK, chat_id)
            try:
                count += await _drop(conn, user, chat_id)
                await conn.execute("delete from orion_mem_chat_state where user_id = $1 and chat_id = $2", user, chat_id)
                from orion.kernel import memory_extract  # Its runs and candidates (they hold the user's sentences) go too.
                await memory_extract.forget_chat(conn, user, chat_id)
            finally:
                await conn.execute("select pg_advisory_unlock($1, hashtext($2))", CHAT_LOCK, chat_id)
        return count
    except Exception:
        logger.warning("Memory: the windows of a deleted chat could not be removed", exc_info=True)
        return 0
    finally:
        await conn.close()


async def forget_user(conn, user_id: str) -> int:
    """Every chat window and chat state of this user (memory_v2 turned off for good: collections revert). The chats are
    untouched; turning it on again and pressing the button rebuilds them."""
    async with conn.transaction():
        rows = await memory.delete_rows(conn, user_id, "source_kind = $2", CHAT_INDEX)
        await conn.execute("delete from orion_mem_trash t where t.user_id = $1 and not exists "
                           "(select 1 from orion_memory m where m.id = t.memory_id)", user_id)
        await conn.execute("delete from orion_mem_chat_state where user_id = $1", user_id)
        await conn.execute("delete from orion_mem_chat_index_jobs where user_id = $1", user_id)
    return len(rows)


async def set_chat_enabled(user_id: str, chat_id: str, on: bool, settings) -> dict:
    """The per-chat switch (ChatGPT's temporary chat): off removes the chat's windows and keeps them out; on indexes it
    again at once in the background. LookupError for a chat this user does not have."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        if not await conn.fetchval("select 1 from orion_chats where id = $1 and user_id = $2", chat_id, user_id):
            raise LookupError(chat_id)
        await conn.execute("insert into orion_mem_chat_state (user_id, chat_id, memory_enabled) values ($1, $2, $3) "
                           "on conflict (user_id, chat_id) do update set memory_enabled = excluded.memory_enabled",
                           user_id, chat_id, on)
        if not on:
            await conn.execute("select pg_advisory_lock($1, hashtext($2))", CHAT_LOCK, chat_id)
            try:
                await _drop(conn, user_id, chat_id)
            finally:
                await conn.execute("select pg_advisory_unlock($1, hashtext($2))", CHAT_LOCK, chat_id)
            # A chat that is out of the memory is out of the extraction too: its runs, candidates and summary go.
            from orion.kernel import memory_extract
            await memory_extract.forget_chat(conn, user_id, chat_id)
            await conn.execute("update orion_mem_chat_state set summary = null, summary_upto = null, summary_stale = false, "
                               "summary_at = null, extracted_upto = null where user_id = $1 and chat_id = $2", user_id, chat_id)
    finally:
        await conn.close()
    if on:
        changed(chat_id, settings)
    return {"chat_id": chat_id, "memory_enabled": on}


async def chat_state(user_id: str, chat_id: str) -> dict:
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        row = await conn.fetchrow("select memory_enabled, windows, indexed_upto, indexed_at, summary, summary_stale from "
                                  "orion_mem_chat_state where user_id = $1 and chat_id = $2", user_id, chat_id)
    finally:
        await conn.close()
    if row is None:
        return {"chat_id": chat_id, "memory_enabled": True, "windows": 0, "indexed_at": None, "summary": None}
    return {"chat_id": chat_id, "memory_enabled": row["memory_enabled"], "windows": row["windows"],
            "indexed_at": row["indexed_at"].isoformat() if row["indexed_at"] else None, "summary": row["summary"],
            "summary_stale": row["summary_stale"]}


# --- The job that indexes the old chats ------------------------------------------------------------------------------

# Chats with something not indexed yet: messages and no state, or a message past `indexed_upto`; off chats and chats
# without messages are left out.
_TODO = ("from orion_chats c left join orion_mem_chat_state s on s.user_id = c.user_id and s.chat_id = c.id, "
         "lateral (select max(m.id) as newest from orion_chat_messages m where m.chat_id = c.id) n "
         "where c.user_id = $1 and coalesce(s.memory_enabled, true) and n.newest is not null "
         "and (s.indexed_upto is null or s.indexed_upto < n.newest)")


async def status(user_id: str) -> dict:
    """{"chats": all of the user's chats, "indexed": chats with windows, "windows", "pending": chats the button would
    index, "job": the backlog job or None}."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        chats = await conn.fetchval("select count(*) from orion_chats where user_id = $1", user_id)
        counts = await conn.fetchrow("select count(*) filter (where windows > 0) as indexed, coalesce(sum(windows), 0) "
                                     "as windows from orion_mem_chat_state where user_id = $1 and memory_enabled",
                                     user_id)
        pending = await conn.fetchval(f"select count(*) {_TODO}", user_id)
        row = await conn.fetchrow("select *, extract(epoch from now() - updated_at) as idle "
                                  "from orion_mem_chat_index_jobs where user_id = $1", user_id)
    finally:
        await conn.close()
    job = None
    if row is not None:
        state = "interrupted" if row["state"] == "running" and float(row["idle"]) > STALE else row["state"]
        job = {"state": state, "total": row["total"], "done": row["done"], "windows": row["windows"],
               "failed": row["failed"], "message": row["message"], "cancel": row["cancel"],
               "started_at": row["started_at"].isoformat(),
               "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None}
    return {"chats": int(chats), "indexed": int(counts["indexed"]), "windows": int(counts["windows"]),
            "pending": int(pending), "job": job}


async def start_backlog(user_id: str, settings) -> dict:
    """Index every chat that is not indexed (or has newer messages), newest first, in the background: the status. One
    job per user; ValueError with a Turkish sentence when the memory is off or a job is running."""
    if not getattr(settings, "memory_v2", False):
        raise ValueError("Sohbet dizini için Ayarlar'da Hafıza koleksiyonları (deneme) açık olmalı.")
    if not getattr(settings, "memory_index_chats", True):
        raise ValueError("Sohbet dizini kapalı.")
    target = await memory.embedding_target(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        total = await conn.fetchval(f"select count(*) {_TODO}", user_id)
        claimed = await conn.fetchval(
            "insert into orion_mem_chat_index_jobs (user_id, state, total) values ($1, 'running', $2) "
            "on conflict (user_id) do update set state = 'running', total = excluded.total, done = 0, windows = 0, "
            "failed = 0, message = null, cancel = false, started_at = now(), updated_at = now(), finished_at = null "
            f"where orion_mem_chat_index_jobs.state <> 'running' or orion_mem_chat_index_jobs.updated_at < now() - "
            f"interval '{STALE} seconds' returning true", user_id, total)
        if not claimed:
            raise ValueError("Sohbetler zaten dizinleniyor.")
    finally:
        await conn.close()
    task = asyncio.create_task(_run(user_id, settings, target), name=f"memory-chat-backlog-{user_id}")
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return (await status(user_id))["job"]


async def cancel_backlog(user_id: str) -> dict | None:
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        # A job whose process is gone cannot see the flag: it ends here.
        await conn.execute(
            "update orion_mem_chat_index_jobs set cancel = true, state = case when updated_at < now() - interval "
            f"'{STALE} seconds' then 'cancelled' else state end, finished_at = case when updated_at < now() - interval "
            f"'{STALE} seconds' then now() else finished_at end where user_id = $1 and state = 'running'", user_id)
    finally:
        await conn.close()
    return (await status(user_id))["job"]


async def _run(user_id: str, settings, target) -> None:
    conn = await memory._connect()
    done = windows = failed = 0
    message, final = None, "done"
    try:
        from orion.kernel import memory_collections
        await memory_collections.ensure_user(conn, user_id)
        todo = [r["id"] for r in await conn.fetch(f"select c.id {_TODO} order by c.updated_at desc, c.id desc", user_id)]

        async def cancelled() -> bool:
            return bool(await conn.fetchval("select cancel from orion_mem_chat_index_jobs where user_id = $1", user_id))

        for chat_id in todo:
            if await cancelled():
                final = "cancelled"
                break
            try:
                result = await index_chat(conn, user_id, chat_id, settings, target, cancelled)
                windows += result.get("windows", 0) if result["state"] == "ok" else 0
                if result["state"] == "cancelled":
                    final = "cancelled"
                    break
            except memory.MemoryUnavailable as exc:  # No model / Router down: every other chat would fail the same way.
                final, message = "failed", str(exc)
                break
            except Exception:
                failed += 1
                logger.warning("Memory: chat %s could not be indexed", chat_id[:8], exc_info=True)
            done += 1
            await conn.execute("update orion_mem_chat_index_jobs set done = $2, windows = $3, failed = $4, "
                               "updated_at = now() where user_id = $1", user_id, done, windows, failed)
    except asyncio.CancelledError:
        final, message = "failed", "Dizinleme durduruldu."
        raise
    except Exception as exc:
        final, message = "failed", str(exc)
        logger.warning("Memory: the chats index job failed", exc_info=True)
    finally:
        try:
            await conn.execute("update orion_mem_chat_index_jobs set state = $2, message = $3, done = $4, windows = $5, "
                               "failed = $6, updated_at = now(), finished_at = now() where user_id = $1",
                               user_id, final, message, done, windows, failed)
        finally:
            await conn.close()
