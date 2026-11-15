"""Minimal memory: chunks of a saved chat with their embedding, searched by the `hafizada_ara` tool.

Rows carry their own text, so deleting the chat keeps them; `source_chat_id`/`source_message_id` only point back at
where a row came from (the chat may be gone). Image and audio rows (kind 'image'/'audio') carry a short label as
their text and `media_id`: a link of owner 'memory' keeps the file after the chat is deleted, and deleting the row
drops that link. Rows of another embedding model (`vector_space`) are never compared with the current one.

`group_id` ties the rows saved together (one chat save, one note, one file) into one memory: the page shows a group
as one card. A copy of a row made for another embedding model (memory_move) keeps the group and names its original
in `copied_from`, so `coalesce(copied_from, id)` (CONTENT_KEY) is the same for a row and all its copies."""
from __future__ import annotations

import logging
import re

from orion.kernel import registry
from orion.kernel.chat_search import message_text

logger = logging.getLogger(__name__)

SETUP_MESSAGE = "Hafıza için Bileşenler'de embedding bileşenini kur ve aç."
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
            await conn.execute("create index if not exists idx_orion_memory_user on orion_memory (user_id, vector_space)")
            for column in ("group_id text", "copied_from bigint", "source_chat_id text", "source_message_id bigint",
                           "item_id text", "whole boolean not null default false", "span_start real", "span_end real",
                           "track text", "caption text", "item_order bigint"):
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
            from orion.kernel.memory_groups import backfill_groups
            await backfill_groups(conn)
        finally:
            await conn.execute("select pg_advisory_unlock($1)", SCHEMA_LOCK)
    except MemoryUnavailable:
        raise
    except Exception as exc:
        logger.warning("Memory table could not be prepared: %s", exc)
        raise MemoryUnavailable(NO_VECTOR_MESSAGE) from exc
    await _enable_trigrams(conn)
    _table_ready = True


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
        if not await conn.fetchval("select 1 from orion_memory where user_id = $1 and id = $2 and group_id is null",
                                   user_id, row_id):
            raise LookupError(group)
        fresh = new_group()
        await conn.execute("update orion_memory set group_id = $3 where user_id = $1 and group_id is null "
                           "and (id = $2 or copied_from = $2)", user_id, row_id, fresh)
        return fresh
    if not await conn.fetchval("select 1 from orion_memory where user_id = $1 and group_id = $2 limit 1",
                               user_id, group):
        raise LookupError(group)
    return group


CONTENT_KEY = "coalesce(copied_from, id)"


def _vector(values) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def chat_paragraphs(messages: list[dict], ids: list | None = None) -> list[tuple[object, str]]:
    """(message id, paragraph) of each readable user and assistant message; ids are None without `ids`."""
    paragraphs = []
    for index, message in enumerate(messages):
        text = message_text(message)
        if text:
            paragraphs.append((ids[index] if ids else None,
                               f"{'Kullanıcı' if message.get('role') == 'user' else 'Asistan'}: {text}"))
    return paragraphs


def chat_text(messages: list[dict]) -> str:
    """The readable user and assistant text of a chat, one paragraph per message."""
    return "\n\n".join(text for _, text in chat_paragraphs(messages))


def chunk_anchor(chunk: str, paragraphs: list[tuple[object, str]]):
    """The id of the message a chunk starts with: the first paragraph whose start is in the chunk, else the one
    the chunk starts inside (a long paragraph split at sentences)."""
    for message_id, text in paragraphs:
        head = text[:80].strip()
        if head and head in chunk:
            return message_id
    head = chunk[:60].strip()
    for message_id, text in paragraphs:
        if head and head in text:
            return message_id
    return None


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
        return int(local_parts.embedding_profile().get("context") or 0) or None
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
    window = window or mp.audio_window(embedding_context())
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
    "span_end, track, caption, item_order) "
    "values ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20) "
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
        row_id = await conn.fetchval(
            ROW_SQL, user_id, piece["text"][:1000] if item["kind"] != "text" or piece.get("whole") else piece["text"],
            space, _vector(piece["vector"]), item.get("source_kind") or "manual", item.get("title") or None,
            item["kind"], item.get("media_id"), item.get("mime"), piece.get("seconds"), group, item.get("chat_id"),
            piece.get("message_id", item.get("message_id")), item_id, bool(piece.get("whole")), piece.get("span_start"), piece.get("span_end"),
            piece.get("track"), (item.get("caption") or None), order)
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
            row["span_end"], row["track"], caption or None, order)
        await link_media(media_id, "memory", str(row_id), conn=conn)
    return len(rows)


