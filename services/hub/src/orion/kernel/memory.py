"""Minimal memory: chunks of a saved chat with their embedding, searched by the `search_memory` tool.

Rows carry their own text, so deleting the chat keeps them; `source_chat_id`/`source_message_id` only point back at
where a row came from (the chat may be gone). Image and audio rows (kind 'image'/'audio') carry a short label as
their text and `media_id`: a link of owner 'memory' keeps the file after the chat is deleted, and deleting the row
drops that link. Rows of another embedding model (`vector_space`) are never compared with the current one.

`group_id` ties the rows saved together (one chat save, one note, one file) into one memory: the page shows a group
as one card. A copy of a row made for another embedding model (memory_move) keeps the group and names its original
in `copied_from`, so `coalesce(copied_from, id)` (CONTENT_KEY) is the same for a row and all its copies."""
from __future__ import annotations

import functools
import logging
import re

from orion.kernel import registry
from orion.kernel.chat_search import message_text

logger = logging.getLogger(__name__)

SETUP_MESSAGE = "Hafıza için Bileşenler'de embedding bileşenini kur ve aç."
ROUTER_DOWN_MESSAGE = "Router'a ulaşılamıyor: hafıza araması şimdilik kapalı. Router çalışınca yeniden dene."


def unreachable(exc: BaseException) -> bool:
    """Whether `exc` (or what caused it) is a failure to reach the Router at all (refused, timed out), not an
    answer saying the embedding component is missing or off."""
    import asyncio
    import socket
    kinds: tuple = (OSError, TimeoutError, asyncio.TimeoutError, socket.gaierror)  # ConnectionError is an OSError
    try:
        import aiohttp
        kinds += (aiohttp.ClientConnectionError,)
    except ImportError:
        pass
    try:
        import httpx
        kinds += (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError)
    except ImportError:
        pass
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, kinds):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False
NO_VECTOR_MESSAGE = "Hafıza kullanılamıyor: veritabanında pgvector (vector eklentisi) yok."
SCHEMA_LOCK = 51730201  # Its own number: 51730102-51730104 belong to the media tables and their migration.


class MemoryUnavailable(Exception):
    """The memory cannot work now; the text is the Turkish answer for the user."""


_table_ready = False
_trgm = False  # pg_trgm is there: near spellings are found by the database, else by Python.


async def ensure_memory_table(conn) -> None:
    global _table_ready
    if _table_ready:
        return
    try:
        if not await conn.fetchval("select 1 from pg_extension where extname = 'vector'"):
            raise MemoryUnavailable(NO_VECTOR_MESSAGE)
        await conn.execute("select pg_advisory_lock($1)", SCHEMA_LOCK)
        try:
            await conn.execute("""
                create table if not exists orion_memory (
                    id bigserial primary key,
                    user_id text not null,
                    text text not null,
                    vector_space text not null,
                    embedding vector(768) not null,
                    source_kind text not null,
                    source_chat_title text,
                    created_at timestamptz not null default now()
                )""")
            await conn.execute("alter table orion_memory add column if not exists kind text not null default 'text'")
            await conn.execute("alter table orion_memory add column if not exists media_id text")
            await conn.execute("alter table orion_memory add column if not exists media_mime text")
            await conn.execute("alter table orion_memory add column if not exists media_seconds real")
            # (user, space, id): also serves "this user's newest rows" without sorting all of them.
            await conn.execute("create index if not exists idx_orion_memory_user_id on orion_memory "
                               "(user_id, vector_space, id)")
            await conn.execute("drop index if exists idx_orion_memory_user")
            for column in ("group_id text", "copied_from bigint", "source_chat_id text", "source_message_id bigint",
                           "item_id text", "whole boolean not null default false", "span_start real", "span_end real",
                           "track text", "caption text", "item_order bigint", "source_message_last bigint", "lossless boolean not null default false",
                           "folded text",
                           "caption_folded text"):
                await conn.execute(f"alter table orion_memory add column if not exists {column}")
            await conn.execute("create index if not exists idx_orion_memory_group on orion_memory (user_id, group_id)")
            await conn.execute("create index if not exists idx_orion_memory_content on orion_memory "
                               "(user_id, vector_space, (coalesce(copied_from, id)))")
            await conn.execute("""
                create table if not exists orion_memory_jobs (
                    user_id text primary key,
                    source_space text not null,
                    target_space text not null,
                    state text not null,
                    total integer not null default 0,
                    done integer not null default 0,
                    copied integer not null default 0,
                    existing integer not null default 0,
                    unsupported jsonb not null default '{}'::jsonb,
                    failed integer not null default 0,
                    message text,
                    cancel boolean not null default false,
                    started_at timestamptz not null default now(),
                    updated_at timestamptz not null default now(),
                    finished_at timestamptz
                )""")
            await conn.execute("""
                create table if not exists orion_memory_media_jobs (
                    id text primary key,
                    user_id text not null,
                    group_id text not null,
                    item_id text not null,
                    name text not null default '',
                    kind text not null,
                    state text not null,
                    done integer not null default 0,
                    total integer not null default 0,
                    message text,
                    cancel boolean not null default false,
                    started_at timestamptz not null default now(),
                    updated_at timestamptz not null default now(),
                    finished_at timestamptz
                )""")
            await conn.execute("create index if not exists idx_orion_memory_media_jobs_user on orion_memory_media_jobs "
                               "(user_id, started_at)")
            # sha256 of the text, kept by a trigger (every insert path has it; the generated-column form is refused
            # because convert_to is not immutable): the duplicate check is one indexed lookup, not a text compare.
            await conn.execute("alter table orion_memory add column if not exists content_hash text")
            await conn.execute("""
                create or replace function orion_memory_hash() returns trigger language plpgsql as $$
                begin
                    new.content_hash := encode(sha256(convert_to(new.text, 'UTF8')), 'hex');
                    return new;
                end $$""")
            await conn.execute("drop trigger if exists orion_memory_hash on orion_memory")
            await conn.execute("create trigger orion_memory_hash before insert or update of text on orion_memory "
                               "for each row execute function orion_memory_hash()")
            while await conn.fetchval(  # Old rows, in batches (one statement would hold every row's lock at once).
                    "with todo as (select id from orion_memory where content_hash is null limit 5000), "
                    "done as (update orion_memory m set text = m.text from todo where m.id = todo.id returning 1) "
                    "select count(*) from done"):
                pass
            # Every row is stored with a group now: the one-time grouping of old rows runs until it has run once
            # (a marker), not as a scan of the whole table at every start.
            await conn.execute("create table if not exists orion_memory_meta (key text primary key, value text)")
            await _migrate(conn)
            if not await conn.fetchval("select value from orion_memory_meta where key = 'groups_backfilled'"):
                from orion.kernel.memory_groups import backfill_groups
                await backfill_groups(conn)
                await conn.execute("insert into orion_memory_meta (key, value) values ('groups_backfilled', 'yes') "
                                   "on conflict do nothing")
            await backfill_message_last(conn)
            await _fold_columns(conn)
            await _counter_triggers(conn)
        finally:
            await conn.execute("select pg_advisory_unlock($1)", SCHEMA_LOCK)
    except MemoryUnavailable:
        raise
    except Exception as exc:
        logger.warning("Memory table could not be prepared: %s", exc)
        raise MemoryUnavailable(NO_VECTOR_MESSAGE) from exc
    await _enable_trigrams(conn)
    _table_ready = True
    _schedule_index_build()


SCHEMA_VERSION = 12  # orion_memory_meta 'schema_version'. 2: collections and sources. 3: the trash. 4: the chats index. 5: entities and facts. 6: events, links, recurring facts. 7: extraction runs, candidates, chat summaries. 8: the profile block (pinned lines). 9: no albums, projects or "Eski hafıza"; Ben is defined, not a membership. 10: no chats index (windows, Sohbetler, its tables). 11: facts keep how exact their start is (`since_precision`). 12: event texts rendered again from their fields (title + body stored; place/names only embedded) and their vectors redone in the background (memory_events.reembed_loop).
LIVE = "not exists (select 1 from orion_mem_trash t where t.memory_id = id)"  # Not in the trash: what every read sees.


def live(alias: str) -> str:
    """LIVE for a table alias (a query with a join)."""
    return f"not exists (select 1 from orion_mem_trash t where t.memory_id = {alias}.id)"


async def _migrate(conn) -> None:
    """The numbered schema steps (docs/hafiza-tasarimi.md §16), each after a dump of the memory tables when there are
    rows to keep (memory_backup). Additive only (columns, tables, triggers; nothing is dropped or rewritten) and
    in one transaction: a step that fails leaves the schema as it was and runs again at the next start. A failed
    dump is logged and the step still runs: it cannot lose a row, and refusing it would leave the memory unusable."""
    version = int(await conn.fetchval("select value from orion_memory_meta where key = 'schema_version'") or 1)
    if version >= SCHEMA_VERSION:
        return
    if await conn.fetchval("select exists (select 1 from orion_memory)"):
        from orion.kernel import memory_backup
        await memory_backup.backup(f"schema-{version}-to-{SCHEMA_VERSION}")
    async with conn.transaction():
        if version < 2:
            await _schema_2(conn)
        if version < 3:
            await _schema_3(conn)
        if version < 4:
            await _schema_4(conn)
        if version < 5:
            await _schema_5(conn)
        if version < 6:
            await _schema_6(conn)
        if version < 7:
            await _schema_7(conn)
        if version < 8:
            await _schema_8(conn)
        if version < 9:
            await _schema_9(conn)
        if version < 10:
            await _schema_10(conn)
        if version < 11:
            await conn.execute("alter table orion_mem_fact add column if not exists since_precision text")
        if version < 12:
            from orion.kernel import memory_events
            logger.info("Memory: %d event texts rendered again", await memory_events.clean_existing(conn))
            await conn.execute("insert into orion_memory_meta (key, value) values ('events_reembed', '0') "
                               "on conflict (key) do update set value = '0'")  # The vectors follow in the background.
        await conn.execute("insert into orion_memory_meta (key, value) values ('schema_version', $1) "
                           "on conflict (key) do update set value = excluded.value", str(SCHEMA_VERSION))
    logger.info("Memory: schema %d -> %d", version, SCHEMA_VERSION)


async def _schema_10(conn) -> None:
    """The chats index is gone (user decision: memory belongs to "Asistanla konuş" only; normal chats are never indexed). Its
    windows (rows with source_kind 'chat_index', their trash entries and Sohbetler memberships), the Sohbetler collection, the
    index job table and the index columns of `orion_mem_chat_state` are deleted; the per-chat extraction state (extracted_upto,
    summary) stays. The dump before this step (memory_backup) holds everything that is dropped. The partial indexes of the
    windows are dropped by the background index build once the general ones exist."""
    await conn.execute("delete from orion_mem_trash t using orion_memory m where m.id = t.memory_id and m.source_kind = 'chat_index'")
    await conn.execute("delete from orion_mem_collection_item where collection_id = 'sohbetler'")
    await conn.execute("delete from orion_mem_collection where id = 'sohbetler'")
    await conn.execute("delete from orion_memory where source_kind = 'chat_index'")  # Text windows: no media, no wholes.
    await conn.execute("drop table if exists orion_mem_chat_index_jobs")
    for column in ("memory_enabled", "indexed_upto", "group_id", "item_id", "windows", "vector_space", "indexed_at"):
        await conn.execute(f"alter table orion_mem_chat_state drop column if exists {column}")
    await conn.execute("drop index if exists idx_orion_memory_chat")
    from orion.kernel import memory_events
    logger.info("Memory: %d event texts cleaned of time words", await memory_events.clean_existing(conn))
    await _relation_history_table(conn)
    await conn.execute("""
        create table if not exists orion_mem_remember (
            id bigserial primary key,
            user_id text not null,
            chat_id text not null,
            text text not null,                  -- what the user asked to remember, in their words
            created_at timestamptz not null default now(),
            done_at timestamptz)""")
    await conn.execute("create index if not exists idx_orion_mem_remember_open on orion_mem_remember (user_id, chat_id) "
                       "where done_at is null")
    # Chat compaction: messages up to `compacted_upto` are not sent to the model any more; `summary` is its first message.
    await conn.execute("alter table orion_mem_chat_state add column if not exists compacted_upto bigint")
    await conn.execute("alter table orion_mem_chat_state add column if not exists compacted_at timestamptz")
    # The profile's non-pinned part after the background model compacted it.
    await conn.execute("alter table orion_mem_profile add column if not exists compacted jsonb")
    await conn.execute("alter table orion_mem_profile add column if not exists compacted_at timestamptz")


async def _relation_history_table(conn) -> None:
    """A person's relation to the user changes over time (sevgilim -> eski sevgilim): one row per relation with its dates,
    like a fact's versions. `orion_mem_entity.relation` stays the current one. Entities that never changed have no row."""
    await conn.execute("""
        create table if not exists orion_mem_entity_relation (
            id bigserial primary key,
            user_id text not null,
            entity_id bigint not null references orion_mem_entity(id),
            relation text not null,
            valid_from date, valid_to date,   -- when it was true in the world (open: since / until unknown or still true)
            recorded_at timestamptz not null default now())""")
    await conn.execute("create index if not exists idx_orion_mem_entity_relation on orion_mem_entity_relation (user_id, entity_id)")


async def _schema_9(conn) -> None:
    """Albums, projects and the migration bucket "Eski hafıza" are gone (user decision), and Ben is no longer a membership
    (memory_collections.ben_sql defines it). Their collections and memberships are deleted; the memories themselves are never touched:
    they stay in the whole memory (a file also in Belgeler, an event in Yaşadıklarım, the chats' windows in Sohbetler)."""
    await conn.execute("delete from orion_mem_collection_item i where i.collection_id = 'ben' or exists (select 1 from "
                       "orion_mem_collection c where c.user_id = i.user_id and c.id = i.collection_id and c.kind <> 'builtin')")
    await conn.execute("delete from orion_mem_collection where kind <> 'builtin'")


async def _schema_2(conn) -> None:
    """The collections and the source links of facts and events (§5). A memory's main collection is its
    membership row with `main` (one per memory): kept per memory, not per vector row, so sorting a memory never
    rewrites the vector table (an update there re-inserts every row into the HNSW index). A memory with none is not
    sorted yet and is searched as before. Every table has user_id; every read filters by it."""
    await conn.execute("""
        create table if not exists orion_mem_collection (
            user_id text not null,
            id text not null,                 -- 'ben', 'yasadiklarim', 'sohbetler' or 'belgeler'
            kind text not null,               -- builtin (the only kind left: albums and projects are gone)
            name text not null,
            deleted_at timestamptz,
            created_at timestamptz not null default now(),
            primary key (user_id, id))""")
    await conn.execute("""
        create table if not exists orion_mem_collection_item (
            user_id text not null,
            collection_id text not null,
            group_id text not null,           -- the memory's group key (coalesce(group_id, 'r' || id))
            main boolean not null default false,
            position bigint,
            added_at timestamptz not null default now(),
            primary key (user_id, collection_id, group_id))""")
    await conn.execute("create unique index if not exists idx_orion_mem_collection_item_main on "
                       "orion_mem_collection_item (user_id, group_id) where main")
    await conn.execute("create index if not exists idx_orion_mem_collection_item_group on orion_mem_collection_item "
                       "(user_id, group_id)")
    await conn.execute("""
        create table if not exists orion_mem_source (
            id bigserial primary key,
            user_id text not null,
            target_type text not null,        -- fact | event
            target_id bigint not null,
            chat_id text, first_message bigint, last_message bigint,
            item_id text,                     -- taken from an item (a file, a note)
            by_user boolean not null default false,
            created_at timestamptz not null default now())""")
    await conn.execute("create index if not exists idx_orion_mem_source_chat on orion_mem_source (user_id, chat_id)")
    await conn.execute("create index if not exists idx_orion_mem_source_item on orion_mem_source (user_id, item_id)")
    await conn.execute("create index if not exists idx_orion_mem_source_target on orion_mem_source "
                       "(user_id, target_type, target_id)")
    # A new memory of a user who has collections (memory_v2 was turned on) is sorted by its first row: a file goes to
    # Belgeler, the rest stays unsorted for now; parts added later belong to their memory already. One rule for
    # every insert path. A user without collections costs one primary-key probe.
    await conn.execute("""
        create or replace function orion_memory_collection() returns trigger language plpgsql as $$
        begin
            if new.group_id is not null and new.source_kind = 'file'
               and exists (select 1 from orion_mem_collection where user_id = new.user_id)
               and not exists (select 1 from orion_memory where user_id = new.user_id and group_id = new.group_id) then
                insert into orion_mem_collection_item (user_id, collection_id, group_id, main)
                    values (new.user_id, 'belgeler', new.group_id, true) on conflict do nothing;
            end if;
            return new;
        end $$""")
    await conn.execute("drop trigger if exists orion_memory_collection on orion_memory")
    await conn.execute("create trigger orion_memory_collection before insert on orion_memory for each row "
                       "execute function orion_memory_collection()")


