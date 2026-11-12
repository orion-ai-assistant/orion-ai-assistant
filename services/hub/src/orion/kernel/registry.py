from __future__ import annotations

import json
import logging
from datetime import datetime

import asyncpg

from services.shared.environment import get_postgres_url

logger = logging.getLogger(__name__)


async def get_chat_tool_selection(chat_id: str, user_id: str):
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Database unavailable; cannot resolve chat tools")
    try:
        await _ensure_tables(conn)
        row = await conn.fetchrow("select tool_selection from orion_chats where id=$1 and user_id=$2", chat_id, user_id)
        if row is None:
            raise RuntimeError("Chat not found in durable storage")
        value = row["tool_selection"]
        return json.loads(value) if isinstance(value, str) else value
    finally:
        await conn.close()


async def set_chat_tool_selection(chat_id: str, user_id: str, selection):
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Database unavailable; tools were not saved")
    try:
        await _ensure_tables(conn)
        result = await conn.execute(
            "update orion_chats set tool_selection=$3::jsonb where id=$1 and user_id=$2",
            chat_id, user_id, json.dumps(selection) if selection is not None else None,
        )
        if result != "UPDATE 1":
            raise RuntimeError("Chat not found; tools were not saved")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

import asyncio
import weakref

# One small pool per event loop (each API process, the Worker). Opening a new PostgreSQL connection for
# every call - every authenticated request checks its session - cost a TCP handshake, a login and a new
# server process each time, for weeks. A pooled connection's close() hands it back instead.
POOL_MAX = 8
POOL_IDLE_SECONDS = 300
_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[str, asyncpg.Pool]]" = weakref.WeakKeyDictionary()
_pool_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


class _PooledConnection:
    """A pool connection that callers use like their own: close() returns it to the pool."""
    __slots__ = ("_pool", "_conn")

    def __init__(self, pool: asyncpg.Pool, conn):
        self._pool, self._conn = pool, conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            # Release rolls back anything left open and resets the session; a broken one is dropped.
            await self._pool.release(conn)


async def _pool(database_url: str, timeout: float) -> asyncpg.Pool:
    loop = asyncio.get_running_loop()
    lock = _pool_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        current = _pools.get(loop)
        if current and current[0] == database_url and not current[1].is_closing():
            return current[1]
        if current:  # The address or password changed: the old connections are not reused.
            current[1].terminate()
        # min_size=0: nothing is opened until asked, so a database that is not up yet fails per call, as before.
        pool = await asyncpg.create_pool(database_url, min_size=0, max_size=POOL_MAX, timeout=timeout,
                                         max_inactive_connection_lifetime=POOL_IDLE_SECONDS)
        _pools[loop] = (database_url, pool)
        return pool


async def close_pool() -> None:
    """At shutdown: the pool of the running loop closes its connections."""
    current = _pools.pop(asyncio.get_running_loop(), None)
    if current:
        try:
            await asyncio.wait_for(current[1].close(), 5)
        except Exception:
            current[1].terminate()


async def _connect(retries: int = 3, delay: float = 0.5, timeout: float = 2.0) -> _PooledConnection | None:
    """A connection from this loop's pool, or None on failure, with retries and timeout."""
    try:
        database_url = get_postgres_url()
    except RuntimeError as exc:
        logger.info("Postgres not configured: %s", exc)
        return None

    for attempt in range(retries):
        try:
            pool = await _pool(database_url, timeout)
            return _PooledConnection(pool, await pool.acquire(timeout=timeout))
        except Exception as e:
            if attempt == retries - 1:
                logger.warning("Failed to connect to Postgres after %d attempts: %s", retries, e)
                return None
            logger.debug("Postgres connection retry (%d/%d): %s. Waiting %.1fs...", attempt + 1, retries, e, delay)
            await asyncio.sleep(delay)
    return None


async def check_db_health(timeout: float = 1.0) -> bool:
    """Quick check if Postgres database is reachable and accepting queries.

    A fresh connection, never a pooled one: an idle pooled connection that a restart broke would read as a
    database that is down."""
    try:
        conn = await asyncpg.connect(get_postgres_url(), timeout=timeout)
    except Exception:
        return False
    try:
        await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        await conn.close()


# The schema is created once per process. Every call used to run it again: an advisory lock and ~20
# CREATE/ALTER ... IF NOT EXISTS statements on each request (every UI poll), and ALTER TABLE takes an
# exclusive table lock even when the column exists. A database restarted under the Hub keeps its tables.
_tables_ready = False