async def text_item_pieces(conn, user_id: str, space: str, text: str, target, local: bool,
                           skip_known: bool = True, group: str | None = None) -> tuple[list[dict], int]:
    """The pieces of a text (memory_pieces.text_pieces: each core embedded with its window). A core already in
    the memory `group` is left out; one this user has in another memory takes that row's vector (not embedded
    again, the text relates the two memories). Returns (pieces, cores left out)."""
    import json
    from orion.kernel.memory_pieces import text_pieces
    pieces, known = [], 0
    for core, window in text_pieces(text):
        if skip_known:
            row = await conn.fetchrow(
                "select group_id, embedding::text as embedding from orion_memory where user_id = $1 "
                "and vector_space = $2 and text = $3 and media_id is null and not whole "
                "order by (group_id = $4) desc nulls last limit 1", user_id, space, core, group or "")
            if row and group and row["group_id"] == group:
                known += 1
                continue
            if row and group:
                pieces.append({"text": core, "vector": json.loads(row["embedding"])})
                continue
            if row:
                known += 1
                continue
        for vector in await _embed(window, target, "document", local):
            pieces.append({"text": core, "vector": vector})
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
                                   "and media_id = $3 limit 1", user_id, group, item["media_id"]):
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
    if not text.strip() and not items:
        raise MemoryUnavailable("Bu sohbette kaydedilecek metin yok.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        group = new_group()
        saved = 0
        if text.strip():
            pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group)
            if pieces:
                for piece in pieces:
                    piece["message_id"] = chunk_anchor(piece["text"], paragraphs)
                item = {"kind": "text", "source_kind": "chat_save", "title": title, "chat_id": chat_id,
                        "message_id": pieces[0]["message_id"]}
                async with conn.transaction():
                    saved += await insert_item(conn, user_id, space, group, item, pieces)
        skipped, warnings, jobs = {}, [], []
        if items:
            support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
            more, skipped, warnings, jobs = await _save_media(conn, user_id, chat_id, title, items, space, target,
                                                              support, group, settings)
            saved += more
        return {"saved": saved, "skipped": skipped, "warnings": warnings, "jobs": jobs, "group_id": group}
    finally:
        await conn.close()


MAX_NOTE = 4000


async def add_note(user_id: str, text: str, settings, source_kind: str = "manual") -> int:
    """Embed and store a text the user (or the assistant on the user's request) wants remembered; returns how many
    rows were new."""
    return (await add_note_to(user_id, text, settings, source_kind))["added"]


async def add_note_to(user_id: str, text: str, settings, source_kind: str = "manual", group: str | None = None) -> dict:
    """A note as a new memory, or an item added to the memory `group` (LookupError when this user has no such
    memory). {"added": new rows, "group_id"}."""
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
        pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group)
        added = 0
        if pieces:
            async with conn.transaction():
                added = await insert_item(conn, user_id, space, group, {"kind": "text", "source_kind": source_kind},
                                          pieces)
        return {"added": added, "group_id": group}
    finally:
        await conn.close()


TEXT_FILE_TYPES = (".txt", ".md", ".markdown")
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
    caption = " ".join((caption or "").split())[:300]
    if kind == "text":
        text = text_of_file(raw)
    elif len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("Dosya çok büyük: en fazla 64 MB olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        group = await join_group(conn, user_id, group)
        if kind == "text":
            pieces, _ = await text_item_pieces(conn, user_id, space, text, target, local, group=group)
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
                               "and media_id = $3 limit 1", user_id, group, media_id):
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