async def _schema_3(conn) -> None:
    """The trash: a removed row keeps everything and has an entry in `orion_mem_trash` (the removal it belongs to,
    `trash_id`: what one press removed, restored or deleted for good together, and when). It is a table of its own so
    that removing 1000 rows is one insert of 1000 small entries: updating a flag on `orion_memory` rows would write
    every one of them into every index again (HNSW, trigram: seconds). Reads skip such rows (LIVE); the counters do
    too (triggers on the trash table)."""
    await conn.execute(
        "create table if not exists orion_mem_trash (memory_id bigint primary key, user_id text not null, "
        "trash_id text not null, deleted_at timestamptz not null default now())")
    await conn.execute("create index if not exists idx_orion_mem_trash_entry on orion_mem_trash (user_id, trash_id)")
    await conn.execute("create index if not exists idx_orion_mem_trash_at on orion_mem_trash (user_id, deleted_at)")
    if await conn.fetchval("select to_regclass('orion_memory_pending') is not null"):
        await conn.execute("alter table orion_memory_pending add column if not exists trash_id text")


async def _schema_4(conn) -> None:
    """The chats index (§7.1): per chat what was indexed (`indexed_upto` = the last message looked at, the chat's one
    memory `group_id`/`item_id`) and the per-chat switch, and the one background job per user that indexes the old
    chats. Every table has user_id; every read filters by it. The windows themselves are rows of `orion_memory`
    (source_kind 'chat_index'), in the Sohbetler collection."""
    await conn.execute("""
        create table if not exists orion_mem_chat_state (
            user_id text not null,
            chat_id text not null,
            memory_enabled boolean not null default true,   -- false: this chat is neither indexed nor searched
            indexed_upto bigint,                            -- the last message id looked at
            group_id text, item_id text,
            windows integer not null default 0,
            vector_space text,
            indexed_at timestamptz,
            primary key (user_id, chat_id))""")
    await conn.execute("create index if not exists idx_orion_mem_chat_state_chat on orion_mem_chat_state (chat_id)")
    # One chat's windows (a search inside a chat compares only these; also the chat's delete and re-index): partial, so
    # it holds nothing until chats are indexed.
    await conn.execute("create index if not exists idx_orion_memory_chat on orion_memory (user_id, source_chat_id) "
                       "where source_kind = 'chat_index'")
    await conn.execute("""
        create table if not exists orion_mem_chat_index_jobs (
            user_id text primary key,
            state text not null,                 -- running | done | cancelled | failed
            total integer not null default 0,
            done integer not null default 0,
            windows integer not null default 0,
            failed integer not null default 0,
            message text,
            cancel boolean not null default false,
            started_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            finished_at timestamptz)""")


async def _schema_5(conn) -> None:
    """Entities and facts (§4, §5; memory_entities): the entities (`keys`: their folded name, relation and aliases, for exact
    matching), the bi-temporal facts (`lineage`: the first version's id, shared by every version), the record of entity
    merges (to undo them) and the proposals (the cards). Every table has user_id; every read filters by it. A fact's
    search vector is an ordinary `orion_memory` row (source_kind 'fact'): nothing changes in that table."""
    await conn.execute("""
        create table if not exists orion_mem_entity (
            id bigserial primary key,
            user_id text not null,
            kind text not null,                -- person | place | org | pet | thing | self
            name text not null,
            relation text,                     -- "annem" (to the user)
            aliases text[] not null default '{}',
            keys text[] not null default '{}', -- folded name, relation and aliases
            merged_into bigint references orion_mem_entity(id),
            deleted_at timestamptz,
            created_at timestamptz not null default now())""")
    await conn.execute("create index if not exists idx_orion_mem_entity_user on orion_mem_entity (user_id) "
                       "where merged_into is null and deleted_at is null")
    await conn.execute("""
        create table if not exists orion_mem_entity_merge (
            id bigserial primary key,
            user_id text not null,
            from_id bigint not null, into_id bigint not null,
            moved jsonb not null,              -- {"facts": [ids], "links": [], "aliases": [added]}
            merged_at timestamptz not null default now(),
            undone_at timestamptz)""")
    await conn.execute("create index if not exists idx_orion_mem_entity_merge_user on orion_mem_entity_merge "
                       "(user_id, into_id)")
    await conn.execute("""
        create table if not exists orion_mem_fact (
            id bigserial primary key,
            user_id text not null,
            entity_id bigint not null references orion_mem_entity(id),
            lineage bigint,                    -- the first version's id: one lineage = one fact over time
            category text not null,
            statement text not null,
            valid_from date, valid_to date,    -- when it was true in the world
            recorded_at timestamptz not null default now(),
            expired_at timestamptz,            -- when Orion stopped counting it (a newer version replaced it)
            superseded_by bigint references orion_mem_fact(id),
            sensitive boolean not null default false,   -- locked: no vector row, only word matches on request
            origin text not null,              -- user | model | extraction
            group_id text,                     -- its memory (vector row) in orion_memory; null for a locked fact
            deleted_at timestamptz)            -- a locked fact removed (others are removed through the trash)""")
    await conn.execute("create index if not exists idx_orion_mem_fact_entity on orion_mem_fact (user_id, entity_id) "
                       "where superseded_by is null")
    await conn.execute("create index if not exists idx_orion_mem_fact_lineage on orion_mem_fact (user_id, lineage)")
    await conn.execute("create index if not exists idx_orion_mem_fact_group on orion_mem_fact (user_id, group_id) "
                       "where group_id is not null")
    await conn.execute("""
        create table if not exists orion_mem_proposal (
            id bigserial primary key,
            user_id text not null,
            batch_id bigint,                   -- several proposals on one card
            action text not null,              -- add | supersede | merge_entities (more with step 6)
            payload jsonb not null,
            reason text,
            chat_id text, message_id bigint,
            state text not null default 'pending',   -- pending | claimed | accepted | edited | rejected
            created_at timestamptz not null default now(),
            decided_at timestamptz)""")
    await conn.execute("create index if not exists idx_orion_mem_proposal_user on orion_mem_proposal (user_id, state)")


async def _schema_6(conn) -> None:
    """Events and their links (§4, §5; memory_events): a dated experience (`occurred_on`..`occurred_end`, `precision`,
    what the user said: `said_as`), versioned like a fact (`lineage`; a correction is a new row), its search vector an
    ordinary `orion_memory` row (source_kind 'event', group `group_id`). `orion_mem_link` ties an event to its
    participants and place (entities) and to items such as photos (a memory's group key). A fact may repeat every year
    (`recurring` 'yearly:MM-DD'). Every table has user_id; every read filters by it."""
    await conn.execute("""
        create table if not exists orion_mem_event (
            id bigserial primary key,
            user_id text not null,
            lineage bigint,                    -- the first version's id: one lineage = one event over time
            title text not null,
            body text,
            occurred_on date not null,         -- the day (or the first day of the month / year / range)
            occurred_end date,                 -- the last day of a range, a month, a year; null for one day
            precision text not null default 'day',   -- day | month | year | approx
            said_as text,                      -- what was said: "bugün", "geçen yaz"
            mood text,
            recorded_at timestamptz not null default now(),
            expired_at timestamptz,
            superseded_by bigint references orion_mem_event(id),
            origin text not null,              -- user | model | extraction
            run_id bigint,
            group_id text not null)            -- its memory (vector row) in orion_memory""")
    await conn.execute("create index if not exists idx_orion_mem_event_day on orion_mem_event (user_id, occurred_on desc, id desc) "
                       "where superseded_by is null")
    await conn.execute("create index if not exists idx_orion_mem_event_lineage on orion_mem_event (user_id, lineage)")
    await conn.execute("create index if not exists idx_orion_mem_event_group on orion_mem_event (user_id, group_id)")
    await conn.execute("""
        create table if not exists orion_mem_link (
            user_id text not null,
            from_type text not null,           -- event
            from_id bigint not null,
            to_type text not null,             -- entity | item
            to_id text not null,               -- an entity's id, or an item's memory group key
            role text,                         -- katılan | yer | fotoğraf
            created_at timestamptz not null default now(),
            primary key (user_id, from_type, from_id, to_type, to_id))""")
    await conn.execute("create index if not exists idx_orion_mem_link_to on orion_mem_link (user_id, to_type, to_id)")
    await conn.execute("alter table orion_mem_fact add column if not exists recurring text")  # 'yearly:MM-DD'
    await conn.execute("create index if not exists idx_orion_mem_fact_recurring on orion_mem_fact (user_id) "
                       "where recurring is not null and superseded_by is null")


async def _schema_7(conn) -> None:
    """Extraction (§7.2, §7.4; memory_extract): one row per run over a chat's new messages (`orion_mem_extraction_run`: queued,
    running, done, cancelled, failed; what the pre-filter said; model, tokens, time; metadata only, never the text), the
    candidates a run produced (`orion_mem_candidate`: the normalised operation, bucket, unsupported-claim risk, duplicate /
    conflict status, the verdict the quality gate gives it and, in live mode, what was done), the per-user switch of
    automatic writing (`orion_mem_extraction_state`: off until the user turns it on) and the chat summary columns of
    `orion_mem_chat_state`. Facts remember the run that wrote them. Every table has user_id; every read filters by it."""
    await conn.execute("""
        create table if not exists orion_mem_extraction_run (
            id bigserial primary key,
            user_id text not null,
            chat_id text not null,
            first_message bigint, last_message bigint,       -- the messages this run covers (set when it starts)
            mode text not null default 'shadow',             -- shadow | live
            prefilter text,                                  -- passed | dropped:<reason>
            state text not null default 'queued',            -- queued | running | done | cancelled | failed
            summary_only boolean not null default false,     -- only the chat summary is remade (a message was deleted)
            model text, tokens_in integer, tokens_out integer,
            candidates integer not null default 0,
            error text,
            attempts integer not null default 0,
            retry_at timestamptz,                            -- backoff after a failure the next attempt may fix
            cancel boolean not null default false,
            created_at timestamptz not null default now(),
            started_at timestamptz, finished_at timestamptz,
            updated_at timestamptz not null default now(),
            duration_ms integer)""")
    await conn.execute("create index if not exists idx_orion_mem_run_user on orion_mem_extraction_run (user_id, id desc)")
    await conn.execute("create index if not exists idx_orion_mem_run_chat on orion_mem_extraction_run (user_id, chat_id, id desc)")
    await conn.execute("create index if not exists idx_orion_mem_run_open on orion_mem_extraction_run (state, id) "
                       "where state in ('queued', 'running')")
    await conn.execute("create unique index if not exists idx_orion_mem_run_one_queued on orion_mem_extraction_run "
                       "(user_id, chat_id) where state = 'queued'")
    await conn.execute("""
        create table if not exists orion_mem_candidate (
            id bigserial primary key,
            user_id text not null,
            run_id bigint not null references orion_mem_extraction_run(id) on delete cascade,
            chat_id text not null,
            first_message bigint, last_message bigint,       -- the source: user messages of the chat
            op jsonb not null,                               -- add_fact | add_event | supersede, normalised
            bucket text not null,                            -- direct | ambiguous | inferred (inferred is never written)
            unsupported_risk text,                           -- low | medium | high (the model's own call)
            dedupe text not null default 'new',              -- new | duplicate_of:<fact/event id> | conflicts_with:<fact id>
            merge_target bigint,                             -- the entity it was matched to (null: a new one)
            sensitive boolean not null default false,
            verdict text not null,                           -- would_write | would_card | would_skip
            reason text,                                     -- why (skip / card)
            outcome text,                                    -- written | carded | skipped | rejected_by_user (live only)
            written_type text, written_id bigint,            -- what live mode made
            created_at timestamptz not null default now())""")
    await conn.execute("create index if not exists idx_orion_mem_candidate_run on orion_mem_candidate (user_id, run_id)")
    await conn.execute("create index if not exists idx_orion_mem_candidate_user on orion_mem_candidate (user_id, id desc)")
    await conn.execute("""
        create table if not exists orion_mem_extraction_state (
            user_id text primary key,
            live boolean not null default false,             -- automatic writing (add-only); off until the user turns it on
            live_since timestamptz,
            updated_at timestamptz not null default now())""")
    for column in ("extracted_upto bigint", "summary text", "summary_upto bigint",
                   "summary_stale boolean not null default false", "summary_at timestamptz"):
        await conn.execute(f"alter table orion_mem_chat_state add column if not exists {column}")
    await conn.execute("alter table orion_mem_fact add column if not exists run_id bigint")


async def _schema_8(conn) -> None:
    """The profile block (§9.2; memory_profile): the lines the user pinned, and the last block built (for the page and the
    log; the block itself is rebuilt from the facts at every chat turn). One row per user."""
    await conn.execute("""
        create table if not exists orion_mem_profile (
            user_id text primary key,
            pinned jsonb not null default '[]',              -- [{"text": "...", "fact_id": 12 | null}]
            generated jsonb not null default '[]',           -- [{"fact_id": 31, "text": "..."}] as last built
            tokens integer, built_at timestamptz)""")


ANN_EF = 100  # hnsw.ef_search while a search runs (the default 40 returns too few rows once the user's filter applies).
ANN_MAX_SCAN = 20000  # hnsw.max_scan_tuples: how far an iterative scan walks the graph to fill a filtered query.