async def _ensure_tables(conn: asyncpg.Connection) -> None:
    global _tables_ready
    if _tables_ready:
        return
    # Multiple native listeners and the Worker initialize the same fresh DB.
    # IF NOT EXISTS alone does not serialize PostgreSQL catalog creation.
    await conn.execute('SELECT pg_advisory_lock(51730101)')
    try:
        await _create_tables(conn)
    finally:
        await conn.execute('SELECT pg_advisory_unlock(51730101)')
    _tables_ready = True


async def _create_tables(conn: asyncpg.Connection) -> None:
    try:
        # --- Settings ---
        await conn.execute(
            """
            create table if not exists orion_settings (
                user_id text not null,
                key text not null,
                value text not null,
                updated_at timestamptz default now(),
                primary key (user_id, key)
            );
            """
        )
        # --- Users ---
        await conn.execute(
            """
            create table if not exists orion_users (
                id text primary key,
                password_hash text not null,
                created_at timestamptz default now(),
                is_active boolean default false
            );
            """
        )
        await conn.execute("alter table orion_users alter column is_active set default false")
        # Profile picture: a small square image.
        await conn.execute("alter table orion_users add column if not exists avatar bytea")
        await conn.execute("alter table orion_users add column if not exists avatar_mime text")
        await conn.execute("alter table orion_users add column if not exists avatar_updated_at timestamptz")
        await conn.execute("""
            create table if not exists orion_invitations (
                id text primary key,
                token_hash text unique not null,
                label text not null default '',
                expires_at timestamptz not null,
                created_at timestamptz not null default now(),
                used_by text,
                revoked boolean not null default false
            )
        """)
        # --- Account sessions (see orion.api.sessions) ---
        await conn.execute("""
            create table if not exists orion_sessions (
                id text primary key,
                user_id text not null,
                device_id text,
                credential text not null,
                created_at timestamptz not null default now(),
                last_seen_at timestamptz not null default now(),
                revoked_at timestamptz
            )
        """)
        await conn.execute("create index if not exists idx_orion_sessions_user_id on orion_sessions (user_id)")
        # --- Chats (metadata) ---
        await conn.execute(
            """
            create table if not exists orion_chats (
                id text primary key,
                user_id text not null,
                title text not null default 'New Chat',
                created_at timestamptz default now(),
                updated_at timestamptz default now()
            );
            """
        )
        await conn.execute(
            "create index if not exists idx_orion_chats_user_id on orion_chats (user_id, updated_at desc);"
        )
        # Chat list pages: (updated_at, id) keys the page boundary.
        await conn.execute(
            "create index if not exists idx_orion_chats_user_page on orion_chats (user_id, updated_at desc, id desc);"
        )
        # --- Chat Messages ---
        await conn.execute("alter table orion_chats add column if not exists tool_selection jsonb")
        # The user's one "Asistanla konuş" conversation, shared by their devices.
        await conn.execute("alter table orion_chats add column if not exists assistant boolean not null default false")
        await conn.execute(
            """
            create table if not exists orion_chat_messages (
                id bigserial primary key,
                chat_id text not null references orion_chats(id) on delete cascade,
                role text not null,
                content_json text not null,
                created_at timestamptz default now()
            );
            """
        )
        await conn.execute(
            "create index if not exists idx_orion_chat_messages_chat_id on orion_chat_messages (chat_id, created_at asc);"
        )
        await conn.execute(
            "create index if not exists idx_orion_chat_messages_chat_id_id on orion_chat_messages (chat_id, id asc);"
        )
        # Chat search (see orion.kernel.chat_search): readable and folded text of each message.
        await conn.execute("alter table orion_chat_messages add column if not exists plain_text text")
        await conn.execute("alter table orion_chat_messages add column if not exists search_text text")
        await conn.execute(
            "create index if not exists idx_orion_chat_messages_unindexed on orion_chat_messages (id) "
            "where search_text is null"
        )
        # The turn a message belongs to ('' for none), so a window "before turn T" is found without opening JSON.
        await conn.execute("alter table orion_chat_messages add column if not exists turn_id text")
        await conn.execute(
            "create index if not exists idx_orion_chat_messages_turnless on orion_chat_messages (id) "
            "where turn_id is null"
        )
    except (asyncpg.exceptions.UniqueViolationError, asyncpg.exceptions.DuplicateTableError):
        # A failure after serialization indicates an actual schema problem.
        raise


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def fetch_setting_overrides(user_id: str) -> dict[str, str]:
    conn = await _connect()
    if conn is None:
        return {}
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            "select key, value from orion_settings where user_id = $1",
            user_id,
        )
        return {row["key"]: row["value"] for row in rows}
    finally:
        await conn.close()