KINDS = ("text", "image", "audio", "video")
MAX_QUERY_CHARS = 2000  # A pasted passage searches by its first 2000 characters (about 500 tokens: one query).
TEXT_MATCH_CHARS = 200  # Longer queries match by meaning only: a word-for-word match of a passage means little.
PAGE = 10  # The tool's page: results 1-10, 11-20, ...
TEXT_CAP = 100  # Text matches taken per search.
FUZZY_MIN = 0.45  # pg_trgm word similarity from which a near spelling counts ("selam nabe" ~ "selam naber").
FUZZY_MIN_PY = 0.6  # The same for the Python fallback (its measure is a little more lenient).
FALLBACK_ROWS = 5000  # Rows the Python fallback compares, newest first.
TEXT_BONUS = 0.5  # The tool ranks a text match this much above its meaning score (good text matches: 0.5-0.75).
_TR_UPPER, _TR_LOWER = "İIÇŞĞÖÜ", "iıçşğöü"
_TR_LETTERS, _TR_ASCII = "çğıöşüâîû", "cgiosuaiu"
# Turkish case folding that does not depend on the database's locale (a C-locale lower() knows only ASCII, and
# pg_trgm there splits words at ç, ş, ı...): the Turkish capitals to small letters, those to ASCII, lower() then.
# "nasilsin" so finds "nasılsın" too.
FOLD_SQL = f"lower(translate(translate(text, '{_TR_UPPER}', '{_TR_LOWER}'), '{_TR_LETTERS}', '{_TR_ASCII}'))"
_FOLD = str.maketrans(_TR_UPPER + _TR_LETTERS, _TR_LOWER.translate(str.maketrans(_TR_LETTERS, _TR_ASCII)) + _TR_ASCII)
_COLUMNS = ("id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
            "media_seconds, group_id, copied_from, source_chat_id, source_message_id, item_id, whole, span_start, "
            "span_end, track, caption, item_order")


def fold(text: str) -> str:
    """Turkish lower case without Turkish letters: İ/I/ı→i, Ş/ş→s..., then lower() for the rest."""
    return text.translate(_FOLD).lower()


def _trigrams(text: str) -> set[str]:
    grams = set()
    for word in re.findall(r"\w+", text):
        padded = f"  {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def word_similarity(query: str, text: str) -> float:
    """The share of the query's trigrams found in the best run of as many words of the text: a light stand-in for
    pg_trgm's word_similarity on a database without the extension. Both are folded already."""
    wanted = _trigrams(query)
    if not wanted:
        return 0.0
    words = re.findall(r"\w+", text)
    size = max(1, len(re.findall(r"\w+", query)))
    best = 0.0
    for start in range(max(1, len(words) - size + 1)):
        best = max(best, len(wanted & _trigrams(" ".join(words[start:start + size]))) / len(wanted))
        if best == 1.0:
            break
    return best


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


def _score_sql(params: list, space, vector) -> str:
    """The meaning score of a row of the current space (null for other spaces or without a query vector)."""
    if vector is None:
        return "null::float8"
    params += [space, vector]
    return f"case when vector_space = ${len(params) - 1} then 1 - (embedding <=> ${len(params)}::vector) end"