# What the page's summary counts, kept by statement triggers as rows come and go (+/-), so no write is followed by a
# recount of the user's rows. Three small tables: memories per (group, space), items per kind, contents per space.
_COUNTERS = (
    ("orion_memory_cnt_group", ("user_id", "g", "s"),
     ("{a}.user_id", "coalesce({a}.group_id, 'r' || {a}.id::text)", "{a}.vector_space"), "{live}"),
    ("orion_memory_cnt_groupall", ("user_id", "g"),
     ("{a}.user_id", "coalesce({a}.group_id, 'r' || {a}.id::text)"), "{live}"),
    ("orion_memory_cnt_item", ("user_id", "i", "kind"),
     ("{a}.user_id", "coalesce({a}.item_id, coalesce({a}.copied_from, {a}.id)::text)", "{a}.kind"),
     "not {a}.whole and {live}"),
    ("orion_memory_cnt_content", ("user_id", "c", "s"),
     ("{a}.user_id", "coalesce({a}.copied_from, {a}.id)", "{a}.vector_space"), "{live}"),
)
# Rows in the trash are not counted: an entry in orion_mem_trash moves them out of the counts (its triggers).
# What the page's summary reads: how many counter rows each user has with n > 0, per aggregate key. The counters above
# have a row per memory, so counting them at every read costs as much as the user has memories (seconds at millions,
# and every write makes the next read count again). The same statements that change a counter row change this small
# table too, only when the row crosses 0, so the summary is a read of a handful of rows however large the memory is.
_AGGREGATES = {
    "orion_memory_cnt_group": ("orion_memory_sum_space", ("user_id", "s")),
    "orion_memory_cnt_groupall": ("orion_memory_sum_total", ("user_id",)),
    "orion_memory_cnt_item": ("orion_memory_sum_kind", ("user_id", "kind")),
    "orion_memory_cnt_content": ("orion_memory_sum_content", ("user_id", "s")),
}
_COLUMN_TYPES = {"user_id": "text", "g": "text", "s": "text", "i": "text", "kind": "text", "c": "bigint"}
_COUNTED = ("group_id", "vector_space", "kind", "item_id", "copied_from", "whole")
COUNTERS_MARKER = "counters_built_5"  # The counts' definition changed (chat windows left out): filled again once.


def _count_sql(source: str, sign: int, other: str | None = None, trashed: bool = False) -> str:
    """plpgsql statements adding `sign` x the rows of transition table `source` to every counter table. `other`:
    the other transition table of an update; only rows whose counted columns changed are taken. Only rows not in the
    trash count; `trashed`: `source` is a query of rows whose trash entry is being made or removed (no check)."""
    join = ""
    if other:
        mine = ", ".join(f"r.{c}" for c in _COUNTED)
        theirs = ", ".join(f"o.{c}" for c in _COUNTED)
        join = f"join {other} o on o.id = r.id and ({mine}) is distinct from ({theirs})"
    sql = ""
    for table, names, exprs, where in _COUNTERS:
        columns = ", ".join(names)
        select = ", ".join(e.format(a="r") for e in exprs)
        group = ", ".join(str(i + 1) for i in range(len(names)))
        condition = where.format(a='r', live='true' if trashed else live('r'))
        aggregate = _AGGREGATES.get(table)
        if aggregate is None:
            sql += (f"insert into {table} ({columns}, n) select {select}, {sign} * count(*) from {source} r {join} "
                    f"where {condition} group by {group} order by {group} "
                    f"on conflict ({columns}) do update set n = {table}.n + excluded.n; ")
            continue
        # The deltas first, then the counter rows they change; where a row crosses 0 (a memory appears or its last row
        # goes) the aggregate follows. Keys are taken in order, so two writers never lock them crosswise.
        named = ", ".join(f"{e.format(a='r')} as {n}" for e, n in zip(exprs, names))
        target, keys = aggregate
        akeys = ", ".join(keys)
        ukeys = ", ".join('u.' + k for k in keys)
        dcols = ", ".join('d.' + c for c in names)
        crossed = ("case when u.new_n - d.dn <= 0 and u.new_n > 0 then 1 "
                   "when u.new_n - d.dn > 0 and u.new_n <= 0 then -1 else 0 end")
        sql += (f"with d as (select {named}, {sign} * count(*) as dn from {source} r {join} where {condition} "
                f"group by {group}), "
                f"up as (insert into {table} ({columns}, n) select {dcols}, d.dn from d order by {dcols} "
                f"on conflict ({columns}) do update set n = {table}.n + excluded.n "
                f"returning {', '.join(f'{table}.{c}' for c in names)}, {table}.n as new_n) "
                f"insert into {target} ({akeys}, n) select {ukeys}, sum({crossed}) "
                f"from up u join d on {' and '.join(f'd.{c} = u.{c}' for c in names)} group by {ukeys} "
                f"having sum({crossed}) <> 0 order by {ukeys} "
                f"on conflict ({akeys}) do update set n = {target}.n + excluded.n; ")
    return sql  # Rows that reach 0 stay (readers count n > 0; _counter_triggers purges them at start): a delete is cheaper.


def _bump(source: str) -> str:
    return (f"insert into orion_memory_version (user_id, n) select distinct r.user_id, 1 from {source} r "
            "on conflict (user_id) do update set n = orion_memory_version.n + 1; ")


async def memory_version(conn, user_id: str) -> int:
    """A per-user write counter, bumped by the same statement triggers that keep the counters: what the cached summary
    is valid for."""
    return int(await conn.fetchval("select n from orion_memory_version where user_id = $1", user_id) or 0)


async def _counter_triggers(conn) -> None:
    """The counter tables, their triggers and, once, their first fill from the rows that exist (marker
    'counters_built'). Insert, delete and update (only rows whose counted columns changed) adjust them per statement;
    truncate empties them."""
    await conn.execute("create table if not exists orion_memory_version (user_id text primary key, n bigint not null "
                       "default 0)")
    for table, names, _, _ in _COUNTERS:
        columns = ", ".join(f"{c} {_COLUMN_TYPES[c]} not null" for c in names)
        await conn.execute(f"create table if not exists {table} ({columns}, n integer not null, "
                           f"primary key ({', '.join(names)}))")
    for table, names in _AGGREGATES.values():
        columns = ", ".join(f"{c} {_COLUMN_TYPES[c]} not null" for c in names)
        await conn.execute(f"create table if not exists {table} ({columns}, n bigint not null, "
                           f"primary key ({', '.join(names)}))")
    # The per-space reads of the summary (contents of one space, joined to the active space's).
    await conn.execute("create index if not exists idx_orion_memory_cnt_content_space on orion_memory_cnt_content "
                       "(user_id, s, c)")
    bodies = {
        "insert": ("new table as new_rows", _count_sql("new_rows", 1) + _bump("new_rows")),
        "delete": ("old table as old_rows", _count_sql("old_rows", -1) + _bump("old_rows")),
        "update": ("old table as old_rows new table as new_rows",
                   _count_sql("old_rows", -1, "new_rows") + _count_sql("new_rows", 1, "old_rows") + _bump("new_rows")),
    }
    for event, (referencing, body) in bodies.items():
        await conn.execute(f"create or replace function orion_memory_count_{event}() returns trigger language plpgsql as "
                           f"$$ begin {body} return null; end $$")
        await conn.execute(f"drop trigger if exists orion_memory_count_{event} on orion_memory")
        await conn.execute(f"create trigger orion_memory_count_{event} after {event} on orion_memory "
                           f"referencing {referencing} for each statement execute function orion_memory_count_{event}()")
    # Entries in the trash move rows out of / back into the counts (set-based, per statement).
    trash_bodies = {
        "insert": ("new table as new_rows",
                   _count_sql("(select m.* from orion_memory m join new_rows n on n.memory_id = m.id)", -1, trashed=True)
                   + _bump("new_rows")),
        "delete": ("old table as old_rows",
                   _count_sql("(select m.* from orion_memory m join old_rows n on n.memory_id = m.id)", 1, trashed=True)
                   + _bump("old_rows")),
    }
    for event, (referencing, body) in trash_bodies.items():
        await conn.execute(f"create or replace function orion_mem_trash_count_{event}() returns trigger language plpgsql "
                           f"as $$ begin {body} return null; end $$")
        await conn.execute(f"drop trigger if exists orion_mem_trash_count_{event} on orion_mem_trash")
        await conn.execute(f"create trigger orion_mem_trash_count_{event} after {event} on orion_mem_trash "
                           f"referencing {referencing} for each statement execute function orion_mem_trash_count_{event}()")
    await conn.execute("create or replace function orion_memory_count_truncate() returns trigger language plpgsql as "
                       "$$ begin truncate orion_memory_cnt_group, orion_memory_cnt_groupall, orion_memory_cnt_item, "
                       "orion_memory_cnt_content, orion_memory_sum_space, orion_memory_sum_total, orion_memory_sum_kind, "
                       "orion_memory_sum_content; "
                       "update orion_memory_version set n = n + 1; "
                       "return null; end $$")
    await conn.execute("drop trigger if exists orion_memory_count_truncate on orion_memory")
    await conn.execute("create trigger orion_memory_count_truncate after truncate on orion_memory "
                       "for each statement execute function orion_memory_count_truncate()")
    for old in ("orion_memory_bump_insert", "orion_memory_bump_update", "orion_memory_bump_delete"):
        await conn.execute(f"drop trigger if exists {old} on orion_memory")
    for table in [c[0] for c in _COUNTERS] + [a[0] for a in _AGGREGATES.values()]:
        await conn.execute(f"delete from {table} where n <= 0")
    if await conn.fetchval("select value from orion_memory_meta where key = $1", COUNTERS_MARKER):
        return
    async with conn.transaction():
        await conn.execute("lock table orion_memory in share row exclusive mode")  # No write slips between the steps.
        for table, names, exprs, where in _COUNTERS:
            await conn.execute(f"truncate {table}")
            select = ", ".join(e.format(a="r") for e in exprs)
            group = ", ".join(str(i + 1) for i in range(len(names)))
            await conn.execute(f"insert into {table} ({', '.join(names)}, n) select {select}, count(*) "
                               f"from orion_memory r where {where.format(a='r', live=live('r'))} group by {group}")
            if table in _AGGREGATES:
                target, keys = _AGGREGATES[table]
                await conn.execute(f"truncate {target}")
                await conn.execute(f"insert into {target} ({', '.join(keys)}, n) select {', '.join(keys)}, count(*) "
                                   f"from {table} where n > 0 group by {', '.join(keys)}")
        await conn.execute("insert into orion_memory_meta (key, value) values ($1, 'yes') on conflict do nothing",
                           COUNTERS_MARKER)


INDEX_LOCK = 51730202
INDEX_DELAY = 5.0  # Seconds after the first use before the background index build starts.
_index_task = None


def _index_plan() -> list[tuple[str, str]]:
    plan = [("idx_orion_memory_item", "create index concurrently if not exists idx_orion_memory_item on orion_memory "
                                      "(user_id, item_id)"),
            ("idx_orion_memory_newest", "create index concurrently if not exists idx_orion_memory_newest on "
                                        "orion_memory (user_id, created_at desc, id desc)"),
            ("idx_orion_memory_hash", "create index concurrently if not exists idx_orion_memory_hash on orion_memory "
                                     "(user_id, vector_space, content_hash)"),
            ("idx_orion_memory_ann", "create index concurrently if not exists idx_orion_memory_ann on "
                                     "orion_memory using hnsw (embedding vector_cosine_ops)")]
    # The minority kinds get an index of their own: a Görsel search in a memory that is mostly text would otherwise
    # walk the text-filled graph for its few images.
    for kind in ("image", "audio", "video"):
        plan.append((f"idx_orion_memory_ann_{kind}", f"create index concurrently if not exists idx_orion_memory_ann_{kind} "
                                                     f"on orion_memory using hnsw (embedding vector_cosine_ops) "
                                                     f"where kind = '{kind}'"))
    # "Son eklenenler" (memory_recent): what the assistant wrote itself, newest first. Tiny partial indexes, so the list does
    # not walk a big memory looking for the few rows that are not the user's own.
    plan.append(("idx_orion_memory_assistant", "create index concurrently if not exists idx_orion_memory_assistant on "
                                               "orion_memory (user_id, created_at desc, id desc) "
                                               "where source_kind = 'assistant'"))
    plan.append(("idx_orion_mem_fact_auto", "create index concurrently if not exists idx_orion_mem_fact_auto on "
                                            "orion_mem_fact (user_id, recorded_at desc, id desc) where origin <> 'user'"))
    plan.append(("idx_orion_mem_event_auto", "create index concurrently if not exists idx_orion_mem_event_auto on "
                                             "orion_mem_event (user_id, recorded_at desc, id desc) where origin <> 'user'"))
    if _trgm:
        plan.append(("idx_orion_memory_folded_trgm", "create index concurrently if not exists "
                                                     "idx_orion_memory_folded_trgm on orion_memory "
                                                     "using gin (folded gin_trgm_ops)"))
        plan.append(("idx_orion_memory_cfolded_trgm", "create index concurrently if not exists "
                                                      "idx_orion_memory_cfolded_trgm on orion_memory "
                                                      "using gin (caption_folded gin_trgm_ops)"))
    return plan


def _schedule_index_build() -> None:
    """The big indexes (HNSW, trigram, hash) are built in the background after the Hub is up, with CREATE INDEX
    CONCURRENTLY (reads and writes go on; searches are exact scans until an index is ready). Once per process."""
    global _index_task
    import asyncio
    if _index_task is not None:
        return
    try:
        _index_task = asyncio.get_running_loop().create_task(_build_indexes(), name="memory-index-build")
    except RuntimeError:
        pass


async def _index_progress(name: str) -> None:
    import asyncio
    conn = await _connect()
    if conn is None:
        return
    try:
        while True:
            await asyncio.sleep(30)
            row = await conn.fetchrow(
                "select phase, blocks_done, blocks_total, tuples_done, tuples_total from pg_stat_progress_create_index "
                "where relid = 'orion_memory'::regclass limit 1")
            if row:
                logger.info("Memory: index %s: %s (blocks %s/%s, tuples %s/%s)", name, row["phase"],
                            row["blocks_done"], row["blocks_total"], row["tuples_done"], row["tuples_total"])
    except Exception:
        pass
    finally:
        await conn.close()


async def _build_indexes() -> None:
    import asyncio
    import time
    await asyncio.sleep(INDEX_DELAY)
    conn = await _connect()
    if conn is None:
        return
    try:
        if not await conn.fetchval("select pg_try_advisory_lock($1)", INDEX_LOCK):
            return  # Another Hub process is building them.
        try:
            await conn.execute("set maintenance_work_mem = '256MB'")
            for name, ddl in _index_plan():
                state = await conn.fetchrow("select i.indisvalid from pg_index i join pg_class c on c.oid = i.indexrelid "
                                            "where c.relname = $1", name)
                if state and state["indisvalid"]:
                    continue
                if state:  # A build that was cut off leaves an invalid index behind.
                    await conn.execute(f"drop index concurrently if exists {name}")
                logger.info("Memory: building index %s in the background", name)
                started, watcher = time.monotonic(), asyncio.create_task(_index_progress(name))
                try:
                    await conn.execute(ddl)
                except Exception as exc:
                    logger.warning("Memory: index %s could not be built (%s); searches stay exact scans", name, exc)
                    continue
                finally:
                    watcher.cancel()
                logger.info("Memory: index %s ready after %.0fs", name, time.monotonic() - started)
                if name == "idx_orion_memory_folded_trgm":  # The earlier expression indexes are not used any more.
                    for old in await conn.fetch("select c.relname from pg_class c where c.relkind = 'i' and "
                                                "c.relname ~ '^idx_orion_memory_trgm'"):
                        await conn.execute(f'drop index concurrently if exists "{old["relname"]}"')
            # The partial indexes of the removed chats index go once their general replacements are valid.
            for new, olds in (("idx_orion_memory_ann", ("idx_orion_memory_ann_main", "idx_orion_memory_ann_chats")),
                              ("idx_orion_memory_newest", ("idx_orion_memory_newest_plain", "idx_orion_memory_newest_chats",
                                                           "idx_orion_memory_user_plain", "idx_orion_memory_user_chats"))):
                if await conn.fetchval("select i.indisvalid from pg_index i join pg_class c on c.oid = i.indexrelid "
                                       "where c.relname = $1", new):
                    for old in olds:
                        await conn.execute(f"drop index concurrently if exists {old}")
        finally:
            await conn.execute("reset maintenance_work_mem")
            await conn.execute("select pg_advisory_unlock($1)", INDEX_LOCK)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info("Memory: background index build stopped: %s", exc)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