async def initialize_stt_language_override(user_id: str, language: str) -> None:
    """First successful initialization wins; never overwrite a saved preference."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Database unavailable; STT language was not initialized")
    try:
        await _ensure_tables(conn)
        await conn.execute(
            """
            insert into orion_settings (user_id, key, value)
            values ($1, 'stt_language', $2)
            on conflict (user_id, key)
            do update set value = excluded.value, updated_at = now()
            where orion_settings.value is null or btrim(orion_settings.value) = ''
            """,
            user_id, language,
        )
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise RuntimeError("STT language initialization could not be saved") from exc
    finally:
        await conn.close()


async def upsert_setting_overrides(user_id: str, overrides: dict[str, str], require_db: bool = False) -> None:
    if not overrides:
        return
    conn = await _connect()
    if conn is None:
        if require_db:
            raise RuntimeError("Database unavailable; settings were not saved")
        return
    try:
        await _ensure_tables(conn)
        records = [(user_id, key, value) for key, value in overrides.items()]
        await conn.executemany(
            """
            insert into orion_settings (user_id, key, value)
            values ($1, $2, $3)
            on conflict (user_id, key)
            do update set value = excluded.value, updated_at = now()
            """,
            records,
        )
    finally:
        await conn.close()


async def insert_missing_setting_overrides(user_id: str, defaults: dict[str, str]) -> None:
    """Add newly introduced defaults without replacing administrator values."""
    if not defaults:
        return
    conn = await _connect()
    if conn is None:
        return
    try:
        await _ensure_tables(conn)
        records = [(user_id, key, value) for key, value in defaults.items()]
        await conn.executemany(
            """
            insert into orion_settings (user_id, key, value)
            values ($1, $2, $3)
            on conflict (user_id, key) do nothing
            """,
            records,
        )
    finally:
        await conn.close()


async def fetch_all_settings() -> dict[str, dict[str, str]]:
    conn = await _connect()
    if conn is None:
        return {}
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch("select user_id, key, value from orion_settings")
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            uid = row["user_id"]
            if uid not in result:
                result[uid] = {}
            result[uid][row["key"]] = row["value"]
        return result
    finally:
        await conn.close()


async def delete_setting_override(user_id: str, key: str) -> None:
    conn = await _connect()
    if conn is None:
        return
    try:
        await _ensure_tables(conn)
        await conn.execute(
            "delete from orion_settings where user_id = $1 and key = $2",
            user_id, key,
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user_by_username(username: str) -> dict | None:
    conn = await _connect()
    if conn is None:
        return None
    try:
        await _ensure_tables(conn)
        row = await conn.fetchrow(
            "select id, password_hash, created_at, is_active, avatar_updated_at"
            " from orion_users where id = $1",
            username,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def _update_user(query: str, *args) -> bool:
    conn = await _connect()
    if conn is None:
        raise RuntimeError("User database unavailable")
    try:
        await _ensure_tables(conn)
        return await conn.execute(query, *args) == "UPDATE 1"
    finally:
        await conn.close()


class UsernameTaken(Exception):
    pass


async def rename_user(old: str, new: str) -> None:
    """Moves the account to a new login name in one transaction (see api/services/account_rename.py).
    Names differing only in letter case count as taken, except the account's own."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("User database unavailable")
    try:
        await _ensure_tables(conn)
        async with conn.transaction():
            # Serializes renames and registrations of the same names.
            await conn.execute("select pg_advisory_xact_lock(51730102)")
            if await conn.fetchval("select 1 from orion_users where id = $1 for update", old) is None:
                raise LookupError(old)
            if await conn.fetchval("select 1 from orion_users where lower(id) = lower($1) and id <> $2", new, old):
                raise UsernameTaken(new)
            await conn.execute("update orion_users set id = $2 where id = $1", old, new)
            await conn.execute("update orion_settings set user_id = $2 where user_id = $1", old, new)
            await conn.execute("update orion_chats set user_id = $2 where user_id = $1", old, new)
            await conn.execute("update orion_invitations set used_by = $2 where used_by = $1", old, new)
            # Their tokens carry the old name; the caller gets a new session.
            await conn.execute("update orion_sessions set revoked_at = now() where user_id = $1 and revoked_at is null", old)
    finally:
        await conn.close()