async def _text_rows(conn, user_id: str, q: str, vector_space: str, kind: str, space=None, vector=None) -> list[dict]:
    """This user's rows whose text has `q` (Turkish case folding) or a near spelling of it, best first. Each row has
    'tmatch' ('text' for a substring, 'similar' for a near spelling), 'tscore' (1 for a substring) and 'score'
    (its meaning score in the current space, else None)."""
    folded = fold(q)
    like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    params = [user_id, like] + ([folded] if _trgm else [])
    score = _score_sql(params, space, vector)
    extra, args = _filters(vector_space, kind, len(params) + 1)
    params += args
    if _trgm:
        rows = await conn.fetch(
            f"select {_COLUMNS}, {score} as score, case when {FOLD_SQL} like $2 then 1.0 "
            f"else word_similarity($3, {FOLD_SQL}) end as tscore from orion_memory where user_id = $1 "
            f"and not whole and ({FOLD_SQL} like $2 or word_similarity($3, {FOLD_SQL}) >= {FUZZY_MIN}){extra} "
            f"order by tscore desc, created_at desc, id desc limit {TEXT_CAP}", *params)
        return [{**dict(r), "tmatch": "text" if float(r["tscore"]) >= 1 else "similar"} for r in rows]
    rows = [{**dict(r), "tscore": 1.0, "tmatch": "text"} for r in await conn.fetch(
        f"select {_COLUMNS}, {score} as score from orion_memory where user_id = $1 and not whole and {FOLD_SQL} like $2"
        f"{extra} order by created_at desc, id desc limit {TEXT_CAP}", *params)]
    if len(rows) >= TEXT_CAP:
        return rows
    extra, args = _filters(vector_space, kind, 3)
    near = []
    for r in await conn.fetch(
            f"select id, {FOLD_SQL} as folded from orion_memory where user_id = $1 and not whole and not ({FOLD_SQL} like $2)"
            f"{extra} order by created_at desc, id desc limit {FALLBACK_ROWS}", user_id, like, *args):
        similarity = word_similarity(folded, r["folded"])
        if similarity >= FUZZY_MIN_PY:
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


async def _nearest(conn, user_id, space, vector, kind, count, offset=0) -> list[dict]:
    """The `count` rows of the current space nearest the query vector, from `offset`. No threshold: an image's score
    against a text is low (0.2-0.4) even when it is the right one, so only the order counts."""
    extra, args = _filters("all", kind, 6)
    return [dict(r) for r in await conn.fetch(
        f"select {_COLUMNS}, 1 - (embedding <=> $3::vector) as score from orion_memory "
        f"where user_id = $1 and vector_space = $2{extra} order by embedding <=> $3::vector, id limit $4 offset $5",
        user_id, space, vector, count, offset, *args)]


async def delete_rows(conn, user_id: str, where: str, *args) -> list[dict]:
    """Delete this user's rows matching `where` (SQL after the user's condition, parameters from $2) and drop their
    media links, inside the caller's transaction; the whole vectors of the items that lost a piece follow
    (refresh_wholes). Returns (id, text, content_key, group_id, item_id) of each."""
    from orion.kernel.media_store import unlink_media
    rows = await conn.fetch(
        f"delete from orion_memory where user_id = $1 and ({where}) "
        f"returning id, text, media_id, group_id, item_id, whole, {CONTENT_KEY} as content_key", user_id, *args)
    rows = [dict(r) for r in rows]
    for r in rows:
        if r["media_id"]:
            await unlink_media(r["media_id"], "memory", str(r["id"]), conn=conn)
    items = {r["item_id"] for r in rows if r.get("item_id") and not r.get("whole")}
    if items:
        await refresh_wholes(conn, user_id, sorted(items))
    return rows


def _parse_vector(value) -> list[float]:
    import json
    return json.loads(value) if isinstance(value, str) else [float(v) for v in value]


async def refresh_wholes(conn, user_id: str, items: list[str]) -> None:
    """Make the whole vectors of `items` the mean of the pieces they have left (memory_pieces.mean_vector); an item
    left with one piece (or none) of a track loses that track's whole vector."""
    from orion.kernel.media_store import unlink_media
    from orion.kernel.memory_pieces import mean_vector
    rows = await conn.fetch("select id, item_id, vector_space, track, whole, media_id, embedding::text as v "
                            "from orion_memory where user_id = $1 and item_id = any($2::text[])", user_id, items)
    tracks: dict = {}
    for r in rows:
        tracks.setdefault((r["item_id"], r["vector_space"], r["track"]), []).append(r)
    for members in tracks.values():
        pieces = [r for r in members if not r["whole"]]
        for whole in (r for r in members if r["whole"]):
            if len(pieces) < 2:
                await conn.execute("delete from orion_memory where id = $1", whole["id"])
                if whole["media_id"]:
                    await unlink_media(whole["media_id"], "memory", str(whole["id"]), conn=conn)
            else:
                await conn.execute("update orion_memory set embedding = $2::vector where id = $1", whole["id"],
                                   _vector(mean_vector([_parse_vector(p["v"]) for p in pieces])))