_iterative_scan: bool | None = None  # Whether this pgvector knows hnsw.iterative_scan (found out once, by the first search).


async def ann_fetch(conn, sql: str, *args) -> list:
    """`conn.fetch` of a nearest-neighbour query with the ANN settings of this query only (transaction local, so a
    pooled connection keeps no setting): a larger ef_search and, where pgvector has it, iterative scan so the
    user's filter cannot leave the result short. Three round trips around the query (begin, settings, commit): the
    first search of the process finds out whether pgvector has iterative scan (inside a savepoint, an older one
    refuses it); the rest ask for it in the same statement as ef_search, or leave it out."""
    global _iterative_scan
    async with conn.transaction():
        if _iterative_scan:
            await conn.execute(f"select set_config('hnsw.ef_search', '{ANN_EF}', true), "
                               "set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                               f"set_config('hnsw.max_scan_tuples', '{ANN_MAX_SCAN}', true)")
        elif _iterative_scan is False:
            await conn.execute(f"select set_config('hnsw.ef_search', '{ANN_EF}', true)")
        else:
            await conn.execute(f"select set_config('hnsw.ef_search', '{ANN_EF}', true)")
            try:
                async with conn.transaction():  # A savepoint: older pgvector does not know these settings.
                    await conn.execute("select set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                                       f"set_config('hnsw.max_scan_tuples', '{ANN_MAX_SCAN}', true)")
                _iterative_scan = True
            except Exception as exc:
                if getattr(exc, "sqlstate", None) == "42704":  # unrecognized configuration parameter: this pgvector lacks it
                    _iterative_scan = False  # Any other failure: ask again next time.
        return await conn.fetch(sql, *args)


async def exact_fetch(conn, sql: str, *args) -> list:
    """`conn.fetch` of a nearest-neighbour query WITHOUT the vector index (transaction local): an exact scan, for the
    rare search whose filtered index scan ran out before the filter's rows did."""
    async with conn.transaction():
        await conn.execute("select set_config('enable_indexscan', 'off', true), "
                           "set_config('enable_bitmapscan', 'off', true)")
        return await conn.fetch(sql, *args)


async def _enable_trigrams(conn) -> None:
    """pg_trgm ships with PostgreSQL and is a trusted extension, so the Hub's own role may create it. Without it the
    near-spelling search runs in Python."""
    global _trgm
    try:
        if not await conn.fetchval("select 1 from pg_extension where extname = 'pg_trgm'"):
            await conn.execute("create extension if not exists pg_trgm")
        _trgm = True
    except Exception as exc:
        logger.info("pg_trgm unavailable, near spellings are matched in Python: %s", exc)
        _trgm = False


async def _connect():
    conn = await registry._connect()
    if conn is None:
        raise MemoryUnavailable("Hafıza şu an kullanılamıyor: veritabanına ulaşılamadı.")
    return conn


async def embedding_targets(settings) -> list[tuple[object, str, bool]]:
    """(settings aimed at the model, its vector space, whether it is local) of every embedding model the Router
    lists, the one searches use first (a local one, else the first)."""
    from orion.kernel.router_models import get_router_catalog
    try:
        models = [m for m in (await get_router_catalog(include_voices=False))["models"] if m.get("capability") == "embed"]
    except Exception as exc:
        raise MemoryUnavailable(ROUTER_DOWN_MESSAGE) from exc  # No catalog: the Router itself cannot be reached.
    if not models:
        raise MemoryUnavailable(SETUP_MESSAGE)
    models.sort(key=lambda m: m.get("provider") != "local")
    found = []
    for chosen in models:
        local = chosen.get("provider") == "local"
        if local:
            from services.shared import local_parts
            space = local_parts.embedding_profile_cached().get("vector_space") or "local"
        else:
            space = f"cloud:{chosen['name']}"
        found.append((settings.model_copy(update={"router_model_group": chosen["name"]}), space, local))
    return found


async def embedding_target(settings, space: str | None = None) -> tuple[object, str, bool]:
    """(settings aimed at the embedding model, its vector space, whether it is local): the one searches and new
    memories use, or, with `space`, the model of that vector space (MemoryUnavailable when the Router has none:
    its query vector cannot be made, the page then searches by words only)."""
    targets = await embedding_targets(settings)
    if space:
        found = next((t for t in targets if t[1] == space), None)
        if found is None:
            raise MemoryUnavailable(f"{space} modeli şu an kullanılamıyor: bu modelle anlamla aranamıyor.")
        return found
    return targets[0]


async def _embed(text: str, settings, kind: str, local: bool) -> list[list[float]]:
    from orion.worker.services.router import embed_text
    try:
        vectors = await embed_text(text, settings, kind, local=local)
    except Exception as exc:
        logger.warning("Memory embedding failed: %s", exc)
        if unreachable(exc):
            raise MemoryUnavailable(ROUTER_DOWN_MESSAGE) from exc
        raise MemoryUnavailable(SETUP_MESSAGE if local else f"Embedding alınamadı: {exc}") from exc
    if any(len(v) != 768 for v in vectors):
        raise MemoryUnavailable("Embedding modeli 768 boyutlu değil; hafıza şimdilik yalnız 768 boyutlu modelleri destekler.")
    return vectors


def new_group() -> str:
    import uuid
    return uuid.uuid4().hex


def local_time(value) -> str | None:
    """A stored time as this PC's local date and time with its UTC offset: '2026-11-15 14:51 (UTC+03:00)'."""
    if value is None:
        return None
    local = value.astimezone()
    offset = local.strftime("%z") or "+0000"
    return f"{local:%Y-%m-%d %H:%M} (UTC{offset[:3]}:{offset[3:5]})"


async def join_group(conn, user_id: str, group: str | None) -> str:
    """The group new parts go into: a new memory, or this user's memory `group` (one saved before groups, "r<id>",
    gets a group first). LookupError for a memory this user does not have."""
    if not group:
        return new_group()
    group = str(group)[:64]
    if group.startswith("r") and group[1:].isdigit():
        row_id = int(group[1:])
        if not await conn.fetchval("select 1 from orion_memory where user_id = $1 and id = $2 and group_id is null "
                                   f"and {LIVE}", user_id, row_id):
            raise LookupError(group)
        fresh = new_group()
        await conn.execute("update orion_memory set group_id = $3 where user_id = $1 and group_id is null "
                           "and (id = $2 or copied_from = $2)", user_id, row_id, fresh)
        return fresh
    if not await conn.fetchval(f"select 1 from orion_memory where user_id = $1 and group_id = $2 and {LIVE} limit 1",
                               user_id, group):  # A memory in the trash takes no new parts.
        raise LookupError(group)
    return group


CONTENT_KEY = "coalesce(copied_from, id)"


def _vector(values) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _is_text_file(item) -> bool:
    return (isinstance(item, dict) and item.get("isText") and isinstance(item.get("data"), str)
            and not item["data"].startswith("data:"))


def memory_message_text(message: dict) -> str | None:
    """A message as the transcript keeps it: what was typed and the names of its attachments; a text file's content
    is its own part of the memory (chat_text_files), never inlined (it can be huge)."""
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not any(_is_text_file(a) for a in attachments):
        return message_text(message)
    trimmed = {**message, "attachments": [{k: v for k, v in a.items() if k != "data"} if _is_text_file(a) else a
                                           for a in attachments]}
    return message_text(trimmed)


def chat_text_files(messages: list[dict], ids: list | None = None) -> list[dict]:
    """The text files the user attached in the chat, once each by content: {name, text, message_id}."""
    import hashlib
    found, seen = [], set()
    for index, message in enumerate(messages):
        if message.get("role") != "user":
            continue
        for item in message.get("attachments") or []:
            if not _is_text_file(item):
                continue
            text = item["data"].replace("\x00", "").strip()
            key = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
            if text and key not in seen:
                seen.add(key)
                found.append({"name": " ".join(str(item.get("name") or "Metin").split())[:200], "text": text,
                              "message_id": ids[index] if ids else None})
    return found


def chat_paragraphs(messages: list[dict], ids: list | None = None) -> list[tuple[object, str]]:
    """(message id, paragraph) of each readable user and assistant message; ids are None without `ids`."""
    paragraphs = []
    for index, message in enumerate(messages):
        text = memory_message_text(message)
        if text:
            paragraphs.append((ids[index] if ids else None,
                               f"{'Kullanıcı' if message.get('role') == 'user' else 'Asistan'}: {text}"))
    return paragraphs


def chat_text(messages: list[dict]) -> str:
    """The readable user and assistant text of a chat, one paragraph per message."""
    return "\n\n".join(text for _, text in chat_paragraphs(messages))


async def _fold_columns(conn) -> None:
    """`folded` (and `caption_folded`): the words of a row folded by fold(), kept for the trigram indexes. A trigger
    fills it for a row stored without one; rows from before it existed are folded here once, in batches (marker)."""
    await conn.execute(
        "create or replace function orion_memory_fold() returns trigger language plpgsql as $$ begin "
        f"if new.folded is null then new.folded := {FOLD_FALLBACK_SQL.format(col='new.text')}; end if; "
        "if new.caption_folded is null and new.caption is not null then "
        f"new.caption_folded := {FOLD_FALLBACK_SQL.format(col='new.caption')}; end if; return new; end $$")
    await conn.execute("drop trigger if exists orion_memory_fold on orion_memory")
    await conn.execute("create trigger orion_memory_fold before insert on orion_memory for each row "
                       "execute function orion_memory_fold()")
    if await conn.fetchval("select value from orion_memory_meta where key = 'folded_v2'"):
        return
    last = 0
    while True:
        rows = await conn.fetch("select id, text, caption, source_kind, source_chat_title from orion_memory "
                                "where id > $1 order by id limit 2000", last)
        if not rows:
            break
        last = rows[-1]["id"]
        await conn.executemany("update orion_memory set folded = $2, caption_folded = $3 where id = $1",
                               [(r["id"], fold(row_match_text({**r, "text": r["text"]})), None if r["caption"] is None else fold(r["caption"]))
                                for r in rows])
    await conn.execute("insert into orion_memory_meta (key, value) values ('folded_v2', 'yes') on conflict do nothing")


async def backfill_message_last(conn) -> int:
    """Once: the last message of each stored chat piece, for rows saved before it was kept. A piece runs to the message
    before the next piece's start (same item and space); the item's last piece to the chat's last message at save time.
    Returns rows updated (0 when it has run)."""
    if await conn.fetchval("select value from orion_memory_meta where key = 'message_last_backfilled'"):
        return 0
    done = await conn.fetchval(
        """with w as (select id, source_chat_id, source_message_id, created_at,
                             lead(source_message_id) over (partition by user_id, item_id, vector_space
                                                           order by item_order, coalesce(copied_from, id)) as next_start
                      from orion_memory where source_kind = 'chat_save' and kind = 'text' and not whole
                      and source_chat_id is not null and source_message_id is not null and item_id is not null),
                u as (update orion_memory m set source_message_last = greatest(w.source_message_id, coalesce(
                          (select max(x.id) from orion_chat_messages x where x.chat_id = w.source_chat_id
                           and case when w.next_start is not null then x.id < w.next_start
                                    else x.created_at <= w.created_at end), w.source_message_id))
                      from w where m.id = w.id and m.source_message_last is null returning 1)
           select count(*) from u""")
    await conn.execute("insert into orion_memory_meta (key, value) values ('message_last_backfilled', 'yes') "
                       "on conflict do nothing")
    return int(done or 0)


async def _chat_messages(chat_id: str) -> tuple[list[dict], list[int]]:
    """The chat's messages in order with their row ids (the anchors a saved row points back at)."""
    import json
    conn = await _connect()
    try:
        await registry._ensure_tables(conn)
        rows = await conn.fetch("select id, content_json from orion_chat_messages where chat_id = $1 order by id",
                                chat_id)
    finally:
        await conn.close()
    messages, ids = [], []
    for row in rows:
        try:
            messages.append(json.loads(row["content_json"]))
            ids.append(row["id"])
        except ValueError:
            continue
    return messages, ids


AUDIO_PIECE_SECONDS = 60  # Gemma's mmproj: 25 tokens/s, so a window fits the default 2048 context.
NOUNS = {"image": ("görsel", "görseller"), "audio": ("ses kaydı", "sesler"), "video": ("video", "videolar")}
NO_MEDIA = {"has_extra": False, "extra_on": False, "extra_kinds": [], "image": False, "audio": False}
MEDIA_KINDS = ("image", "audio", "video")


def media_kind(mime: str) -> str | None:
    mime = (mime or "").lower()
    for kind in MEDIA_KINDS:
        if mime.startswith(kind + "/"):
            return kind
    return None


def chat_media(messages: list[dict], ids: list | None = None) -> list[dict]:
    """Images, audio and video the user sent in the chat (attachments and media parts), once each by media id. The
    assistant's messages, its spoken replies (TTS) included, are never taken."""
    found, seen = [], set()
    for index, message in enumerate(messages):
        if message.get("role") != "user":
            continue
        shown = message.get("display_text")
        content = message.get("content")
        text = (shown if isinstance(shown, str) else content if isinstance(content, str) else "") or ""
        items = [item for item in message.get("attachments") or [] if isinstance(item, dict)]
        if isinstance(content, list):
            items += [part["attachment"] for part in content if isinstance(part, dict)
                      and part.get("type") == "hub_media" and isinstance(part.get("attachment"), dict)]
        for item in items:
            media_id, mime = item.get("media_id"), str(item.get("mime_type") or "").lower()
            kind = media_kind(mime)
            if not kind or not isinstance(media_id, str) or media_id in seen:
                continue
            seen.add(media_id)
            found.append({"kind": kind, "media_id": media_id, "mime": mime, "name": item.get("name") or "",
                          "text": text.strip()[:300], "message_id": ids[index] if ids else None})
    return found


def media_support(local: bool, model_name: str) -> dict:
    """What the active embedding model takes now (local_parts.embedding_media); a cloud model takes no media. A
    video is its frames (images) and, when the model hears, its sound."""
    support = {"model": model_name, **NO_MEDIA}
    if local:
        from services.shared import local_parts
        try:
            support = local_parts.embedding_media()
        except Exception as exc:
            logger.warning("Embedding media support unknown: %s", exc)
    return {**support, "video": bool(support.get("image"))}


def skip_warning(kind: str, count: int, support: dict) -> str:
    noun, many = NOUNS[kind]
    head = f"Sohbette {count} {noun} var ama"
    needs = "audio" if kind == "audio" else "image"
    if support["has_extra"] and needs in support["extra_kinds"] and not support["extra_on"]:
        return (f"{head} {support['model']} modelinin ek dosyası kapalı; {many} kaydedilmedi. "
                "Görsel/ses için modelin ek dosyasını aç (Bileşenler → Hafıza).")
    sense, label = ("sesi", "Ses") if kind == "audio" else ("görselleri", "Görsel/video")
    return (f"{head} {support['model']} {sense} anlamıyor; {many} kaydedilmedi. "
            f"{label} için EmbeddingGemma 2'yi ek dosyasıyla seç.")


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def span_label(start, end) -> str:
    return f"{clock(start)}–{clock(end)}"


def embedding_context() -> int | None:
    """The local embedding server's context (tokens), None when unknown (a cloud model)."""
    try:
        from services.shared import local_parts
        return int(local_parts.embedding_profile_cached().get("context") or 0) or None
    except Exception:
        return None


def audio_pieces(raw: bytes, piece_seconds: int = AUDIO_PIECE_SECONDS) -> tuple[list[bytes], float]:
    """Decode any audio PyAV reads and cut it into 16 kHz mono WAV pieces of at most `piece_seconds`, without
    overlap; returns (pieces, total seconds). Memory rows use audio_windows."""
    from orion.kernel import memory_pieces as mp
    pcm = mp.decode_pcm(raw)
    step = piece_seconds * mp.SAMPLE_RATE * 2
    pieces = [mp.wav_bytes(pcm[start:start + step]) for start in range(0, len(pcm), step)
              if len(pcm[start:start + step]) >= mp.SAMPLE_RATE // 5]  # A tail under 0.1 s says nothing.
    return pieces, len(pcm) / (mp.SAMPLE_RATE * 2)


def audio_windows(raw: bytes, window: float | None = None) -> tuple[list[tuple[object, bytes]], float]:
    """(span, WAV of its window) for each piece of an audio (memory_pieces.spans); returns (pieces, seconds)."""
    from orion.kernel import memory_pieces as mp
    pcm = mp.decode_pcm(raw)
    seconds = len(pcm) / (mp.SAMPLE_RATE * 2)
    if seconds < 0.1:
        return [], seconds
    window = window or mp.audio_window()
    return [(span, mp.wav_bytes(mp.pcm_slice(pcm, span.window_start, span.window_end)))
            for span in mp.spans(seconds, window)], seconds


async def _embed_media(parts: list[dict], target) -> list[float]:
    from orion.worker.services.router import generate_embeddings
    vector = await generate_embeddings(parts, target)  # Media take no prefix (EmbeddingGemma 2: text only).
    if not vector or len(vector) != 768:
        raise ValueError("Embedding modeli 768 boyutlu vektör döndürmedi")
    return vector


async def _read_chat_media(chat_id: str, media_id: str) -> tuple[str, bytes]:
    from orion.kernel.media_store import read_media
    return await read_media(chat_id, media_id)


def media_name(item: dict) -> str:
    name = item.get("name") or ""
    return "" if name.lower().startswith(("ses", "audio", "recording", "voice")) else name


async def media_pieces(item: dict, mime: str, raw: bytes, target) -> list[dict]:
    """The pieces of one image or audio file: [{"text", "vector", "span_start", "span_end"}] (one for an image; an
    audio's overlapping windows, memory_pieces). Videos are embedded by memory_media_jobs."""
    import asyncio
    from orion.worker.services.router import audio_part, image_part
    tail = f" — {item['text']}" if item.get("text") else ""
    if item["kind"] == "image":
        return [{"text": (item.get("name") or "Görsel") + tail,
                 "vector": await _embed_media([image_part(raw, mime)], target)}]
    windows, seconds = await asyncio.to_thread(audio_windows, raw)
    if not windows:
        raise ValueError("ses çözülemedi")
    name = media_name(item)
    base = f"Ses kaydı {clock(seconds)}" + (f" ({name})" if name else "")
    pieces = []
    for span, wav in windows:
        part = f" {span_label(span.start, span.end)}" if len(windows) > 1 else ""
        pieces.append({"text": base + part + tail, "vector": await _embed_media([audio_part(wav, "wav")], target),
                       "span_start": span.start, "span_end": span.end, "seconds": seconds})
    return pieces


def whole_label(item: dict, pieces: list[dict]) -> str:
    """The label of an item's whole vector: the media's label without the piece's times, or the text's title."""
    kind = item["kind"]
    if kind == "text":
        return item.get("title") or "Metnin tamamı"
    seconds = next((p.get("seconds") for p in pieces if p.get("seconds")), None)
    name = media_name(item)
    noun = "Ses kaydı" if kind == "audio" else "Video"
    tail = f" — {item['text']}" if item.get("text") else ""
    return f"{noun} {clock(seconds or 0)}" + (f" ({name})" if name else "") + tail


ROW_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title, kind, "
    "media_id, media_mime, media_seconds, group_id, source_chat_id, source_message_id, item_id, whole, span_start, "
    "span_end, track, caption, item_order, source_message_last, folded, caption_folded, lossless) "
    "values ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, "
    "$22, $23, $24) "
    "returning id")


def item_order() -> int:
    """Where a new item stands in its memory: the time it was asked for (ms), so a video embedded in the background
    keeps its place among the parts added after it."""
    import time
    return int(time.time() * 1000)


async def insert_item(conn, user_id: str, space: str, group: str, item: dict, pieces: list[dict]) -> int:
    """Store one item's pieces and, for more than one piece of a track, its whole vector (the normalized mean of
    the pieces'), inside the caller's transaction; media rows are linked to their file. Returns the rows stored.
    `item`: kind, source_kind, title, media_id, mime, chat_id, message_id, caption, item_id, order;
    a piece: text, vector, span_start, span_end, track, seconds."""
    from orion.kernel.media_store import link_media
    from orion.kernel.memory_pieces import mean_vector
    item_id = item.get("item_id") or new_group()
    order = item.get("order") or item_order()
    rows = list(pieces)
    tracks: dict = {}
    for piece in pieces:
        tracks.setdefault(piece.get("track"), []).append(piece)
    for track, members in tracks.items():
        if len(members) > 1:
            rows.append({"text": whole_label(item, members), "vector": mean_vector([m["vector"] for m in members]),
                         "track": track, "whole": True, "seconds": members[0].get("seconds")})
    stored = 0
    for piece in rows:
        text = piece["text"][:1000] if item["kind"] != "text" or piece.get("whole") else piece["text"]
        caption = item.get("caption") or None
        row_id = await conn.fetchval(
            ROW_SQL, user_id, text,
            space, _vector(piece["vector"]), item.get("source_kind") or "manual", item.get("title") or None,
            item["kind"], item.get("media_id"), item.get("mime"), piece.get("seconds"), group, item.get("chat_id"),
            piece.get("message_id", item.get("message_id")), item_id, bool(piece.get("whole")), piece.get("span_start"), piece.get("span_end"),
            piece.get("track"), caption, order, piece.get("message_last", item.get("message_last")), fold(match_text(text, item.get("title"), caption, item.get("source_kind") or "manual")),
            None if caption is None else fold(caption), item["kind"] == "text" and not piece.get("whole"))
        if item.get("media_id"):
            await link_media(item["media_id"], "memory", str(row_id), conn=conn)
        stored += 1
    return stored


async def reuse_item(conn, user_id: str, space: str, group: str, media_id: str, name: str, caption: str,
                     source_kind: str = "file", chat_id: str | None = None) -> int:
    """The same file already in another of this user's memories (in this vector space): its rows (vectors, video
    tracks, whole vectors) copied into `group` as a new item, nothing embedded again, the one media copy linked.
    Returns rows copied, 0 when no memory has it (or a video is still being embedded)."""
    from orion.kernel.media_store import link_media
    source = await conn.fetchval(
        "select item_id from orion_memory where user_id = $1 and vector_space = $2 and media_id = $3 "
        "and item_id is not null order by id limit 1", user_id, space, media_id)
    if not source:
        return 0
    rows = await conn.fetch(
        "select text, embedding::text as embedding, kind, media_mime, media_seconds, whole, span_start, span_end, "
        "track from orion_memory where user_id = $1 and vector_space = $2 and item_id = $3 and media_id = $4 "
        "order by id", user_id, space, source, media_id)
    item_id, order = new_group(), item_order()
    for row in rows:
        row_id = await conn.fetchval(
            ROW_SQL, user_id, row["text"], space, row["embedding"], source_kind, name or None, row["kind"], media_id,
            row["media_mime"], row["media_seconds"], group, chat_id, None, item_id, row["whole"], row["span_start"],
            row["span_end"], row["track"], caption or None, order, None,
            fold(match_text(row["text"], name, caption, source_kind)), fold(caption) if caption else None, False)
        await link_media(media_id, "memory", str(row_id), conn=conn)
    return len(rows)


async def text_item_pieces(conn, user_id: str, space: str, text: str, target, local: bool,
                           skip_known: bool = True, group: str | None = None, split: list | None = None,
                           progress=None) -> tuple[list[dict], int]:
    """The pieces of a text (memory_pieces.text_pieces: each core embedded with its window). A core already in
    the memory `group` is left out; one this user has in another memory takes that row's vector (not embedded
    again, the text relates the two memories). Returns (pieces, cores left out). `split`: text_pieces(text) made
    already (in a thread: a long text takes a moment). `progress(done, total)`: awaited after each piece; True stops
    (the pieces so far are returned with `stopped`)."""
    import asyncio
    import json
    from orion.kernel.memory_pieces import text_pieces
    pieces, known = [], 0
    if split is None:
        split = await asyncio.to_thread(text_pieces, text)
    seen: dict[str, dict] = {}
    if skip_known and split:
        import hashlib
        hashes = sorted({hashlib.sha256(core.encode("utf-8")).hexdigest() for core, _ in split})
        # One lookup for every core (index user, space, content_hash): the row of this group first, else any.
        for r in await conn.fetch(
                "select distinct on (content_hash) content_hash, text, embedding::text as embedding, "
                f"case when {LIVE} then group_id end as group_id "
                "from orion_memory where user_id = $1 and vector_space = $2 and content_hash = any($3::text[]) "
                "and media_id is null and not whole order by content_hash, "
                f"(group_id = $4 and {LIVE}) desc nulls last, id",  # A removed copy is not "known".
                user_id, space, hashes, group or ""):
            seen[r["text"]] = dict(r)
    for index, (core, window) in enumerate(split):
        if progress is not None and await progress(index, len(split)):
            raise asyncio.CancelledError("stopped")
        if skip_known:
            row = seen.get(core)
            if row and group and row["group_id"] == group:
                known += 1
                continue
            if row and group:
                pieces.append({"text": core, "vector": json.loads(row["embedding"]), "split_index": index})
                continue
            if row:
                known += 1
                continue
        for vector in await _embed(window, target, "document", local):
            pieces.append({"text": core, "vector": vector, "split_index": index})
            break  # A window longer than the context is cut by embed_text; its first part stands for it.
    return pieces, known


async def _save_media(conn, user_id, chat_id, title, items, space, target, support,
                      group: str, settings=None) -> tuple[int, dict, list[str], list[dict]]:
    """Images and audio of a chat save, now; videos as background jobs (memory_media_jobs). Returns
    (rows stored, skipped by kind, warnings, jobs started)."""
    from orion.kernel.media_store import read_media
    saved, skipped, failed, warnings, jobs = 0, {}, {}, [], []
    for kind in MEDIA_KINDS:
        wanted = [item for item in items if item["kind"] == kind]
        if not wanted:
            continue
        if not support[kind]:
            skipped[kind] = len(wanted)
            warnings.append(skip_warning(kind, len(wanted), support))
            continue
        for item in wanted:
            if await conn.fetchval("select 1 from orion_memory where user_id = $1 and group_id = $2 "
                                   f"and media_id = $3 and {LIVE} limit 1", user_id, group, item["media_id"]):
                continue
            async with conn.transaction():
                reused = await reuse_item(conn, user_id, space, group, item["media_id"], title,
                                          item["text"] or "", "chat_save", chat_id)
            if reused:
                saved += reused
                continue
            meta = {**item, "source_kind": "chat_save", "title": title, "chat_id": chat_id, "caption": item["text"],
                    "item_id": new_group(), "order": item_order()}
            try:
                if kind == "video":
                    from orion.kernel import memory_media_jobs
                    mime, raw = await read_media(chat_id, item["media_id"])
                    jobs.append(await memory_media_jobs.start(user_id, group, meta, raw, settings, held=True))
                    continue
                mime, raw = await _read_chat_media(chat_id, item["media_id"])
                pieces = await media_pieces(item, mime, raw, target)
                async with conn.transaction():
                    saved += await insert_item(conn, user_id, space, group, meta, pieces)
            except Exception as exc:
                logger.warning("Memory could not store %s %s: %s", kind, item["media_id"][:12], exc)
                failed[kind] = failed.get(kind, 0) + 1
    for kind, count in failed.items():
        warnings.append(f"{count} {NOUNS[kind][0]} kaydedilemedi (embedding modeli işleyemedi).")
    return saved, skipped, warnings, jobs


async def save_chat(user_id: str, chat_id: str, title: str, settings) -> dict:
    """Embed and store the chat's text (overlapping pieces, memory_pieces) and the images, audio and video the
    active model takes. Returns {"saved": new rows, "skipped": {kind: count}, "warnings": [Turkish sentences],
    "jobs": [videos still being embedded]}."""
    messages, ids = await _chat_messages(chat_id)
    paragraphs = chat_paragraphs(messages, ids)
    text = "\n\n".join(text for _, text in paragraphs)
    items = chat_media(messages, ids)
    files = chat_text_files(messages, ids)
    if not text.strip() and not items and not files:
        raise MemoryUnavailable("Bu sohbette kaydedilecek metin yok.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        group = new_group()
        saved = 0
        if text.strip():
            import asyncio
            from orion.kernel.memory_pieces import chat_pieces
            # Sections split on message boundaries (a user message stays with its answer).
            chat_split = await asyncio.to_thread(chat_pieces, [t for _, t in paragraphs])
            split = [(core, window) for core, window, _, _ in chat_split]
            pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group, split=split)
            if pieces:
                for piece in pieces:  # Each piece points at the message its core starts with (exact).
                    piece["message_id"] = paragraphs[chat_split[piece["split_index"]][2]][0]
                    piece["message_last"] = paragraphs[chat_split[piece["split_index"]][3]][0]
                item = {"kind": "text", "source_kind": "chat_save", "title": title, "chat_id": chat_id,
                        "message_id": pieces[0]["message_id"]}
                async with conn.transaction():
                    saved += await insert_item(conn, user_id, space, group, item, pieces)
        for file in files:
            # An attached text file is a part of its own (named by the file), like an image is: the transcript only
            # names it.
            import asyncio
            from orion.kernel.memory_pieces import text_pieces
            file_split = await asyncio.to_thread(text_pieces, file["text"])
            pieces, _ = await text_item_pieces(conn, user_id, space, file["text"], target, local, group=group,
                                               split=file_split)
            if pieces:
                async with conn.transaction():
                    saved += await insert_item(conn, user_id, space, group, {
                        "kind": "text", "source_kind": "file", "title": file["name"], "chat_id": chat_id,
                        "message_id": file["message_id"]}, pieces)
        skipped, warnings, jobs = {}, [], []
        if items:
            support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
            more, skipped, warnings, jobs = await _save_media(conn, user_id, chat_id, title, items, space, target,
                                                              support, group, settings)
            saved += more
        return {"saved": saved, "skipped": skipped, "warnings": warnings, "jobs": jobs, "group_id": group}
    finally:
        await conn.close()


MAX_NOTE = 2 * 1024 * 1024  # Characters: a resource cap only (as a text file's), a long note is split into windows.
MAX_TITLE = 120


async def add_note(user_id: str, text: str, settings, source_kind: str = "manual", title: str = "") -> int:
    """Embed and store a text the user (or the assistant on the user's request) wants remembered; returns how many
    rows were new."""
    result = await add_note_to(user_id, text, settings, source_kind, title=title)
    return result["added"] or (1 if result.get("job") else 0)  # A long note is embedded in the background.


async def add_note_to(user_id: str, text: str, settings, source_kind: str = "manual", group: str | None = None,
                      title: str = "") -> dict:
    """A note as a new memory, or an item added to the memory `group` (LookupError when this user has no such
    memory), with an optional `title` (the memory's name on its card). A long note (many windows) is embedded in
    the background like a text file: {"added": new rows, "group_id"[, "job"]}."""
    import asyncio
    title = " ".join((title or "").split())[:MAX_TITLE] or None
    text = (text or "").strip()
    if not text:
        raise ValueError("Hafızaya eklenecek metin boş.")
    if len(text) > MAX_NOTE:
        raise ValueError(f"Not çok uzun: en fazla {MAX_NOTE} karakter olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        group = await join_group(conn, user_id, group)
        from orion.kernel import memory_pieces as mp
        split = await asyncio.to_thread(mp.text_pieces, text)
        if len(split) > TEXT_JOB_PIECES:
            from orion.kernel import memory_media_jobs
            job = await memory_media_jobs.start_text(user_id, group, {
                "kind": "text", "source_kind": source_kind, "title": title, "name": title or "Not"}, text, split,
                settings)
            return {"added": 0, "group_id": group, "job": job}
        pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group, split=split)
        added = 0
        if pieces:
            async with conn.transaction():
                added = await insert_item(conn, user_id, space, group,
                                          {"kind": "text", "source_kind": source_kind, "title": title}, pieces)
        return {"added": added, "group_id": group}
    finally:
        await conn.close()


async def edit_note(user_id: str, group: str, text: str, settings, title: str | None = None) -> dict:
    """Edit a note's text IN PLACE (the memory page's note detail): the same memory (group id, collections, trash rules), its
    saved date kept, its text pieces replaced by the new ones (embedded again). `title`: a new name (None keeps it). Only notes
    (text the user or the assistant saved) are edited: ValueError for a file, a media item or a chat summary; LookupError when
    this user has no such memory; ValueError for an empty/too long text. {"group_id", "added"}."""
    import asyncio
    text = (text or "").strip()
    if not text:
        raise ValueError("Not metni boş olamaz.")
    if len(text) > MAX_NOTE:
        raise ValueError(f"Not çok uzun: en fazla {MAX_NOTE} karakter olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        group = await join_group(conn, user_id, group)
        rows = await conn.fetch(f"select source_kind, kind, media_id, created_at, source_chat_title from orion_memory "
                                f"where user_id = $1 and group_id = $2 and {LIVE} order by id", user_id, group)
        if not rows or any(r["media_id"] or r["kind"] != "text" or r["source_kind"] not in ("manual", "assistant")
                           for r in rows):
            raise ValueError("Yalnızca notlar düzenlenir.")
        target, space, local = await embedding_target(settings)
        from orion.kernel import memory_pieces as mp
        split = await asyncio.to_thread(mp.text_pieces, text)
        pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, skip_known=False, group=group,
                                           split=split)
        if not pieces:
            raise ValueError("Not metni boş olamaz.")
        name = " ".join(title.split())[:MAX_TITLE] or None if title is not None else rows[0]["source_chat_title"]
        async with conn.transaction():
            await delete_rows(conn, user_id, "group_id = $2", group)
            added = await insert_item(conn, user_id, space, group,
                                      {"kind": "text", "source_kind": rows[0]["source_kind"], "title": name}, pieces)
            await conn.execute("update orion_memory set created_at = $3 where user_id = $1 and group_id = $2",
                               user_id, group, min(r["created_at"] for r in rows))
        return {"group_id": group, "added": added}
    finally:
        await conn.close()


TEXT_FILE_TYPES = (".txt", ".md", ".markdown")
# A text file of more pieces than this is embedded as a background job (a request per piece: the page must not wait).
# A latency choice, not a quality one: a few pieces answer within the request.
TEXT_JOB_PIECES = 8
MAX_TEXT_FILE = 2 * 1024 * 1024


def file_kind(name: str, mime: str) -> str | None:
    """'image', 'audio', 'video' or 'text' (a .txt/.md file) for a file added on the memory page; None for anything
    else."""
    mime, name = (mime or "").lower(), (name or "").lower()
    kind = media_kind(mime)
    if kind:
        return kind
    if mime.startswith("text/") or name.endswith(TEXT_FILE_TYPES):
        return "text"
    return None


def file_refusal(kind: str, support: dict) -> str:
    needs = "audio" if kind == "audio" else "image"
    sense, label = ("sesi", "Ses") if kind == "audio" else ("görselleri", "Görsel/video")
    if support["has_extra"] and needs in support["extra_kinds"] and not support["extra_on"]:
        return (f"Dosya eklenmedi: {support['model']} modelinin ek dosyası kapalı. "
                "Görsel/ses için modelin ek dosyasını aç (Bileşenler → Hafıza).")
    return f"Dosya eklenmedi: {support['model']} {sense} anlamıyor. {label} için EmbeddingGemma 2'yi ek dosyasıyla seç."


def text_of_file(raw: bytes) -> str:
    if len(raw) > MAX_TEXT_FILE:
        raise ValueError("Metin dosyası çok büyük: en fazla 2 MB olabilir.")
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise ValueError("Dosyada metin yok.")
    return text


async def add_file(user_id: str, name: str, mime: str, raw: bytes, settings, group: str | None = None,
                   caption: str = "") -> dict:
    """A file the user adds on the memory page, as a new memory or into the memory `group`, with the user's
    `caption`. A text file (.txt/.md): overlapping pieces. An image or audio: as a chat save stores it (one copy per
    sha256 linked to each row; the same file in the same space is skipped). A video: embedded in the background
    (memory_media_jobs), the answer has its "job". {"added": new rows, "group_id", "warnings": [...][, "job"]};
    ValueError with a Turkish sentence for a file that cannot be added, LookupError for a memory this user does not
    have."""
    import hashlib
    from orion.kernel.media_store import MAX_MEDIA_BYTES, hold_media
    kind = file_kind(name, mime)
    if kind is None:
        raise ValueError("Yalnız görsel, ses, video ve metin (.txt, .md) dosyaları eklenebilir.")
    if not raw:
        raise ValueError("Dosya boş.")
    name = " ".join((name or "").split())[:200]
    caption = (caption or "").strip()[:4000]  # Its own lines kept: a long caption reads as written.
    if kind == "text":
        text = text_of_file(raw)
        import asyncio
        from orion.kernel import memory_pieces as mp
        split = await asyncio.to_thread(mp.text_pieces, text)
    elif len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("Dosya çok büyük: en fazla 64 MB olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        group = await join_group(conn, user_id, group)
        if kind == "text" and len(split) > TEXT_JOB_PIECES:
            # Many pieces take a request each: embedded in the background with progress, the answer comes now.
            from orion.kernel import memory_media_jobs
            job = await memory_media_jobs.start_text(user_id, group, {
                "kind": "text", "source_kind": "file", "title": name, "caption": caption, "name": name},
                text, split, settings)
            return {"added": 0, "group_id": group, "warnings": [], "job": job}
        if kind == "text":
            pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group, split=split)
            added = 0
            if pieces:
                async with conn.transaction():
                    added = await insert_item(conn, user_id, space, group, {
                        "kind": "text", "source_kind": "file", "title": name, "caption": caption}, pieces)
            return {"added": added, "group_id": group, "warnings": [] if added else ["Bu metin bu hafızada zaten var."]}
        support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
        if not support[kind]:
            raise ValueError(file_refusal(kind, support))
        mime = mime.lower()
        media_id = hashlib.sha256(raw).hexdigest()
        if await conn.fetchval("select 1 from orion_memory where user_id = $1 and group_id = $2 "
                               f"and media_id = $3 and {LIVE} limit 1", user_id, group, media_id):
            return {"added": 0, "group_id": group, "warnings": ["Bu dosya bu hafızada zaten var."]}
        async with conn.transaction():
            reused = await reuse_item(conn, user_id, space, group, media_id, name, caption)
        if reused:
            return {"added": reused, "group_id": group, "warnings": [], "reused": True}
        item = {"kind": kind, "media_id": media_id, "mime": mime, "name": name, "text": caption, "caption": caption,
                "source_kind": "file", "title": name, "item_id": new_group(), "order": item_order()}
        if kind == "video":
            from orion.kernel import memory_media_jobs
            job = await memory_media_jobs.start(user_id, group, item, raw, settings)
            return {"added": 0, "group_id": group, "warnings": [], "job": job}
        try:
            pieces = await media_pieces(item, mime, raw, target)
        except Exception as exc:
            logger.warning("Memory could not embed the file %s: %s", name, exc)
            raise ValueError("Embedding modeli bu dosyayı işleyemedi.") from exc
        async with conn.transaction():
            await hold_media(conn, media_id, mime, raw)
            added = await insert_item(conn, user_id, space, group, item, pieces)
        return {"added": added, "group_id": group, "warnings": []}
    finally:
        await conn.close()


MAX_OPEN = 50  # Ids one open_memory_files call looks at (a resource cap; the token budget decides how many open).
OPEN_SHARE = 0.4  # The share of the chat model's context that files opened by one call may take.
OPEN_FLOOR = 2000  # Tokens a call may always use (one small file opens even in a tiny context).
IMAGE_TOKENS = 1000  # What an image costs a chat model (local mmproj models 256-1024, cloud ones 85-1500): an estimate.
TEXT_OPEN_TOKENS = 3000  # The most of a text memory one open sends (the whole when it fits, else the asked part and neighbours).


async def chat_context(settings) -> int | None:
    """The active chat model's context window in tokens: the local model's configured context; None when it is
    not known (a cloud model: the API does not say)."""
    from orion.kernel.router_models import get_model_provider
    try:
        if await get_model_provider(settings.router_model_group, "chat") != "local":
            return None
        from services.shared import local_parts
        part = local_parts.spec("llm")
        _, current = local_parts.effective(part, local_parts.hardware())
        return int(current["context_size"]) or None
    except Exception as exc:
        logger.warning("Chat model context unknown: %s", exc)
        return None


def open_cost(row: dict) -> int:
    """About how many tokens opening this memory row puts into the conversation: an image IMAGE_TOKENS; audio its
    seconds (up to OPEN_AUDIO_MAX) at 25 tokens a second; a video its frames (OPEN_FRAMES) and its sound; a text
    its length (at most TEXT_OPEN_TOKENS)."""
    from orion.kernel.memory_pieces import AUDIO_TOKENS_PER_SECOND
    kind = row.get("kind")
    seconds = min(float(row.get("media_seconds") or 0), OPEN_AUDIO_MAX)
    if kind == "image":
        return IMAGE_TOKENS
    if kind == "audio":
        return int(seconds * AUDIO_TOKENS_PER_SECOND) or AUDIO_TOKENS_PER_SECOND * 30
    if kind == "video":
        return OPEN_FRAMES["whole" if row.get("whole") else "piece"] * IMAGE_TOKENS + int(seconds * AUDIO_TOKENS_PER_SECOND)
    return min(TEXT_OPEN_TOKENS, int(float(row.get("chars") or 0) * 0.35) + 1)


def plan_open(costs: list[int], budget: int) -> int:
    """How many of the asked files (in order) fit `budget` tokens together: the first always opens, then each that
    still fits; the rest wait for another ask."""
    used, fit = 0, 0
    for cost in costs:
        if fit and used + cost > budget:
            break
        used += cost
        fit += 1
    return fit


async def open_plan(user_id: str, ids: list[int], settings) -> tuple[list[int], list[int], dict]:
    """(ids that fit, ids left for another ask, {"budget", "used"}) for opening `ids` in this chat: the budget is
    OPEN_SHARE of the chat model's context (a default window when unknown), every file costed by open_cost."""
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_OPEN]
    context = await chat_context(settings) or 32768
    budget = max(OPEN_FLOOR, int(context * OPEN_SHARE))
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        rows = {r["id"]: dict(r) for r in await conn.fetch(
            "select m.id, m.kind, m.whole, m.media_seconds, (select coalesce(sum(length(t.text)), 0) from orion_memory t "
            "where t.user_id = m.user_id and not t.whole and t.item_id is not null and t.item_id = m.item_id) as chars "
            f"from orion_memory m where m.user_id = $1 and m.id = any($2::bigint[]) and {live('m')}",
            user_id, ids)}
    finally:
        await conn.close()
    known = [i for i in ids if i in rows]
    costs = [open_cost(rows[i]) for i in known]
    fit = plan_open(costs, budget)
    return known[:fit], known[fit:], {"budget": budget, "used": sum(costs[:fit])}


async def chat_model_media(settings) -> dict:
    """{"image": bool, "audio": bool}: what the chat model can be shown. The local model by its extra file
    (mmproj) and what the file says; a cloud model (or a model group) is taken to see images but not hear audio,
    since an OpenAI-compatible API does not say."""
    from orion.kernel.router_models import get_model_provider
    try:
        provider = await get_model_provider(settings.router_model_group, "chat")
    except Exception:
        provider = None
    if provider == "local":
        from services.shared import local_parts
        try:
            support = local_parts.part_media("llm")
            return {"image": bool(support["image"]), "audio": bool(support["audio"])}
        except Exception as exc:
            logger.warning("Chat model media support unknown: %s", exc)
            return {"image": False, "audio": False}
    return {"image": True, "audio": False}


async def current_space(settings) -> str | None:
    try:
        return (await embedding_target(settings))[1]
    except MemoryUnavailable:
        return None


JOB_STALE_SECONDS = 90  # A background job whose heartbeat is older lost its process (memory_move, memory_media_jobs).
KINDS = ("text", "image", "audio", "video")
MAX_QUERY_CHARS = 1_000_000  # A request-size guard only: a pasted passage is searched whole (_query_vector).
QUERY_WINDOWS = 48  # Windows of a long query embedded at most (a cost cap: more are sampled evenly over the text).
QUERY_CACHE = 32  # Recent query vectors kept (the detail's part scores ask again with the same text)
TEXT_MATCH_CHARS = 200  # Longer queries match by meaning only: a word-for-word match of a passage means little.
TEXT_CAP = 100  # Text matches taken per search.
FUZZY_MIN = 0.45  # Word similarity from which a near spelling counts ("selam nabe" ~ "selam naber"): SQL and Python.
FALLBACK_ROWS = 5000  # Rows the Python fallback (no pg_trgm) compares, newest first: a cost cap; older rows match by substring only.
_TR_UPPER, _TR_LOWER = "İIÇŞĞÖÜ", "iıçşğöü"
_TR_LETTERS, _TR_ASCII = "çğıöşüâîû", "cgiosuaiu"
_FOLD = str.maketrans(_TR_UPPER + _TR_LETTERS, _TR_LOWER.translate(str.maketrans(_TR_LETTERS, _TR_ASCII)) + _TR_ASCII)
# The same fold for a row inserted without one (a safety net: every insert path of this module sets `folded` itself).
FOLD_FALLBACK_SQL = ("lower(translate(translate({col}, '" + _TR_UPPER + "', '" + _TR_LOWER + "'), '" + _TR_LETTERS
                     + "', '" + _TR_ASCII + "'))")
_COLUMNS = ("id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
            "media_seconds, group_id, copied_from, source_chat_id, source_message_id, source_message_last, item_id, whole, span_start, "
            "span_end, track, caption, item_order")


def fold(text: str) -> str:
    """THE fold of words for matching: Turkish letters without their marks (İ/I/ı→i, Ş/ş→s...), then Python's
    lower() for every other script (German, Cyrillic...). It is computed here, once, when a row is stored (the
    `folded` and `caption_folded` columns, which the trigram indexes cover) and for the query: a database lower()
    follows its locale, so SQL never folds."""
    return text.translate(_FOLD).lower()


CHARS_PER_TOKEN = 3  # Turkish text costs about this many characters per token in common tokenizers (English ~4): a guess on the high side.


def estimate_tokens(text: str) -> int:
    """About how many model tokens `text` costs (a budget guard for the profile block and search_memory's `budget`; the
    real count is the model's tokenizer's, which the Hub does not carry). Rounded up, so a budget is never overrun."""
    return -(-len(text or "") // CHARS_PER_TOKEN)


def match_text(text: str, title: str | None = None, caption: str | None = None, source_kind: str | None = None) -> str:
    """THE words a row is matched by (lexical, keyword and spelling matches, `folded` column): its text, the file's
    name (the title of a file's rows) and the user's caption. Names and captions are not embedded into the meaning
    vector (noise there); they only count here."""
    parts = [text or ""]
    if title and source_kind != "chat_save":  # A file's name, a note's name; a chat's title is not a word of its text.
        parts.append(title)
    if caption:
        parts.append(caption)
    return "\n".join(parts)


def row_match_text(row) -> str:
    return match_text(row.get("text"), row.get("source_chat_title"), row.get("caption"), row.get("source_kind"))


def join_pieces(pieces: list[dict]) -> str:
    """The text of an item from its pieces in order. Pieces stored since cores are cut without loss (`lossless`) are
    exact slices: they join with nothing between and give back the saved text byte for byte; older pieces were
    stripped when cut (their blank lines are gone), so they are joined with a blank line, the closest there is."""
    out = ""
    for index, piece in enumerate(pieces):
        out += piece["text"]
        if index + 1 < len(pieces) and not piece.get("lossless"):
            out += "\n\n"
    return out


def like_pattern(folded: str) -> str:
    """A LIKE pattern that finds `folded` anywhere (its wildcards escaped)."""
    return "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _trigrams(text: str) -> set[str]:
    grams = set()
    for word in re.findall(r"\w+", text):
        grams |= _word_trigrams(word)
    return grams


@functools.lru_cache(maxsize=8192)
def _word_trigrams(word: str) -> frozenset[str]:
    """The trigrams of one word as pg_trgm pads it. A text's are the union of its words' (remembered: a search compares the
    same few words over and over)."""
    padded = f"  {word} "
    return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))