async def set_user_password(username: str, password_hash: str) -> bool:
    """Every session of the account ends with it: they carry the old hash's credential."""
    return await _update_user("update orion_users set password_hash = $2 where id = $1", username, password_hash)


async def set_user_avatar(username: str, data: bytes | None, mime: str | None) -> bool:
    return await _update_user(
        "update orion_users set avatar = $2, avatar_mime = $3,"
        " avatar_updated_at = case when $2::bytea is null then null else now() end where id = $1",
        username, data, mime,
    )


async def get_user_avatar(username: str) -> tuple[str, bytes] | None:
    conn = await _connect()
    if conn is None:
        raise RuntimeError("User database unavailable")
    try:
        await _ensure_tables(conn)
        row = await conn.fetchrow("select avatar, avatar_mime from orion_users where id = $1", username)
        return (row["avatar_mime"], bytes(row["avatar"])) if row and row["avatar"] is not None else None
    finally:
        await conn.close()


async def create_user(username: str, password_hash: str) -> bool:
    conn = await _connect()
    if conn is None:
        return False
    try:
        await _ensure_tables(conn)
        await conn.execute(
            """
            insert into orion_users (id, password_hash, is_active)
            values ($1, $2, false)
            on conflict (id) do nothing
            """,
            username, password_hash,
        )
        return True
    except Exception:
        logger.exception("Failed to create user")
        return False
    finally:
        await conn.close()


async def list_active_users() -> list[dict]:
    conn = await _connect()
    if conn is None:
        raise RuntimeError('User database unavailable')
    try:
        await _ensure_tables(conn)
        return [dict(row) for row in await conn.fetch('SELECT id, created_at FROM orion_users WHERE is_active=true')]
    finally:
        await conn.close()


async def set_user_approval(username: str, approved: bool) -> bool:
    return await set_user_active(username, approved)


# ---------------------------------------------------------------------------
# Chats (Write-Through & Cache-Aside database layer)
# ---------------------------------------------------------------------------

async def upsert_chat(chat_id: str, user_id: str, title: str = "New Chat") -> None:
    """Insert a new chat or update its `updated_at` timestamp on conflict.

    Safe to call multiple times for the same chat_id (e.g. on every new
    message), which keeps `updated_at` fresh and the chat list ordered.
    """
    conn = await _connect()
    if conn is None:
        # The chat row is the only record of the chat now: a send that cannot write it is refused.
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        await conn.execute(
            """
            insert into orion_chats (id, user_id, title)
            values ($1, $2, $3)
            on conflict (id)
            do update set updated_at = now()
            """,
            chat_id, user_id, title,
        )
    finally:
        await conn.close()


async def get_assistant_chat_db(user_id: str) -> str | None:
    """The user's "Asistanla konuş" chat, if they have one."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        return await conn.fetchval(
            "select id from orion_chats where user_id = $1 and assistant order by updated_at desc limit 1",
            user_id,
        )
    finally:
        await conn.close()


async def set_assistant_chat_db(chat_id: str, user_id: str) -> None:
    """Make `chat_id` the user's only "Asistanla konuş" chat."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        await conn.execute(
            "update orion_chats set assistant = (id = $1) where user_id = $2 and (assistant or id = $1)",
            chat_id, user_id,
        )
    finally:
        await conn.close()


async def rename_chat_db(chat_id: str, title: str, updated_at: str, owner_id: str) -> None:
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        await conn.execute(
            """insert into orion_chats (id, user_id, title, updated_at)
            values ($1, $2, $3, $4)
            on conflict (id) do update
            set title = excluded.title, updated_at = excluded.updated_at""",
            chat_id, owner_id, title, datetime.fromisoformat(updated_at),
        )
    finally:
        await conn.close()


async def touch_chat_db(chat_id: str, updated_at: str) -> None:
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        await conn.execute(
            "update orion_chats set updated_at = $2 where id = $1",
            chat_id, datetime.fromisoformat(updated_at),
        )
    finally:
        await conn.close()


