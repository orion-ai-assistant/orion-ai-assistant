"""Minimal memory: chunks of a saved chat with their embedding, searched by the `hafizada_ara` tool.

Rows carry their own text and do not reference the chat, so deleting the chat keeps them. Rows of another
embedding model (`vector_space`) are never compared with the current one, only counted."""
from __future__ import annotations

import logging

from orion.kernel import registry
from orion.kernel.chat_search import message_text

logger = logging.getLogger(__name__)

SETUP_MESSAGE = "Hafıza için Bileşenler'de embedding bileşenini kur ve aç."
NO_VECTOR_MESSAGE = "Hafıza kullanılamıyor: veritabanında pgvector (vector eklentisi) yok."
LIMIT = 5


class MemoryUnavailable(Exception):
    """The memory cannot work now; the text is the Turkish answer for the user."""


_table_ready = False


async def ensure_memory_table(conn) -> None:
    global _table_ready
    if _table_ready:
        return
    try:
        if not await conn.fetchval("select 1 from pg_extension where extname = 'vector'"):
            raise MemoryUnavailable(NO_VECTOR_MESSAGE)
        await conn.execute("select pg_advisory_lock(51730102)")
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
            await conn.execute("create index if not exists idx_orion_memory_user on orion_memory (user_id, vector_space)")
        finally:
            await conn.execute("select pg_advisory_unlock(51730102)")
    except MemoryUnavailable:
        raise
    except Exception as exc:
        logger.warning("Memory table could not be prepared: %s", exc)
        raise MemoryUnavailable(NO_VECTOR_MESSAGE) from exc
    _table_ready = True


async def _connect():
    conn = await registry._connect()
    if conn is None:
        raise MemoryUnavailable("Hafıza şu an kullanılamıyor: veritabanına ulaşılamadı.")
    return conn


async def embedding_target(settings) -> tuple[object, str, bool]:
    """(settings aimed at the embedding model, its vector space, whether it is local)."""
    from orion.kernel.router_models import get_router_catalog
    try:
        models = [m for m in (await get_router_catalog(include_voices=False))["models"] if m.get("capability") == "embed"]
    except Exception as exc:
        raise MemoryUnavailable(SETUP_MESSAGE) from exc
    if not models:
        raise MemoryUnavailable(SETUP_MESSAGE)
    chosen = next((m for m in models if m.get("provider") == "local"), models[0])
    local = chosen.get("provider") == "local"
    if local:
        from services.shared import local_parts
        space = local_parts.embedding_profile().get("vector_space") or "local"
    else:
        space = f"cloud:{chosen['name']}"
    return settings.model_copy(update={"router_model_group": chosen["name"]}), space, local


async def _embed(text: str, settings, kind: str, local: bool) -> list[list[float]]:
    from orion.worker.services.router import embed_text
    try:
        vectors = await embed_text(text, settings, kind)
    except Exception as exc:
        logger.warning("Memory embedding failed: %s", exc)
        raise MemoryUnavailable(SETUP_MESSAGE if local else f"Embedding alınamadı: {exc}") from exc
    if any(len(v) != 768 for v in vectors):
        raise MemoryUnavailable("Embedding modeli 768 boyutlu değil; hafıza şimdilik yalnız 768 boyutlu modelleri destekler.")
    return vectors


def _vector(values) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def chat_text(messages: list[dict]) -> str:
    """The readable user and assistant text of a chat, one paragraph per message."""
    paragraphs = []
    for message in messages:
        text = message_text(message)
        if text:
            paragraphs.append(f"{'Kullanıcı' if message.get('role') == 'user' else 'Asistan'}: {text}")
    return "\n\n".join(paragraphs)


async def save_chat(user_id: str, chat_id: str, title: str, settings) -> int:
    """Chunk, embed and store the chat; returns how many chunks were saved."""
    from orion.worker.services.router import chunk_text
    chunks = chunk_text(chat_text(await registry.get_chat_history_db(chat_id)))
    if not chunks:
        raise MemoryUnavailable("Bu sohbette kaydedilecek metin yok.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        rows = []
        for chunk in chunks:
            for vector in await _embed(chunk, target, "document", local):
                rows.append((user_id, chunk, space, _vector(vector), title or None))
        await conn.executemany(
            "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title) "
            "values ($1, $2, $3, $4::vector, 'chat_save', $5)", rows)
        return len(rows)
    finally:
        await conn.close()


async def search(user_id: str, query: str, settings) -> dict:
    """The closest saved texts of this user in the current vector space (plus a count of other spaces)."""
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        vector = (await _embed(query, target, "query", local))[0]
        rows = await conn.fetch(
            "select text, created_at from orion_memory where user_id = $1 and vector_space = $2 "
            "order by embedding <=> $3::vector limit $4", user_id, space, _vector(vector), LIMIT)
        other = await conn.fetchval(
            "select count(*) from orion_memory where user_id = $1 and vector_space <> $2", user_id, space)
        return {"rows": [{"text": r["text"], "date": r["created_at"].date().isoformat()} for r in rows],
                "other_spaces": int(other or 0)}
    finally:
        await conn.close()