def word_similarity(query: str, text: str) -> float:
    """The share of the query's trigrams found in the best run of as many words of the text: a light stand-in for
    pg_trgm's word_similarity on a database without the extension. Both are folded already."""
    wanted = _trigrams(query)
    if not wanted:
        return 0.0
    words = re.findall(r"\w+", text)
    size = max(1, len(re.findall(r"\w+", query)))
    grams = [_word_trigrams(w) for w in words]  # each word once, however many windows it sits in
    best = 0.0
    for start in range(max(1, len(words) - size + 1)):
        window = set().union(*grams[start:start + size])
        best = max(best, len(wanted & window) / len(wanted))
        if best == 1.0:
            break
    return best


def filters_sql(vector_space: str, kind: str, params: list, collections: list[str] | None = None,
                live: bool = True, when=None) -> str:
    """SQL conditions (after the user's, `$1`) for the page's model, kind and collection filters, and the rows in the
    trash left out (`live`); their values are appended to `params` (the caller's, numbered from its length). `when` (memory_events.when_keys): only memories dated within a date range."""
    from orion.kernel.memory_collections import filter_sql
    sql = (f" and {LIVE}" if live else "") + filter_sql(collections, params)  # `live` False: the caller checks the trash.
    if when:
        from orion.kernel import memory_events
        sql += memory_events.when_sql(when, params)
    if vector_space and vector_space != "all":
        params.append(vector_space)
        sql += f" and vector_space = ${len(params)}"
    if kind in KINDS:  # A literal (validated): the planner can then use the kind's own partial vector index.
        sql += f" and kind = '{kind}'"
    elif kind and kind != "all":
        params.append(kind)
        sql += f" and kind = ${len(params)}"
    return sql