async def insert_messages(chat_id: str, messages: list[dict]) -> None:
    """Persist a batch of message dicts (role + full content) to PostgreSQL.

    Each dict is stored as-is in `content_json` so that complex payloads
    (multimodal content, thinking fields, etc.) are preserved exactly.
    """
    if not messages:
        return
    conn = await _connect()
    if conn is None:
        return
    try:
        await _ensure_tables(conn)
        records = [
            (chat_id, msg.get("role", "user"), json.dumps(msg), *_search_columns(msg), str(msg.get("turn_id") or ""))
            for msg in messages
        ]
        await conn.executemany(
            """
            insert into orion_chat_messages (chat_id, role, content_json, plain_text, search_text, turn_id)
            values ($1, $2, $3, $4, $5, $6)
            """,
            records,
        )
    finally:
        await conn.close()


def _search_columns(message: dict) -> tuple[str, str]:
    """(plain_text, search_text) of a message; empty, never NULL, once it has been looked at."""
    from orion.kernel.chat_search import fold, message_text
    text = message_text(message) or ''
    return text, fold(text)


async def index_unsearchable_messages(batch: int = 100) -> int:
    """Fill the search columns of messages saved before the chat search existed.

    Returns how many rows were filled; 0 when none are left (or without a database).
    """
    conn = await _connect()
    if conn is None:
        return 0
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            "select id, content_json from orion_chat_messages where search_text is null order by id limit $1",
            batch,
        )
        records = []
        for row in rows:
            try:
                message = json.loads(row["content_json"])
            except json.JSONDecodeError:
                message = {}
            records.append((row["id"], *_search_columns(message if isinstance(message, dict) else {})))
        if records:
            await conn.executemany(
                "update orion_chat_messages set plain_text = $2, search_text = $3 where id = $1", records)
        return len(records)
    finally:
        await conn.close()


async def index_message_turns(batch: int = 200) -> int:
    """Fill turn_id of messages saved before the column existed; returns how many (0: none left)."""
    conn = await _connect()
    if conn is None:
        return 0
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            "select id, content_json from orion_chat_messages where turn_id is null order by id limit $1", batch)
        records = []
        for row in rows:
            try:
                message = json.loads(row["content_json"])
            except json.JSONDecodeError:
                message = {}
            records.append((row["id"], str(message.get("turn_id") or "") if isinstance(message, dict) else ""))
        if records:
            await conn.executemany("update orion_chat_messages set turn_id = $2 where id = $1", records)
        return len(records)
    finally:
        await conn.close()


def _messages(rows) -> list[dict]:
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["content_json"]))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed message JSON")
    return result


async def get_chat_window_db(chat_id: str, limit: int | None, before_turn: str | None = None,
                             max_chars: int | None = None) -> tuple[list[dict], int] | None:
    """The newest messages of a chat (or those before `before_turn`), without reading the rest.

    The window is `limit` messages, or fewer once their readable text reaches `max_chars` (at least one);
    the second value is that size plus one when an older message exists (the client's "more above" sign).
    The messages returned start at the beginning of their turn, so failed-turn labels see whole turns; the
    caller keeps the last `size` of them. None: `before_turn` is not in this chat.
    """
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        end_id = None
        if before_turn is not None:
            end_id = await conn.fetchval(
                """select min(id) from orion_chat_messages where chat_id = $1
                   and (turn_id = $2 or (turn_id is null and strpos(content_json, $2) > 0))""",
                chat_id, before_turn)
            if end_id is None:
                return None
        heads = await conn.fetch(
            """select id, coalesce(length(plain_text), 0) as chars from orion_chat_messages
               where chat_id = $1 and ($2::bigint is null or id < $2) order by id desc limit $3""",
            chat_id, end_id, None if limit is None else limit + 1)
        if not heads:
            return [], 0
        size, chars = 0, 0
        for row in heads:
            if (limit is not None and size >= limit) or (max_chars and size and chars >= max_chars):
                break
            size, chars = size + 1, chars + row["chars"]
        older = size < len(heads)
        oldest = heads[size]["id"] if older else heads[size - 1]["id"]
        start = await conn.fetchval(
            """select max(id) from orion_chat_messages where chat_id = $1 and id <= $2 and role = 'user'""",
            chat_id, oldest)
        rows = await conn.fetch(
            """select content_json from orion_chat_messages
               where chat_id = $1 and id >= $2 and ($3::bigint is null or id < $3) order by id""",
            chat_id, start or 0, end_id)
        return _messages(rows), size + (1 if older else 0)
    finally:
        await conn.close()


