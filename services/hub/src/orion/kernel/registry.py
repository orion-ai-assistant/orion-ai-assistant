from __future__ import annotations

import json
import logging

import asyncpg

from orion.kernel.environment import get_postgres_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _connect() -> asyncpg.Connection | None:
    """Return an open asyncpg connection or None on failure."""
    try:
        database_url = get_postgres_url()
    except RuntimeError as exc:
        logger.info("Postgres not configured: %s", exc)
        return None

    try:
        return await asyncpg.connect(database_url)
    except Exception:
        logger.exception("Failed to connect to Postgres")
        return None


async def _ensure_tables(conn: asyncpg.Connection) -> None:
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
            is_active boolean default true
        );
        """
    )
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


async def upsert_setting_overrides(user_id: str, overrides: dict[str, str]) -> None:
    if not overrides:
        return
    conn = await _connect()
    if conn is None:
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
            insert into orion_users (id, password_hash)
            values ($1, $2)
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
            "select content_json from orion_chat_messages where chat_id = $1 order by created_at asc",
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
                "title": row["title"],
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
        return
    try:
        await _ensure_tables(conn)
        await conn.execute("delete from orion_chats where id = $1", chat_id)
    finally:
        await conn.close()