def _score_sql(params: list, space, vector) -> str:
    """The meaning score of a row of the current space (null for other spaces or without a query vector)."""
    if vector is None:
        return "null::float8"
    params += [space, vector]
    return f"case when vector_space = ${len(params) - 1} then 1 - (embedding <=> ${len(params)}::vector) end"


async def _text_rows(conn, user_id: str, q: str, vector_space: str, kind: str, space=None, vector=None,
                     fuzzy: bool = True, collections: list[str] | None = None, when=None) -> list[dict]:
    """This user's rows whose text has `q` (Turkish case folding) or a near spelling of it, best first. Each row has
    'tmatch' ('text' for a substring, 'similar' for a near spelling), 'tscore' (1 for a substring) and 'score'
    (its meaning score in the current space, else None). `fuzzy` False: substrings only (a keyword search)."""
    folded = fold(q)
    like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    params = [user_id, like] + ([folded] if _trgm and fuzzy else [])
    score = _score_sql(params, space, vector)
    extra = filters_sql(vector_space, kind, params, collections, when=when)
    if _trgm and fuzzy:
        # Words first, near spellings after: a substring match ranks above any near spelling (its score is 1), so the
        # near-spelling search (`<%`, which walks most of the trigram index) only runs when the words alone did not
        # fill the cap, and then only for the rest. `like` and `<%` (word_similarity >= the threshold set here) are the
        # operators the GIN trigram index answers; one row per memory is chosen in SQL, so the cap counts memories.
        group = "coalesce(group_id, 'r' || id::text)"
        found = await conn.fetch(
            f"select * from (select distinct on ({group}) {_COLUMNS}, {score} as score, 1.0::float8 as tscore "
            f"from orion_memory where user_id = $1 and not whole and folded like $2 and $3::text is not null{extra} "
            f"order by {group}, created_at desc, id desc) best "
            f"order by created_at desc, id desc limit {TEXT_CAP}", *params)
        rows = [{**dict(r), "tmatch": "text"} for r in found]
        if len(rows) < TEXT_CAP:
            seen = [group_key(r) for r in rows]
            params.append(seen)
            async with conn.transaction():
                await conn.execute(f"select set_config('pg_trgm.word_similarity_threshold', '{FUZZY_MIN}', true)")
                near = await conn.fetch(
                    f"select * from (select distinct on ({group}) {_COLUMNS}, {score} as score, "
                    f"word_similarity($3, folded) as tscore from orion_memory "
                    f"where user_id = $1 and not whole and not (folded like $2) and $3 <% folded "
                    f"and {group} <> all(${len(params)}::text[]){extra} "
                    f"order by {group}, tscore desc, created_at desc, id desc) best "
                    f"order by tscore desc, created_at desc, id desc limit {TEXT_CAP - len(rows)}", *params)
            rows += [{**dict(r), "tmatch": "similar"} for r in near]
        return rows
    rows = [{**dict(r), "tscore": 1.0, "tmatch": "text"} for r in await conn.fetch(
        f"select {_COLUMNS}, {score} as score from orion_memory where user_id = $1 and not whole and folded like $2"
        f"{extra} order by created_at desc, id desc limit {TEXT_CAP}", *params)]
    if len(rows) >= TEXT_CAP or not fuzzy:
        return rows
    plain = [user_id, like]
    extra = filters_sql(vector_space, kind, plain, collections, when=when)
    near = []
    for r in await conn.fetch(
            f"select id, folded from orion_memory where user_id = $1 and not whole and not (folded like $2)"
            f"{extra} order by created_at desc, id desc limit {FALLBACK_ROWS}", *plain):
        similarity = word_similarity(folded, r["folded"])
        if similarity >= FUZZY_MIN:
            near.append((similarity, r["id"]))
    near = sorted(near, reverse=True)[:TEXT_CAP - len(rows)]
    if near:
        params = [user_id, [row_id for _, row_id in near]]
        score = _score_sql(params, space, vector)
        found = {r["id"]: dict(r) for r in await conn.fetch(
            f"select {_COLUMNS}, {score} as score from orion_memory where user_id = $1 and id = any($2::bigint[])",
            *params)}
        rows += [{**found[row_id], "tscore": similarity, "tmatch": "similar"}
                 for similarity, row_id in near if row_id in found]
    return rows