async def get_chat_tail_db(chat_id: str, rows: int) -> tuple[list[dict], int]:
    """The last `rows` messages of a chat from the start of their first turn, and how many successful turns
    came before them (the model's history counts turns from the chat's start; see tool_history.model_history)."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        oldest = await conn.fetchval(
            """select min(id) from (select id from orion_chat_messages where chat_id = $1
               order by id desc limit $2) tail""", chat_id, rows)
        if oldest is None:
            return [], 0
        start = await conn.fetchval(
            "select max(id) from orion_chat_messages where chat_id = $1 and id <= $2 and role = 'user'",
            chat_id, oldest) or 0
        prior = await conn.fetchval(
            """select count(*) from orion_chat_messages where chat_id = $1 and id < $2 and role = 'user'
               and strpos(content_json, '"failed_turn": true') = 0""", chat_id, start)
        found = await conn.fetch(
            "select content_json from orion_chat_messages where chat_id = $1 and id >= $2 order by id",
            chat_id, start)
        return _messages(found), prior
    finally:
        await conn.close()


async def search_chat_messages_db(user_id: str, words: list[str], chats: int = 30, per_chat: int = 3) -> list[dict]:
    """Messages of the user that hold the words as one phrase, or else every longer word somewhere.

    Chats with the phrase come first, then the most recently used; in a chat,
    its phrase matches first, then the newest (at most `per_chat`). The
    assistant chat has its own page and is left out, as in the chat list.
    """
    from orion.kernel.chat_search import SEARCHED_ROLES, like_pattern, loose_words, phrase_pattern
    if not words:
        return []
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            """
            select chat_id, title, role, plain_text, created_at from (
                select found.*,
                    dense_rank() over (order by chat_phrase desc, updated_at desc, chat_id) as chat_rank
                from (
                    select m.chat_id, c.title, c.updated_at, m.role, m.plain_text, m.created_at,
                        row_number() over (
                            partition by m.chat_id order by (m.search_text ~ $3) desc, m.id desc) as position,
                        bool_or(m.search_text ~ $3) over (partition by m.chat_id) as chat_phrase
                    from orion_chat_messages m
                    join orion_chats c on c.id = m.chat_id
                    where c.user_id = $1 and not c.assistant
                        and m.role = any($2::text[])
                        and (m.search_text ~ $3
                            or (cardinality($4::text[]) > 0 and m.search_text like all($4::text[])))
                ) found
                where position <= $6
            ) ranked
            where chat_rank <= $5
            order by chat_rank, position
            """,
            user_id, list(SEARCHED_ROLES), phrase_pattern(words),
            [like_pattern(word) for word in loose_words(words)], chats, per_chat,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def search_chat_titles_db(user_id: str, words: list[str], limit: int = 20) -> list[dict]:
    """Chats whose name holds the words as one phrase, most recently used first.

    Names are folded like chat_search.fold (the four Turkish i's are one letter, then lower case). Clients
    no longer hold the whole chat list, so they cannot match names themselves.
    """
    from orion.kernel.chat_search import phrase_pattern
    if not words:
        return []
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            """
            select id as chat_id, title, updated_at from orion_chats
            where user_id = $1 and not assistant and lower(translate(title, 'İIı', 'iii')) ~ $2
            order by updated_at desc, id desc limit $3
            """,
            user_id, phrase_pattern(words), limit,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_chat_history_db(chat_id: str) -> list[dict]:
    """Fetch all messages for a chat from PostgreSQL in chronological order.

    Returns an empty list if Postgres is unavailable or the chat has no
    messages yet. Callers use this as the Cache-Aside hydration source.
    """
    conn = await _connect()
    if conn is None:
        return []
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            "select content_json from orion_chat_messages where chat_id = $1 order by id asc",
            chat_id,
        )
        result = []
        for row in rows:
            try:
                result.append(json.loads(row["content_json"]))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed message JSON for chat %s", chat_id)
        return result
    finally:
        await conn.close()


async def get_turn_audio_db(chat_id: str, turn_id: str):
    conn = await _connect()
    if conn is None:
        return None
    try:
        await _ensure_tables(conn)
        value = await conn.fetchval("""
            select content_json from orion_chat_messages
            where chat_id=$1 and role='assistant'
              -- A plain text match first: only the row(s) naming this turn are decoded as JSON.
              and strpos(content_json, $2) > 0
              and content_json::jsonb->>'turn_id'=$2
              and content_json::jsonb ? 'audio'
            order by id desc limit 1
        """, chat_id, turn_id)
        return json.loads(value).get('audio') if value else None
    finally:
        await conn.close()


async def get_chat_db(chat_id: str) -> dict | None:
    """Return persistent metadata for one chat, if it exists."""
    conn = await _connect()
    if conn is None:
        return None
    try:
        await _ensure_tables(conn)
        row = await conn.fetchrow(
            """
            select id as chat_id, user_id, title, created_at, updated_at
            from orion_chats
            where id = $1
            """,
            chat_id,
        )
        if row is None:
            return None
        return {
            "chat_id": row["chat_id"],
            "user_id": row["user_id"],
            "name": row["title"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
    finally:
        await conn.close()


def _chat_row(row) -> dict:
    return {
        "chat_id": row["chat_id"],
        "user_id": row["user_id"],
        "name": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


async def get_user_chats_db(user_id: str, *, limit: int | None = None, before: tuple[datetime, str] | None = None,
                            include_assistant: bool = True) -> list[dict]:
    """A user's chats (metadata only), most recently updated first; ties by id, so pages never skip one.

    With `limit`, one page; `before` is the (updated_at, chat_id) of the last chat of the previous page. The
    (user_id, updated_at desc) index serves both. Without it, every chat (account rename needs them all).
    """
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            """
            select id as chat_id, user_id, title, created_at, updated_at
            from orion_chats
            where user_id = $1 and ($2 or not assistant)
              and ($3::timestamptz is null or (updated_at, id) < ($3::timestamptz, $4::text))
            order by updated_at desc, id desc
            limit $5
            """,
            user_id, include_assistant, before[0] if before else None, before[1] if before else None, limit,
        )
        return [_chat_row(row) for row in rows]
    finally:
        await conn.close()


async def get_all_chats_db() -> list[dict]:
    """Every chat's metadata, for the admin panel."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch("select id as chat_id, user_id, title, created_at, updated_at from orion_chats "
                                "order by updated_at desc, id desc")
        return [_chat_row(row) for row in rows]
    finally:
        await conn.close()


