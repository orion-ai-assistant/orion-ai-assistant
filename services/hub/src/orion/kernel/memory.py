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
SCHEMA_LOCK = 51730201  # Its own number: 51730102-51730104 belong to the media tables and their migration.


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
            await conn.execute("create index if not exists idx_orion_memory_user on orion_memory (user_id, vector_space)")
        finally:
            await conn.execute("select pg_advisory_unlock($1)", SCHEMA_LOCK)
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
        return await _insert_new(conn, rows, "chat_save")
    finally:
        await conn.close()


INSERT_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title) "
    "select $1, $2, $3, $4::vector, $6, $5 where not exists "
    "(select 1 from orion_memory where user_id = $1 and vector_space = $3 and text = $2)")
MAX_NOTE = 4000


async def _insert_new(conn, rows, source_kind: str) -> int:
    """Insert rows, skipping a text this user already has in the same vector space; returns how many were new."""
    seen, fresh = set(), []
    for row in rows:
        if (row[1], row[2]) in seen:
            continue
        seen.add((row[1], row[2]))
        existing = await conn.fetchval(
            "select 1 from orion_memory where user_id = $1 and vector_space = $2 and text = $3", row[0], row[2], row[1])
        if not existing:
            fresh.append((*row, source_kind))
    if fresh:
        await conn.executemany(INSERT_SQL, fresh)
    return len(fresh)