_query_cache: dict = {}


def query_windows(q: str) -> tuple[list[str], int]:
    """The windows a query is embedded as: one for a short query; a long passage is split as stored text is
    (memory_pieces.text_pieces: cores with their overlapping windows), at most QUERY_WINDOWS of them taken evenly
    across it. (windows, how many the text has)."""
    from orion.kernel.memory_pieces import text_pieces
    pieces = [window for _, window in text_pieces(q)] or [q]
    total = len(pieces)
    if total > QUERY_WINDOWS:
        step = total / QUERY_WINDOWS
        pieces = [pieces[int(k * step)] for k in range(QUERY_WINDOWS)]
    return pieces, total


async def _query_vector(q: str, settings, space: str | None = None) -> tuple[str | None, str | None, str | None]:
    """(vector space, query vector, reason when the search by meaning is off): THE vector of a text, whether
    it was typed, pasted or read from a dropped file. A short text is a question (embedded as a query); a longer
    one is a passage compared with stored passages: embedded as the stored windows are (documents), the way
    stored text is split (query_windows), and the normalized mean of those vectors searches, as a stored long
    text's whole vector is made. A text that is stored as it is (a dropped file) so finds its own copy first.
    The last QUERY_CACHE vectors are kept (the same text asked again, e.g. for a memory's part scores)."""
    import asyncio
    import hashlib
    try:
        target, space, local = await embedding_target(settings, space)
    except MemoryUnavailable as exc:
        return None, None, str(exc)
    key = (space, hashlib.sha256(q.encode("utf-8", "replace")).hexdigest())
    if key in _query_cache:
        _query_cache[key] = _query_cache.pop(key)
        return space, _query_cache[key], None
    try:
        passage = len(q) > TEXT_MATCH_CHARS
        windows, _ = await asyncio.to_thread(query_windows, q) if passage else ([q], 1)
        task = "document" if passage else "query"
        if len(windows) == 1:
            vector = _vector((await _embed(windows[0], target, task, local))[0])
        else:
            from orion.kernel.memory_pieces import mean_vector
            vector = _vector(mean_vector([(await _embed(w, target, task, local))[0] for w in windows]))
    except MemoryUnavailable as exc:
        return space, None, str(exc)
    _query_cache[key] = vector
    while len(_query_cache) > QUERY_CACHE:
        _query_cache.pop(next(iter(_query_cache)))
    return space, vector, None


async def delete_rows(conn, user_id: str, where: str, *args) -> list[dict]:
    """Delete this user's rows matching `where` (SQL after the user's condition, parameters from $2) and drop their
    media links, inside the caller's transaction; the whole vectors of the items that lost a piece follow
    (refresh_wholes). Returns (id, text, content_key, group_id, item_id) of each."""
    from orion.kernel.media_store import unlink_many
    rows = await conn.fetch(
        f"delete from orion_memory where user_id = $1 and ({where}) "
        f"returning id, text, media_id, group_id, item_id, whole, {CONTENT_KEY} as content_key", user_id, *args)
    rows = [dict(r) for r in rows]
    await unlink_many([(r["media_id"], r["id"]) for r in rows if r["media_id"]], "memory", conn=conn)
    items = {r["item_id"] for r in rows if r.get("item_id") and not r.get("whole")}
    if items:
        await refresh_wholes(conn, user_id, sorted(items))
    return rows


def _parse_vector(value) -> list[float]:
    import json
    return json.loads(value) if isinstance(value, str) else [float(v) for v in value]


async def refresh_wholes(conn, user_id: str, items: list[str]) -> None:
    """Make the whole vectors of `items` follow their pieces: the mean of the live pieces (pgvector's avg, then
    normalized: the direction is what a whole vector is; no embedding is read out). A track left with fewer than two
    pieces (live or in the trash) loses its whole vector; one whose pieces are all in the trash is in the trash with
    them (memory_trash), and comes back when one of them does."""
    from orion.kernel.media_store import unlink_many
    wholes = await conn.fetch("select id, item_id, vector_space, track, media_id from orion_memory "
                              "where user_id = $1 and item_id = any($2::text[]) and whole", user_id, items)
    if not wholes:
        return
    counts = {(r["item_id"], r["vector_space"], r["track"]): r for r in await conn.fetch(
        "select m.item_id, m.vector_space, m.track, count(*) as n, count(*) filter (where t.memory_id is null) as live, "
        "(array_agg(t.trash_id order by t.deleted_at desc) filter (where t.memory_id is not null))[1] as trash_id "
        "from orion_memory m left join orion_mem_trash t on t.memory_id = m.id "
        "where m.user_id = $1 and m.item_id = any($2::text[]) and not m.whole group by 1, 2, 3",
        user_id, items)}

    def of(w):
        return counts.get((w["item_id"], w["vector_space"], w["track"]))
    gone = [w for w in wholes if of(w) is None or of(w)["n"] < 2]
    if gone:
        await conn.execute("delete from orion_memory where id = any($1::bigint[])", [w["id"] for w in gone])
        await unlink_many([(w["media_id"], w["id"]) for w in gone if w["media_id"]], "memory", conn=conn)
    dead = [w for w in wholes if w not in gone and of(w)["live"] == 0]
    if dead:
        await conn.execute(
            "insert into orion_mem_trash (memory_id, user_id, trash_id) select unnest($1::bigint[]), $2, "
            "unnest($3::text[]) on conflict do nothing", [w["id"] for w in dead], user_id, [of(w)["trash_id"] for w in dead])
    kept = [w for w in wholes if w not in gone and w not in dead]
    if kept:
        await conn.execute(
            "update orion_memory w set embedding = l2_normalize(m.v) "
            "from (select item_id, vector_space, track, avg(embedding) as v from orion_memory where user_id = $1 "
            f"and item_id = any($2::text[]) and not whole and {LIVE} group by 1, 2, 3) m "
            "where w.id = any($3::bigint[]) and w.item_id = m.item_id "
            "and w.vector_space = m.vector_space and w.track is not distinct from m.track",
            user_id, items, [w["id"] for w in kept])
        await conn.execute("delete from orion_mem_trash where memory_id = any($1::bigint[])", [w["id"] for w in kept])


async def delete_whole_items(user_id: str, item_ids: list[str]) -> list[dict]:
    """Delete these items for good, at once (every piece, the whole vectors, the copies in other spaces, the media
    links): a part of the page is an item, a long text has thousands of rows and the panel holds only some of them.
    Returns (id, text) of each piece deleted. The user's removals go through the trash (memory_trash) instead."""
    item_ids = [str(i)[:64] for i in item_ids if i and not str(i).startswith("r")][:100]
    if not item_ids:
        return []
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        async with conn.transaction():
            rows = await delete_rows(conn, user_id, "item_id = any($2::text[])", item_ids)
    finally:
        await conn.close()
    return [{"id": r["id"], "text": r["text"]} for r in rows if not r.get("whole")]


async def _neighbours_of(conn, user_id: str, ids: list[int]) -> list[int]:
    """The text pieces next to the pieces `ids` are about to delete (in their items, in every vector space): the
    surviving piece on either side of each gap. Their stored vectors were made from windows that hold the deleted
    text. One statement: the items' pieces in order, each flagged dead or alive, a survivor beside a dead one counts."""
    return [r["id"] for r in await conn.fetch(
        """with asked as (select coalesce(copied_from, id) as c, item_id from orion_memory where user_id = $1
                          and id = any($2::bigint[]) and kind = 'text' and not whole and item_id is not null),
                pieces as (select m.id, coalesce(m.copied_from, m.id) in (select c from asked) as dead,
                                  lag(coalesce(m.copied_from, m.id) in (select c from asked)) over w as prev_dead,
                                  lead(coalesce(m.copied_from, m.id) in (select c from asked)) over w as next_dead
                           from orion_memory m where m.user_id = $1 and m.kind = 'text' and not m.whole
                           and m.item_id in (select item_id from asked)
                           window w as (partition by m.item_id, m.vector_space
                                        order by m.item_order, coalesce(m.copied_from, m.id)))
           select id from pieces where not dead and (prev_dead or next_dead) order by id""", user_id, ids)]