async def delete_chat_db(chat_id: str) -> None:
    """Permanently delete a chat and all its messages from PostgreSQL.

    The FK cascade on `orion_chat_messages` ensures messages are removed
    automatically when the parent chat row is deleted.
    """
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Postgres is unavailable")
    try:
        await _ensure_tables(conn)
        await conn.execute("delete from orion_chats where id = $1", chat_id)
    finally:
        await conn.close()


async def delete_failed_turn_db(chat_id: str, key: str, *, delete_empty: bool,
                                removed_media=(), retained_media=()) -> bool:
    """Delete one failed exchange atomically; preserve all other messages."""
    from orion.kernel.chat_history import remove_failed_turn, referenced_media_ids
    conn = await _connect()
    if conn is None:
        raise RuntimeError('Postgres is unavailable')
    try:
        await _ensure_tables(conn)
        async with conn.transaction():
            await conn.fetchval('select id from orion_chats where id=$1 for update', chat_id)
            rows = await conn.fetch(
                'select id, content_json from orion_chat_messages where chat_id=$1 order by id', chat_id)
            messages = [json.loads(row['content_json']) for row in rows]
            _, removed = remove_failed_turn(messages, key)
            removed_ids = {id(message) for message in removed}
            ids = [row['id'] for row, message in zip(rows, messages) if id(message) in removed_ids]
            if ids:
                await conn.execute('delete from orion_chat_messages where chat_id=$1 and id=any($2::bigint[])', chat_id, ids)
            media_ids = (set(removed_media) | referenced_media_ids(removed)) - set(retained_media)
            if media_ids:
                from orion.kernel.media_store import ensure_table
                await ensure_table(conn)
                await conn.execute('''delete from orion_chat_media m where m.chat_id=$1
                    and m.id=any($2::text[]) and not exists (
                        select 1 from orion_chat_messages msg
                        where msg.chat_id=m.chat_id and strpos(msg.content_json,m.id)>0)''',
                    chat_id, list(media_ids))
            if delete_empty:
                result = await conn.execute('''delete from orion_chats where id=$1
                    and not exists(select 1 from orion_chat_messages where chat_id=$1)''', chat_id)
                return result == 'DELETE 1'
            return False
    finally:
        await conn.close()