async def add_note(user_id: str, text: str, settings, source_kind: str = "manual") -> int:
    """Embed and store a text the user (or the assistant on the user's request) wants remembered."""
    from orion.worker.services.router import chunk_text
    text = (text or "").strip()
    if not text:
        raise ValueError("Hafızaya eklenecek metin boş.")
    if len(text) > MAX_NOTE:
        raise ValueError(f"Not çok uzun: en fazla {MAX_NOTE} karakter olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        rows = []
        for piece in chunk_text(text) or [text]:
            for vector in await _embed(piece, target, "document", local):
                rows.append((user_id, piece, space, _vector(vector), None))
        return await _insert_new(conn, rows, source_kind)
    finally:
        await conn.close()


async def current_space(settings) -> str | None:
    try:
        return (await embedding_target(settings))[1]
    except MemoryUnavailable:
        return None


KINDS = ("text", "image", "audio")
MIN_SCORE = 0.45  # Below this a vector neighbour is noise, not a match by meaning.
TEXT_CAP = 500
_COLUMNS = "id, text, kind, vector_space, source_kind, source_chat_title, created_at"


def _filters(vector_space: str, kind: str, first: int) -> tuple[str, list]:
    """SQL conditions (after the user's) for the page's model and kind filters, numbered from `first`."""
    sql, args = "", []
    if vector_space and vector_space != "all":
        args.append(vector_space)
        sql += f" and vector_space = ${first + len(args) - 1}"
    if kind and kind != "all":
        args.append(kind)
        sql += f" and kind = ${first + len(args) - 1}"
    return sql, args


def _item(r, space, score=None, match=None) -> dict:
    item = {"id": r["id"], "text": r["text"], "kind": r["kind"], "vector_space": r["vector_space"],
            "source_kind": r["source_kind"], "source_chat_title": r["source_chat_title"],
            "created_at": r["created_at"].isoformat(), "searchable": r["vector_space"] == space}
    if match:
        item["match"] = match
        item["score"] = None if score is None else round(float(score), 4)
    return item


async def _query_vector(q: str, settings) -> tuple[str | None, str | None, str | None]:
    """(current vector space, query vector, reason when the search by meaning is off)."""
    try:
        target, space, local = await embedding_target(settings)
    except MemoryUnavailable as exc:
        return None, None, str(exc)
    try:
        return space, _vector((await _embed(q, target, "query", local))[0]), None
    except MemoryUnavailable as exc:
        return space, None, str(exc)


async def list_items(user_id: str, settings, q: str = "", limit: int = 50, offset: int = 0,
                     vector_space: str = "all", kind: str = "all") -> dict:
    """A page of this user's rows and a summary of all of them. Without `q`: newest first. With `q`: matches by
    meaning (current vector space, best score first) then plain-text-only matches; each says its `match`."""
    q = (q or "").strip()
    kind = kind if kind in KINDS else "all"
    vector_space = vector_space or "all"
    semantic, reason, vector = None, None, None
    if q:
        space, vector, reason = await _query_vector(q, settings)
        semantic = vector is not None
    else:
        space = await current_space(settings)
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        if q:
            meaning = []
            if vector is not None and vector_space in ("all", space):
                extra, args = _filters("all", kind, 4)
                meaning = await conn.fetch(
                    f"select {_COLUMNS}, 1 - (embedding <=> $3::vector) as score from orion_memory "
                    f"where user_id = $1 and vector_space = $2{extra} order by embedding <=> $3::vector limit {TEXT_CAP}",
                    user_id, space, vector, *args)
                meaning = [r for r in meaning if float(r["score"]) >= MIN_SCORE]
            extra, args = _filters(vector_space, kind, 3)
            texts = await conn.fetch(
                f"select {_COLUMNS} from orion_memory where user_id = $1 and text ilike $2{extra} "
                f"order by created_at desc, id desc limit {TEXT_CAP}", user_id, f"%{q}%", *args)
            text_ids = {r["id"] for r in texts}
            meaning_ids = {r["id"] for r in meaning}
            merged = [_item(r, space, r["score"], "both" if r["id"] in text_ids else "meaning") for r in meaning]
            merged += [_item(r, space, None, "text") for r in texts if r["id"] not in meaning_ids]
            items, matching = merged[offset:offset + limit], len(merged)
        else:
            extra, args = _filters(vector_space, kind, 4)
            rows = await conn.fetch(
                f"select {_COLUMNS} from orion_memory where user_id = $1{extra} order by created_at desc, id desc "
                f"limit $2 offset $3", user_id, limit, offset, *args)
            extra, args = _filters(vector_space, kind, 2)
            matching = await conn.fetchval(f"select count(*) from orion_memory where user_id = $1{extra}", user_id, *args)
            items = [_item(r, space) for r in rows]
        spaces = await conn.fetch(
            "select vector_space as key, count(*) as n from orion_memory where user_id = $1 group by vector_space", user_id)
        kinds = await conn.fetch(
            "select kind as key, count(*) as n from orion_memory where user_id = $1 group by kind", user_id)
    finally:
        await conn.close()
    per_space = {r["key"]: int(r["n"]) for r in spaces}
    total = sum(per_space.values())
    result = {
        "items": items,
        "matching": int(matching or 0),
        "summary": {"total": total, "per_vector_space": per_space,
                    "per_kind": {r["key"]: int(r["n"]) for r in kinds}, "current_vector_space": space,
                    "not_searchable": total - per_space.get(space, 0) if space else total},
    }
    if q:
        result["semantic"] = semantic
        if reason:
            result["semantic_reason"] = reason
    return result


async def delete_items(user_id: str, ids: list[int]) -> list[dict]:
    """Delete this user's rows among `ids`; returns the deleted rows (id, text). Other users' ids are ignored."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return []
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        rows = await conn.fetch(
            "delete from orion_memory where user_id = $1 and id = any($2::bigint[]) returning id, text", user_id, ids)
    finally:
        await conn.close()
    return [{"id": r["id"], "text": r["text"]} for r in rows]


async def search(user_id: str, query: str, settings) -> dict:
    """The closest saved texts of this user in the current vector space (plus a count of other spaces)."""
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        vector = (await _embed(query, target, "query", local))[0]
        rows = await conn.fetch(
            "select id, text, created_at from orion_memory where user_id = $1 and vector_space = $2 "
            "order by embedding <=> $3::vector limit $4", user_id, space, _vector(vector), LIMIT)
        other = await conn.fetchval(
            "select count(*) from orion_memory where user_id = $1 and vector_space <> $2", user_id, space)
        return {"rows": [{"id": r["id"], "text": r["text"], "date": r["created_at"].date().isoformat()} for r in rows],
                "other_spaces": int(other or 0)}
    finally:
        await conn.close()