async def delete_items(user_id: str, ids: list[int]) -> list[dict]:
    """Delete this user's rows among `ids` together with their copies in other vector spaces (one memory, one
    delete); returns the asked rows that were deleted (id, text). Other users' ids are ignored."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return []
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        async with conn.transaction():
            rows = await delete_rows(
                conn, user_id, f"{CONTENT_KEY} in (select {CONTENT_KEY} from orion_memory where user_id = $1 "
                               "and id = any($2::bigint[]))", ids)
    finally:
        await conn.close()
    asked = set(ids)
    return [{"id": r["id"], "text": r["text"]} for r in rows if r["id"] in asked]


def _ranked(meaning: list[dict], texts: list[dict]) -> list[dict]:
    """Meaning and text matches in one order: the meaning score, a substring match TEXT_BONUS higher, a near
    spelling 80 % of that (pg_trgm's word similarity of a real typo is only ~0.4-0.6); rows of another space have
    only their text match."""
    pool = {r["id"]: {**r, "tmatch": None, "tscore": 0.0} for r in meaning}
    for r in texts:
        known = pool.get(r["id"], {})
        pool[r["id"]] = {**r, "score": r["score"] if r["score"] is not None else known.get("score")}

    def rank(r):
        bonus = {"text": TEXT_BONUS, "similar": 0.8 * TEXT_BONUS}.get(r["tmatch"], 0.0)
        return (float(r["score"]) if r["score"] is not None else 0.0) + bonus + 0.01 * float(r["tscore"])
    return sorted(pool.values(), key=lambda r: (-rank(r), -r["id"]))


def group_key(row) -> str:
    """The memory (card) a row belongs to: its group, or the row alone for a row saved before groups."""
    return row.get("group_id") or f"r{row['id']}"


def content_key(row) -> int:
    return row.get("copied_from") or row["id"]


def unshadowed(n: int) -> str:
    """SQL: the row is in the space ${n}, or that space has no copy of it (a memory is listed once)."""
    return (f"(vector_space = ${n} or not exists (select 1 from orion_memory c where c.user_id = orion_memory.user_id "
            f"and c.vector_space = ${n} and coalesce(c.copied_from, c.id) = "
            f"coalesce(orion_memory.copied_from, orion_memory.id)))")


def _one_per_content(ranked: list[dict]) -> list[dict]:
    """The best-ranked row of each content (a row and its copies in other spaces count once)."""
    seen, kept = set(), []
    for r in ranked:
        key = content_key(r)
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return kept


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


GROUP_ROW_COLUMNS = ("id, text, kind, vector_space, source_kind, source_chat_title, group_id, copied_from, media_id, "
                     "created_at, item_id, whole, span_start, span_end, track, item_order")


async def group_context(conn, user_id: str, rows: list[dict], space) -> dict[int, dict]:
    """For each row: what was saved with it, briefly. {row id: {"parts", "index", "source_kind", "title",
    "others": [{"id", "kind", "text"}] (at most 3, other kinds first)}}."""
    groups = {r.get("group_id") for r in rows if r.get("group_id")}
    members: dict[str, list[dict]] = {}
    if groups:
        for r in await conn.fetch(f"select {GROUP_ROW_COLUMNS} from orion_memory where user_id = $1 "
                                  "and group_id = any($2::text[]) order by id", user_id, list(groups)):
            members.setdefault(r["group_id"], []).append(dict(r))
    context = {}
    for row in rows:
        parts = pieces_only(prefer_space(members.get(row.get("group_id"), []) or [row], space))
        keys = [content_key(p) for p in parts]
        mine = content_key(row)
        item = row.get("item_id")
        others = [p for p in parts if content_key(p) != mine and not (item and p.get("item_id") == item)]
        others.sort(key=lambda p: (p["kind"] == (row.get("kind") or "text"), order_key(p)))
        mine_part = next((p for p in parts if content_key(p) == mine), None)
        position = item_positions(parts).get(mine_part["id"]) if mine_part else None
        siblings = [p for p in parts if item and p.get("item_id") == item and p.get("track") == row.get("track")]
        context[row["id"]] = {
            "key": group_key(row), "parts": len(parts), "index": keys.index(mine) + 1 if mine in keys else None,
            "source_kind": row.get("source_kind"), "title": row.get("source_chat_title"),
            "piece": position[0] if position else None,
            "pieces": position[1] if position else (len(siblings) or None),
            "others": [{"id": p["id"], "kind": p["kind"], "text": " ".join(p["text"].split())[:100]}
                       for p in others[:3]]}
    return context


def promote_pieces(ranked: list[dict]) -> list[dict]:
    """An item's whole vector ranks the item; what is shown is its best piece: a whole row is replaced by the best
    piece of its item (and track) further down the list, marked `via_whole`. A whole row whose pieces are not in the
    list stays, standing for the item."""
    shown, used = [], set()
    for index, r in enumerate(ranked):
        if r["id"] in used:
            continue
        if r.get("whole") and r.get("item_id"):
            best = next((p for p in ranked[index + 1:] if p.get("item_id") == r["item_id"] and not p.get("whole")
                         and p.get("track") == r.get("track") and p["id"] not in used), None)
            if best is not None:
                used.add(best["id"])
                shown.append({**best, "via_whole": True, "whole_score": r.get("score")})
                continue
        shown.append(r)
    return shown


SEARCH_FILE_TEXT = 4000  # Characters of a text file a search by file reads (the first piece is what is embedded).


async def file_query_vector(name: str, mime: str, raw: bytes, settings) -> tuple[str, str, str]:
    """(vector space, vector, kind) to search memory with a file: an image as it is; an audio by its first window;
    a video by frames spread over all of it (as many as one request takes); a text file by its first piece, as a
    document (it is compared with stored documents). ValueError (Turkish) for a file the model cannot take."""
    import asyncio
    from orion.kernel import memory_pieces as mp
    from orion.worker.services.router import audio_part, image_part
    kind = file_kind(name, mime)
    if kind is None:
        raise ValueError("Yalnız görsel, ses, video ve metin (.txt, .md) dosyalarıyla aranabilir.")
    if not raw:
        raise ValueError("Dosya boş.")
    target, space, local = await embedding_target(settings)
    if kind == "text":
        text = text_of_file(raw)[:SEARCH_FILE_TEXT]
        pieces = mp.text_pieces(text)
        return space, _vector((await _embed(pieces[0][1] if pieces else text, target, "document", local))[0]), kind
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
            count = mp.frames_per_window(embedding_context(), await memory_media_jobs.frame_cost(probe[0], target))
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


async def search(user_id: str, query: str, settings, page: int = 1, kind: str = "all",
                 exclude: list[str] | None = None) -> dict:
    """One page (PAGE rows, best first, no threshold) of this user's memory for the `hafizada_ara` tool. With a
    query: rows by meaning (current vector space) and text matches (substring or near spelling, any space) in one
    order. Without one: the newest rows (of `kind`). {"rows", "more", "other_spaces"[, "reason"]}."""
    from orion.kernel import memory_filters as mf
    query = (query or "").strip()
    negatives = [t.strip() for t in exclude or [] if t.strip()][:5]
    kind = kind if kind in KINDS else "all"
    page = max(1, int(page))
    start = (page - 1) * PAGE
    excluded, stats = 0, None
    if query:
        space, vector, reason = await _query_vector(query, settings)
    else:
        space, vector, reason = await current_space(settings), None, None
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        if query:
            meaning = await _nearest(conn, user_id, space, vector, kind,
                                     start + PAGE + 1 + (200 if negatives else 0)) if vector else []
            texts = await _text_rows(conn, user_id, query, "all", kind, space, vector)
            ranked = promote_pieces(_one_per_content(_ranked(meaning, texts)))
            if negatives:
                out = await mf.excluded_groups(conn, user_id, space, negatives, settings)
                kept = [r for r in ranked if group_key(r) not in out]
                excluded, ranked = len(ranked) - len(kept), kept
            stats = mf.calibrate(list((await mf.group_scores(conn, user_id, space, vector, kind)).values()))                 if vector else None
        else:
            extra, args = _filters("all", kind, 4)
            params = [user_id, PAGE + 1, start, *args]
            if space:
                params.append(space)
                extra += f" and {unshadowed(len(params))}"
            ranked = [None] * start + [{**dict(r), "score": None, "tmatch": None} for r in await conn.fetch(
                f"select {_COLUMNS} from orion_memory where user_id = $1 and not whole{extra} "
                f"order by created_at desc, id desc "
                f"limit $2 offset $3", *params)]
        page_rows = [r for r in ranked[start:start + PAGE] if r is not None]
        groups = await group_context(conn, user_id, page_rows, space)
        # Rows of another space that have no copy in this one: the search reaches them by text only.
        other = await conn.fetchval(
            "select count(*) from orion_memory r where user_id = $1 and vector_space <> $2 and not exists "
            "(select 1 from orion_memory c where c.user_id = $1 and c.vector_space = $2 "
            "and coalesce(c.copied_from, c.id) = coalesce(r.copied_from, r.id))", user_id, space) if space else 0
    finally:
        await conn.close()
    rows = []
    for r in page_rows:
        # Every row of the space has a meaning score (no threshold), so a text match is what the label says.
        match = (r["tmatch"] or "meaning") if query else None
        rows.append({"id": r["id"], "text": r["text"], "date": local_time(r["created_at"]),
                     "kind": r.get("kind") or "text", "media_id": r.get("media_id"),
                     "score": None if r["score"] is None else round(float(r["score"]), 3), "match": match,
                     "group": groups.get(r["id"]), "whole": bool(r.get("whole")), "track": r.get("track"),
                     "via_whole": bool(r.get("via_whole")),
                     "above": None if not query or stats is None or r["score"] is None
                     else round(float(r["score"]) - stats[0], 3),
                     "span": None if r.get("span_start") is None else (float(r["span_start"]), float(r["span_end"]))})
    result = {"rows": rows, "more": len(ranked) > start + PAGE, "other_spaces": int(other or 0)}
    if negatives:
        result["excluded"], result["exclude_terms"] = excluded, negatives
    if query and reason:
        result["reason"] = reason
    return result


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


async def open_media(user_id: str, chat_id: str, ids: list[int], kinds: set[str]) -> list[dict]:
    """This user's rows among `ids`, in that order. The file of each image/audio row whose kind is in `kinds` is
    linked to the chat (owner 'chat'), so the chat's history can hand it to the model on every later turn and the
    cleaner keeps it while a message names it; such rows say `opened` and list their `media`
    ([{"kind", "media_id", "mime"}]). A video row (or a piece of a long audio) is opened as what it holds: a few
    frames and its sound, stored as files of their own (segment_media)."""
    import asyncio
    import hashlib
    from orion.kernel.media_store import hold_media, link_media
    ids = [int(i) for i in ids][:3]
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        rows = {r["id"]: dict(r) for r in await conn.fetch(
            "select id, text, kind, media_id, media_mime, media_seconds, span_start, span_end, whole, track "
            "from orion_memory where user_id = $1 and id = any($2::bigint[])", user_id, ids)}
        found, seen = [], set()
        for row_id in ids:
            row = rows.get(row_id)
            if row is None or row_id in seen:
                continue
            seen.add(row_id)
            row["opened"] = False
            wanted = bool(kinds & {"image", "audio"}) if row["kind"] == "video" else row["kind"] in kinds
            if not row["media_id"] or not wanted:
                found.append(row)
                continue
            try:
                if not _sliced(row):
                    await link_media(row["media_id"], "chat", chat_id, conn=conn)
                    row["media"] = [{"kind": row["kind"], "media_id": row["media_id"], "mime": row["media_mime"]}]
                else:
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
                row["opened"] = bool(row["media"])
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