async def prune_failed_chat_db(chat_id: str) -> bool:
    """Remove failed message groups and delete the chat only if it is empty."""
    from orion.kernel.chat_history import successful_history
    conn = await _connect()
    if conn is None:
        raise RuntimeError('Postgres is unavailable')
    try:
        await _ensure_tables(conn)
        async with conn.transaction():
            rows = await conn.fetch(
                'select id, content_json from orion_chat_messages where chat_id=$1 order by id', chat_id)
            messages = [json.loads(row['content_json']) for row in rows]
            keep = {id(message) for message in successful_history(messages)}
            removed = [row['id'] for row, message in zip(rows, messages) if id(message) not in keep]
            if removed:
                await conn.execute('delete from orion_chat_messages where chat_id=$1 and id=any($2::bigint[])', chat_id, removed)
            result = await conn.execute('''delete from orion_chats where id=$1
                and not exists(select 1 from orion_chat_messages where chat_id=$1)''', chat_id)
            return result == 'DELETE 1'
    finally:
        await conn.close()


async def replace_initial_chat_title(chat_id: str, expected: str, title: str) -> bool:
    """Do not overwrite a title the user renamed while generation was running."""
    conn = await _connect()
    if conn is None:
        return False
    try:
        result = await conn.execute(
            "update orion_chats set title = $3 where id = $1 and title = $2",
            chat_id, expected, title,
        )
        return result == "UPDATE 1"
    finally:
        await conn.close()


async def set_user_active(username: str, active: bool) -> bool:
    conn = await _connect()
    if conn is None:
        raise RuntimeError("User database unavailable")
    try:
        await _ensure_tables(conn)
        async with conn.transaction():
            result = await conn.execute("update orion_users set is_active = $2 where id = $1", username, active)
            if not active:
                # Reactivation must not revive the sessions that deactivation ended.
                await conn.execute("update orion_sessions set revoked_at = now() where user_id = $1 and revoked_at is null", username)
        return result == "UPDATE 1"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Account sessions
# ---------------------------------------------------------------------------

async def _session_connection() -> asyncpg.Connection:
    # Unavailable storage is not an ended session: callers answer 503, never 401.
    conn = await _connect()
    if conn is None:
        raise RuntimeError("Session database unavailable")
    try:
        await _ensure_tables(conn)
    except BaseException:
        await conn.close()  # Not left open for the garbage collector.
        raise
    return conn


async def create_session(session_id: str, user_id: str, device_id: str | None, credential: str, forget_after) -> None:
    conn = await _session_connection()
    try:
        await conn.execute(
            "insert into orion_sessions (id, user_id, device_id, credential) values ($1, $2, $3, $4)",
            session_id, user_id, device_id, credential,
        )
        await conn.execute(
            "delete from orion_sessions where last_seen_at < now() - $1::interval or revoked_at < now() - $1::interval",
            forget_after,
        )
    finally:
        await conn.close()


async def get_session(session_id: str) -> dict | None:
    """The session with its account's current password hash and activation."""
    conn = await _session_connection()
    try:
        row = await conn.fetchrow(
            """
            select s.id, s.user_id, s.device_id, s.credential, s.last_seen_at, s.revoked_at,
                   u.password_hash, u.is_active
            from orion_sessions s join orion_users u on u.id = s.user_id
            where s.id = $1
            """,
            session_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def touch_session(session_id: str) -> None:
    conn = await _session_connection()
    try:
        await conn.execute("update orion_sessions set last_seen_at = now() where id = $1 and revoked_at is null", session_id)
    finally:
        await conn.close()


async def revoke_sessions(*, session_id: str | None = None, user_id: str | None = None, device_id: str | None = None) -> None:
    column, value = next((name, value) for name, value in
                         (('id', session_id), ('user_id', user_id), ('device_id', device_id)) if value)
    conn = await _session_connection()
    try:
        await conn.execute(f"update orion_sessions set revoked_at = now() where {column} = $1 and revoked_at is null", value)
    finally:
        await conn.close()


async def create_local_user(username: str, password_hash: str) -> bool:
    """Create an active account atomically on the private local listener."""
    conn = await _connect()
    if conn is None:
        raise RuntimeError('User database unavailable')
    try:
        await _ensure_tables(conn)
        result = await conn.execute(
            'insert into orion_users (id,password_hash,is_active) values ($1,$2,true) on conflict (id) do nothing',
            username, password_hash,
        )
        return result == 'INSERT 0 1'
    finally:
        await conn.close()
