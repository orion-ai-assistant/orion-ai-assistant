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

async def _connect(retries: int = 3, delay: float = 0.5, timeout: float = 2.0) -> asyncpg.Connection | None:
    """Return an open asyncpg connection or None on failure, with retries and timeout."""
    try:
        database_url = get_postgres_url()
    except RuntimeError as exc:
        logger.info("Postgres not configured: %s", exc)
        return None

    for attempt in range(retries):
        try:
            return await asyncpg.connect(database_url, timeout=timeout)
        except Exception as e:
            if attempt == retries - 1:
                logger.warning("Failed to connect to Postgres after %d attempts: %s", retries, e)
                return None
            logger.debug("Postgres connection retry (%d/%d): %s. Waiting %.1fs...", attempt + 1, retries, e, delay)
            await asyncio.sleep(delay)
    return None


async def check_db_health(timeout: float = 1.0) -> bool:
    """Quick check if Postgres database is reachable and accepting queries."""
    conn = await _connect(retries=1, delay=0.1, timeout=timeout)
    if conn is None:
        return False
    try:
        await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        await conn.close()


async def _ensure_tables(conn: asyncpg.Connection) -> None:
    # Multiple native listeners and the Worker initialize the same fresh DB.
    # IF NOT EXISTS alone does not serialize PostgreSQL catalog creation.
    await conn.execute('SELECT pg_advisory_lock(51730101)')
    try:
        await _create_tables(conn)
    finally:
        await conn.execute('SELECT pg_advisory_unlock(51730101)')


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
        # --- Chat Messages ---
        await conn.execute("alter table orion_chats add column if not exists tool_selection jsonb")
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
            "select id, password_hash, created_at, is_active from orion_users where id = $1",
            username,
        )
        return dict(row) if row else None
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
        return
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
            (chat_id, msg.get("role", "user"), json.dumps(msg))
            for msg in messages
        ]
        await conn.executemany(
            """
            insert into orion_chat_messages (chat_id, role, content_json)
            values ($1, $2, $3)
            """,
            records,
        )
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


async def get_user_chats_db(user_id: str) -> list[dict]:
    """Return a user's chat list (metadata only, no messages) ordered by
    most-recently-updated first.
    """
    conn = await _connect()
    if conn is None:
        return []
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch(
            """
            select id as chat_id, user_id, title, created_at, updated_at
            from orion_chats
            where user_id = $1
            order by updated_at desc
            """,
            user_id,
        )
        return [
            {
                "chat_id": row["chat_id"],
                "user_id": row["user_id"],
                "name": row["title"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]
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
    await _ensure_tables(conn)
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