_reembed_tasks: set = set()


def _schedule_reembed(user_id: str, ids: list[int], settings) -> None:
    import asyncio
    try:
        task = asyncio.get_running_loop().create_task(_reembed(user_id, ids, settings), name="memory-reembed")
    except RuntimeError:
        return
    _reembed_tasks.add(task)
    task.add_done_callback(_reembed_tasks.discard)


async def _reembed(user_id: str, ids: list[int], settings) -> None:
    """Make the vectors of the text pieces `ids` again from their windows as they are now (a neighbour was deleted),
    in EVERY vector space they exist in, with that space's own model; the items' whole vectors follow. A space whose
    model cannot be reached drops its copy of the piece when the memory has another copy of it (a later move makes
    it again), so the deleted words never stay findable through a stale window; a piece with no other copy is kept
    and logged. Own settings, own connection: a delete never waits for this or fails because of it."""
    from orion.kernel.memory_pieces import windows_of
    try:
        if settings is None:
            from orion.kernel.config import get_runtime_settings
            settings = await get_runtime_settings(None, user_id)
        try:
            targets = {space: (target, local) for target, space, local in await embedding_targets(settings)}
        except MemoryUnavailable as exc:
            logger.warning("Memory: no embedding model for the neighbours of a deleted piece: %s", exc)
            targets = {}
        conn = await _connect()
        try:
            wanted = [dict(r) for r in await conn.fetch(
                "select id, item_id, vector_space from orion_memory where user_id = $1 and id = any($2::bigint[])",
                user_id, ids)]
            for item, space in sorted({(r["item_id"], r["vector_space"]) for r in wanted}):
                mine = {r["id"] for r in wanted if (r["item_id"], r["vector_space"]) == (item, space)}
                pieces = sorted((dict(r) for r in await conn.fetch(
                    "select id, text, copied_from, item_order from orion_memory where user_id = $1 and item_id = $2 "
                    "and vector_space = $3 and kind = 'text' and not whole", user_id, item, space)), key=order_key)
                windows = windows_of([p["text"] for p in pieces])
                if space in targets:
                    target, local = targets[space]
                    for piece, window in zip(pieces, windows):
                        if piece["id"] in mine:
                            vector = (await _embed(window, target, "document", local))[0]
                            await conn.execute("update orion_memory set embedding = $3::vector "
                                               "where user_id = $1 and id = $2", user_id, piece["id"], _vector(vector))
                else:
                    for piece in pieces:
                        if piece["id"] not in mine:
                            continue
                        other = await conn.fetchval(
                            "select 1 from orion_memory where user_id = $1 and vector_space <> $2 and "
                            f"{CONTENT_KEY} = $3 limit 1", user_id, space, content_key(piece))
                        if other:
                            await conn.execute("delete from orion_memory where user_id = $1 and id = $2",
                                               user_id, piece["id"])
                        else:
                            logger.warning("Memory: piece %s in space %s keeps a stale window (no model for it)",
                                           piece["id"], space)
                async with conn.transaction():
                    await refresh_wholes(conn, user_id, [item])
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Memory: neighbours of a deleted piece could not be embedded again: %s", exc)


async def delete_items(user_id: str, ids: list[int], settings=None) -> list[dict]:
    """Delete this user's rows among `ids` together with their copies in other vector spaces for good, at once (one
    memory, one delete; the user's removals go through the trash, memory_trash); returns the asked rows that were
    deleted (id, text). Other users' ids are ignored. A deleted text
    piece leaves its words in its neighbours' windows, so with `settings` those neighbours are embedded again in
    the background (their vectors follow the text that is left)."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return []
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        async with conn.transaction():
            neighbours = await _neighbours_of(conn, user_id, ids)
            rows = await delete_rows(
                conn, user_id, f"{CONTENT_KEY} in (select {CONTENT_KEY} from orion_memory where user_id = $1 "
                               "and id = any($2::bigint[]))", ids)
    finally:
        await conn.close()
    if neighbours:  # After the commit; the job resolves its own settings and never fails the delete.
        _schedule_reembed(user_id, neighbours, settings)
    asked = set(ids)
    return [{"id": r["id"], "text": r["text"]} for r in rows if r["id"] in asked]


def group_key(row) -> str:
    """The memory (card) a row belongs to: its group, or the row alone for a row saved before groups."""
    return row.get("group_id") or f"r{row['id']}"


def content_key(row) -> int:
    return row.get("copied_from") or row["id"]


def prefer_space(rows: list[dict], space) -> list[dict]:
    """One row per content, the copy in `space` when there is one, in the order the memory was saved."""
    chosen = {}
    for r in rows:
        key = content_key(r)
        if key not in chosen or (r["vector_space"] == space and chosen[key]["vector_space"] != space):
            chosen[key] = r
    return sorted(chosen.values(), key=order_key)


def order_key(row) -> tuple:
    """A part's place in its memory: its item's place (the order the parts were added in; old rows first), then the
    order it was stored in."""
    return (row.get("item_order") or 0, content_key(row))


def pieces_only(rows: list[dict]) -> list[dict]:
    """The parts a memory shows: every row but the items' whole vectors."""
    return [r for r in rows if not r.get("whole")]


def item_positions(parts: list[dict]) -> dict[int, tuple[int, int]]:
    """{row id: (its place among its item's pieces of the same track, how many there are)} for the parts of a
    memory (pieces_only, in order); a part without an item is (1, 1)."""
    members: dict = {}
    for p in parts:
        members.setdefault((p.get("item_id") or f"r{content_key(p)}", p.get("track")), []).append(p["id"])
    positions = {}
    for ids in members.values():
        for index, row_id in enumerate(ids, 1):
            positions[row_id] = (index, len(ids))
    return positions


async def file_query_vector(name: str, mime: str, raw: bytes, settings) -> tuple[str, str, str]:
    """(vector space, vector, kind) to search memory with a file: an image as it is; an audio by its first window;
    a video by frames spread over all of it (as many as one request takes); a text file as a pasted text is
    (_query_vector). ValueError (Turkish) for a file the model cannot take."""
    import asyncio
    from orion.kernel import memory_pieces as mp
    from orion.worker.services.router import audio_part, image_part
    kind = file_kind(name, mime)
    if kind is None:
        raise ValueError("Yalnız görsel, ses, video ve metin (.txt, .md) dosyalarıyla aranabilir.")
    if not raw:
        raise ValueError("Dosya boş.")
    target, space, local = await embedding_target(settings)
    if kind == "text":  # The vector a pasted text gets (_query_vector).
        space, vector, reason = await _query_vector(text_of_file(raw), settings)
        if vector is None:
            raise MemoryUnavailable(reason or NO_VECTOR_MESSAGE)
        return space, vector, kind
    support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
    if not support[kind]:
        raise ValueError(file_refusal(kind, support).replace("Dosya eklenmedi", "Bu dosyayla aranamaz"))
    try:
        if kind == "image":
            vector = await _embed_media([image_part(raw, mime.lower())], target)
        elif kind == "audio":
            windows, _ = await asyncio.to_thread(audio_windows, raw)
            if not windows:
                raise ValueError("Ses çözülemedi.")
            vector = await _embed_media([audio_part(windows[0][1], "wav")], target)
        else:
            info = await asyncio.to_thread(mp.video_info, raw)
            seconds = min(info["seconds"] or mp.MAX_VIDEO_SECONDS, mp.MAX_VIDEO_SECONDS)
            from orion.kernel import memory_media_jobs
            _, probe, _ = await asyncio.to_thread(mp.video_frames, raw, [seconds / 2])
            if not probe:
                raise ValueError("Videodan kare çıkarılamadı.")
            mp.ensure_context(embedding_context(), await memory_media_jobs.frame_cost(probe[0], target), video=True)
            count = mp.VIDEO_FRAMES
            count = min(count, max(1, int(seconds * mp.VIDEO_MAX_FPS)))
            times = [(k + 0.5) * seconds / count for k in range(count)]
            _, frames, _ = await asyncio.to_thread(mp.video_frames, raw, times)
            if not frames:
                raise ValueError("Videodan kare çıkarılamadı.")
            vector = await _embed_media([image_part(f, "image/jpeg") for f in frames], target)
    except ValueError:
        raise
    except MemoryUnavailable:
        raise
    except Exception as exc:
        logger.warning("Memory search by file %s failed: %s", name, exc)
        raise ValueError("Embedding modeli bu dosyayı işleyemedi.") from exc
    return space, _vector(vector), kind


OPEN_FRAMES = {"piece": 4, "whole": 6}  # Frames shown to the chat model for a video segment / a whole video.
OPEN_AUDIO_MAX = 120.0  # Seconds of a video's sound (from its start) opened with the whole video.


def segment_media(raw: bytes, row: dict, kinds: set[str]) -> list[tuple[str, str, bytes]]:
    """What the chat model is shown of a video segment or an audio piece (in a thread): (kind, mime, bytes) of a few
    frames of the segment and its sound; for a whole video, frames across it and the start of its sound."""
    from orion.kernel import memory_pieces as mp
    found = []
    seconds = float(row.get("media_seconds") or 0)
    start, end = row.get("span_start"), row.get("span_end")
    if start is None:
        start, end = 0.0, seconds
    start, end = float(start), float(end)
    if row["kind"] == "video" and "image" in kinds:
        count = OPEN_FRAMES["whole" if row.get("whole") else "piece"]
        length = max(0.1, end - start)
        times = [start + (k + 0.5) * length / count for k in range(count)]
        _, frames, _ = mp.video_frames(raw, times, size=640)
        found += [("image", "image/jpeg", frame) for frame in frames]
    if "audio" in kinds and row["kind"] in ("audio", "video"):
        stop = min(end, start + OPEN_AUDIO_MAX) if row.get("whole") and row["kind"] == "video" else end
        pcm = mp.pcm_slice(mp.decode_pcm(raw), start, stop)
        if len(pcm) >= mp.SAMPLE_RATE // 5:
            found.append(("audio", "audio/wav", mp.wav_bytes(pcm)))
    return found


def _sliced(row: dict) -> bool:
    """A row opened as a slice of its file: any video row, and a piece of an audio that has more than one."""
    if row["kind"] == "video":
        return True
    return row["kind"] == "audio" and not row.get("whole") and row.get("span_start") is not None and (
        float(row["span_start"]) > 0 or float(row["span_end"]) < float(row.get("media_seconds") or 0) - 0.5)


def opened_in_chat(messages: list[dict]) -> dict[str, int]:
    """{file sha256: turn} of the memory files still in a chat's history (open_memory_files's tool messages keep
    them as `media_refs`, documents and files the model could not take as kind "file"; a video's frames name their file in `source`). Keyed by content: the same bytes under
    any memory row are the same file, a changed file is a new one, and a file no longer in the history (compacted)
    is not listed. The turn counts the user's messages from 1; the first opening wins."""
    opened: dict[str, int] = {}
    turn = 0
    for message in messages:
        if message.get("role") == "user":
            turn += 1
        if message.get("role") == "tool" and isinstance(message.get("media_refs"), list):
            for ref in message["media_refs"]:
                for key in (ref.get("media_id"), ref.get("source")):
                    if key:
                        opened.setdefault(str(key), max(turn, 1))
    return opened


async def open_media(user_id: str, chat_id: str, ids: list[int], kinds: set[str],
                     already: dict[str, int] | None = None) -> list[dict]:
    """This user's rows among `ids`, in that order. The file of each image/audio row whose kind is in `kinds` is
    linked to the chat (owner 'chat'), so the chat's history can hand it to the model on every later turn and the
    cleaner keeps it while a message names it; such rows say `opened` and list their `media`
    ([{"kind", "media_id", "mime"}]). A video row (or a piece of a long audio) is opened as what it holds: a few
    frames and its sound, stored as files of their own (segment_media). A file whose kind the model cannot take is
    still linked to the chat (the user sees it under the turn): such rows say `shown`. A row whose file is in
    `already` ({sha256: turn}, opened earlier in this chat) is not attached again: it says `already` (that turn)."""
    import asyncio
    import hashlib
    from orion.kernel.media_store import hold_media, link_media
    ids = [int(i) for i in ids][:MAX_OPEN]
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        rows = {r["id"]: dict(r) for r in await conn.fetch(
            "select id, text, kind, media_id, media_mime, media_seconds, span_start, span_end, whole, track "
            f"from orion_memory where user_id = $1 and id = any($2::bigint[]) and {LIVE}", user_id, ids)}
        found, seen = [], set()
        for row_id in ids:
            row = rows.get(row_id)
            if row is None or row_id in seen:
                continue
            seen.add(row_id)
            row["opened"] = False
            if already and row["media_id"] and row["media_id"] in already:
                row["already"] = already[row["media_id"]]
                found.append(row)
                continue
            wanted = bool(kinds & {"image", "audio"}) if row["kind"] == "video" else row["kind"] in kinds
            if not row["media_id"]:
                found.append(row)
                continue
            try:
                if not wanted:  # The model cannot take it; the user still gets the file under the turn.
                    await link_media(row["media_id"], "chat", chat_id, conn=conn)
                    row["shown"] = True
                elif not _sliced(row):
                    await link_media(row["media_id"], "chat", chat_id, conn=conn)
                    row["media"] = [{"kind": row["kind"], "media_id": row["media_id"], "mime": row["media_mime"]}]
                else:
                    # The whole file stays reachable from the chat too: the user sees it under the turn.
                    await link_media(row["media_id"], "chat", chat_id, conn=conn)
                    stored = await conn.fetchrow("select data from orion_media where id = $1", row["media_id"])
                    if stored is None:
                        raise LookupError(row["media_id"])
                    parts = await asyncio.to_thread(segment_media, bytes(stored["data"]), row, kinds)
                    row["media"] = []
                    async with conn.transaction():
                        for kind, mime, data in parts:
                            media_id = hashlib.sha256(data).hexdigest()
                            await hold_media(conn, media_id, mime, data)
                            await link_media(media_id, "chat", chat_id, conn=conn)
                            row["media"].append({"kind": kind, "media_id": media_id, "mime": mime})
                row["opened"] = bool(row.get("media"))
            except LookupError:
                row["missing"] = True
            except Exception as exc:
                logger.warning("Memory could not open row %s: %s", row_id, exc)
                row["failed"] = True
            found.append(row)
        return found
    finally:
        await conn.close()


async def read_item_media(user_id: str, item_id: int) -> tuple[str, bytes, str]:
    """(mime, bytes, media id) of the file this user's memory row links. LookupError for anything else: another
    user's row, a missing row, a row without media and a file that is gone are the same answer."""
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        row = await conn.fetchrow(
            "select m.id, m.mime_type, m.data from orion_memory r join orion_media_links l "
            "on l.media_id = r.media_id and l.owner_kind = 'memory' and l.owner_id = r.id::text "
            "join orion_media m on m.id = r.media_id where r.user_id = $1 and r.id = $2", user_id, int(item_id))
    finally:
        await conn.close()
    if row is None:
        raise LookupError("Media not found")
    return row["mime_type"], bytes(row["data"]), row["id"]


async def item_span(user_id: str, item_id: int) -> tuple[float, float] | None:
    """(start, end) seconds of this user's timed memory row (a video segment, an audio piece), None for others."""
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        row = await conn.fetchrow("select span_start, span_end from orion_memory where user_id = $1 and id = $2",
                                  user_id, int(item_id))
    finally:
        await conn.close()
    if row is None or row["span_start"] is None:
        return None
    return float(row["span_start"]), float(row["span_end"])
