"""Minimal memory: chunks of a saved chat with their embedding, searched by the `hafizada_ara` tool.

Rows carry their own text and do not reference the chat, so deleting the chat keeps them. Image and audio rows
(kind 'image'/'audio') carry a short label as their text and `media_id`: a link of owner 'memory' keeps the file
after the chat is deleted, and deleting the row drops that link. Rows of another
embedding model (`vector_space`) are never compared with the current one, only counted."""
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


AUDIO_PIECE_SECONDS = 60  # Gemma's mmproj: 25 tokens/s, so a piece fits the default 2048 context.
NOUNS = {"image": ("görsel", "görseller"), "audio": ("ses kaydı", "sesler")}
NO_MEDIA = {"has_extra": False, "extra_on": False, "extra_kinds": [], "image": False, "audio": False}


def chat_media(messages: list[dict]) -> list[dict]:
    """Images and audio the user sent in the chat (attachments and media parts), once each by media id. The
    assistant's messages, its spoken replies (TTS) included, are never taken."""
    found, seen = [], set()
    for message in messages:
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
            kind = "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else None
            if not kind or not isinstance(media_id, str) or media_id in seen:
                continue
            seen.add(media_id)
            found.append({"kind": kind, "media_id": media_id, "mime": mime, "name": item.get("name") or "",
                          "text": text.strip()[:300]})
    return found


def media_support(local: bool, model_name: str) -> dict:
    """What the active embedding model takes now (local_parts.embedding_media); a cloud model takes no media."""
    if local:
        from services.shared import local_parts
        try:
            return local_parts.embedding_media()
        except Exception as exc:
            logger.warning("Embedding media support unknown: %s", exc)
    return {"model": model_name, **NO_MEDIA}


def skip_warning(kind: str, count: int, support: dict) -> str:
    noun, many = NOUNS[kind]
    head = f"Sohbette {count} {noun} var ama"
    if support["has_extra"] and kind in support["extra_kinds"] and not support["extra_on"]:
        return (f"{head} {support['model']} modelinin ek dosyası kapalı; {many} kaydedilmedi. "
                "Görsel/ses için modelin ek dosyasını aç (Bileşenler → Hafıza).")
    sense, label = ("görselleri", "Görsel") if kind == "image" else ("sesi", "Ses")
    return (f"{head} {support['model']} {sense} anlamıyor; {many} kaydedilmedi. "
            f"{label} için EmbeddingGemma 2'yi ek dosyasıyla seç.")


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def audio_pieces(raw: bytes, piece_seconds: int = AUDIO_PIECE_SECONDS) -> tuple[list[bytes], float]:
    """Decode any audio PyAV reads and cut it into 16 kHz mono WAV pieces of at most `piece_seconds`; returns
    (pieces, total seconds)."""
    import io
    import wave
    import av
    rate = 16000
    pcm = bytearray()
    with av.open(io.BytesIO(raw)) as source:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

        def take(frames):
            for frame in frames:
                pcm.extend(bytes(frame.planes[0])[:frame.samples * 2])
        for frame in source.decode(audio=0):
            take(resampler.resample(frame))
        take(resampler.resample(None))
    step = piece_seconds * rate * 2
    pieces = []
    for start in range(0, len(pcm), step):
        chunk = bytes(pcm[start:start + step])
        if len(chunk) < rate // 5:  # A tail under 0.1 s says nothing.
            continue
        out = io.BytesIO()
        with wave.open(out, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(chunk)
        pieces.append(out.getvalue())
    return pieces, len(pcm) / (rate * 2)


async def _embed_media(parts: list[dict], target) -> list[float]:
    from orion.worker.services.router import generate_embeddings
    vector = await generate_embeddings(parts, target)  # Media take no prefix (EmbeddingGemma 2: text only).
    if not vector or len(vector) != 768:
        raise ValueError("Embedding modeli 768 boyutlu vektör döndürmedi")
    return vector


async def _read_chat_media(chat_id: str, media_id: str) -> tuple[str, bytes]:
    from orion.kernel.media_store import read_media
    return await read_media(chat_id, media_id)


async def _media_rows(item: dict, chat_id: str, target) -> list[tuple[str, list[float], float | None]]:
    """(label, vector, seconds) per row of one image or audio file: one image, one row per <=60 s of audio."""
    mime, raw = await _read_chat_media(chat_id, item["media_id"])
    return await _rows_of(item, mime, raw, target)


async def _rows_of(item: dict, mime: str, raw: bytes, target) -> list[tuple[str, list[float], float | None]]:
    import asyncio
    from orion.worker.services.router import audio_part, image_part
    tail = f" — {item['text']}" if item["text"] else ""
    if item["kind"] == "image":
        return [((item["name"] or "Görsel") + tail, await _embed_media([image_part(raw, mime)], target), None)]
    pieces, seconds = await asyncio.to_thread(audio_pieces, raw)
    if not pieces:
        raise ValueError("ses çözülemedi")
    name = item["name"] if not item["name"].lower().startswith(("ses", "audio", "recording", "voice")) else ""
    base = f"Ses kaydı {clock(seconds)}" + (f" ({name})" if name else "")
    rows = []
    for index, piece in enumerate(pieces, 1):
        part = f" parça {index}/{len(pieces)}" if len(pieces) > 1 else ""
        rows.append((base + part + tail, await _embed_media([audio_part(piece, "wav")], target), seconds))
    return rows


MEDIA_INSERT_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title, kind, "
    "media_id, media_mime, media_seconds) values ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9, $10) returning id")


async def _save_media(conn, user_id, chat_id, title, items, space, target, support) -> tuple[int, dict, list[str]]:
    from orion.kernel.media_store import link_media
    saved, skipped, failed, warnings = 0, {}, {}, []
    for kind in ("image", "audio"):
        wanted = [item for item in items if item["kind"] == kind]
        if not wanted:
            continue
        if not support[kind]:
            skipped[kind] = len(wanted)
            warnings.append(skip_warning(kind, len(wanted), support))
            continue
        for item in wanted:
            if await conn.fetchval("select 1 from orion_memory where user_id = $1 and vector_space = $2 "
                                   "and media_id = $3 limit 1", user_id, space, item["media_id"]):
                continue
            try:
                rows = await _media_rows(item, chat_id, target)
                async with conn.transaction():
                    for label, vector, seconds in rows:
                        row_id = await conn.fetchval(
                            MEDIA_INSERT_SQL, user_id, label[:1000], space, _vector(vector), "chat_save",
                            title or None, kind, item["media_id"], item["mime"], seconds)
                        await link_media(item["media_id"], "memory", str(row_id), conn=conn)
                saved += len(rows)
            except Exception as exc:
                logger.warning("Memory could not store %s %s: %s", kind, item["media_id"][:12], exc)
                failed[kind] = failed.get(kind, 0) + 1
    for kind, count in failed.items():
        warnings.append(f"{count} {NOUNS[kind][0]} kaydedilemedi (embedding modeli işleyemedi).")
    return saved, skipped, warnings


async def save_chat(user_id: str, chat_id: str, title: str, settings) -> dict:
    """Chunk, embed and store the chat's text, and the images and audio the active model takes. Returns
    {"saved": new rows, "skipped": {kind: count}, "warnings": [Turkish sentences]}."""
    from orion.worker.services.router import chunk_text
    messages = await registry.get_chat_history_db(chat_id)
    chunks = chunk_text(chat_text(messages))
    items = chat_media(messages)
    if not chunks and not items:
        raise MemoryUnavailable("Bu sohbette kaydedilecek metin yok.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        rows = []
        for chunk in chunks:
            for vector in await _embed(chunk, target, "document", local):
                rows.append((user_id, chunk, space, _vector(vector), title or None))
        saved = await _insert_new(conn, rows, "chat_save")
        skipped, warnings = {}, []
        if items:
            support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
            more, skipped, warnings = await _save_media(conn, user_id, chat_id, title, items, space, target, support)
            saved += more
        return {"saved": saved, "skipped": skipped, "warnings": warnings}
    finally:
        await conn.close()


INSERT_SQL = (
    "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, source_chat_title) "
    "select $1, $2, $3, $4::vector, $6, $5 where not exists "
    "(select 1 from orion_memory where user_id = $1 and vector_space = $3 and text = $2 and media_id is null)")
MAX_NOTE = 4000


async def _insert_new(conn, rows, source_kind: str) -> int:
    """Insert rows, skipping a text this user already has in the same vector space; returns how many were new."""
    seen, fresh = set(), []
    for row in rows:
        if (row[1], row[2]) in seen:
            continue
        seen.add((row[1], row[2]))
        existing = await conn.fetchval(
            "select 1 from orion_memory where user_id = $1 and vector_space = $2 and text = $3 and media_id is null",
            row[0], row[2], row[1])
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


TEXT_FILE_TYPES = (".txt", ".md", ".markdown")
MAX_TEXT_FILE = 2 * 1024 * 1024


def file_kind(name: str, mime: str) -> str | None:
    """'image', 'audio' or 'text' (a .txt/.md file) for a file added on the memory page; None for anything else."""
    mime, name = (mime or "").lower(), (name or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or name.endswith(TEXT_FILE_TYPES):
        return "text"
    return None


def file_refusal(kind: str, support: dict) -> str:
    sense, label = ("görselleri", "Görsel") if kind == "image" else ("sesi", "Ses")
    if support["has_extra"] and kind in support["extra_kinds"] and not support["extra_on"]:
        return (f"Dosya eklenmedi: {support['model']} modelinin ek dosyası kapalı. "
                "Görsel/ses için modelin ek dosyasını aç (Bileşenler → Hafıza).")
    return f"Dosya eklenmedi: {support['model']} {sense} anlamıyor. {label} için EmbeddingGemma 2'yi ek dosyasıyla seç."


async def add_file(user_id: str, name: str, mime: str, raw: bytes, settings) -> dict:
    """A file the user adds on the memory page. A text file (.txt/.md): one row per chunk. An image or audio: as a
    chat save stores it (one copy per sha256 linked to each row; the same file in the same space is skipped).
    {"added": new rows, "warnings": [...]}; ValueError with a Turkish sentence for a file that cannot be added."""
    import hashlib
    from orion.kernel.media_store import MAX_MEDIA_BYTES, hold_media, link_media
    from orion.worker.services.router import chunk_text
    kind = file_kind(name, mime)
    if kind is None:
        raise ValueError("Yalnız görsel, ses ve metin (.txt, .md) dosyaları eklenebilir.")
    if not raw:
        raise ValueError("Dosya boş.")
    name = " ".join((name or "").split())[:200]
    if kind == "text":
        if len(raw) > MAX_TEXT_FILE:
            raise ValueError("Metin dosyası çok büyük: en fazla 2 MB olabilir.")
        text = raw.decode("utf-8-sig", errors="replace").strip()
        if not text:
            raise ValueError("Dosyada metin yok.")
    elif len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("Dosya çok büyük: en fazla 64 MB olabilir.")
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        target, space, local = await embedding_target(settings)
        if kind == "text":
            rows = []
            for piece in chunk_text(text) or [text]:
                for vector in await _embed(piece, target, "document", local):
                    rows.append((user_id, piece, space, _vector(vector), name or None))
            added = await _insert_new(conn, rows, "file")
            return {"added": added, "warnings": [] if added else ["Bu metin hafızada zaten var."]}
        support = media_support(local, getattr(target, "router_model_group", "") or "Embedding modeli")
        if not support[kind]:
            raise ValueError(file_refusal(kind, support))
        mime = mime.lower()
        media_id = hashlib.sha256(raw).hexdigest()
        if await conn.fetchval("select 1 from orion_memory where user_id = $1 and vector_space = $2 "
                               "and media_id = $3 limit 1", user_id, space, media_id):
            return {"added": 0, "warnings": ["Bu dosya hafızada zaten var."]}
        item = {"kind": kind, "media_id": media_id, "mime": mime, "name": name, "text": ""}
        try:
            rows = await _rows_of(item, mime, raw, target)
        except Exception as exc:
            logger.warning("Memory could not embed the file %s: %s", name, exc)
            raise ValueError("Embedding modeli bu dosyayı işleyemedi.") from exc
        async with conn.transaction():
            await hold_media(conn, media_id, mime, raw)
            for label, vector, seconds in rows:
                row_id = await conn.fetchval(MEDIA_INSERT_SQL, user_id, label[:1000], space, _vector(vector), "file",
                                             name or None, kind, media_id, mime, seconds)
                await link_media(media_id, "memory", str(row_id), conn=conn)
        return {"added": len(rows), "warnings": []}
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


KINDS = ("text", "image", "audio")
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
            "media_seconds")


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
            f"and ({FOLD_SQL} like $2 or word_similarity($3, {FOLD_SQL}) >= {FUZZY_MIN}){extra} "
            f"order by tscore desc, created_at desc, id desc limit {TEXT_CAP}", *params)
        return [{**dict(r), "tmatch": "text" if float(r["tscore"]) >= 1 else "similar"} for r in rows]
    rows = [{**dict(r), "tscore": 1.0, "tmatch": "text"} for r in await conn.fetch(
        f"select {_COLUMNS}, {score} as score from orion_memory where user_id = $1 and {FOLD_SQL} like $2"
        f"{extra} order by created_at desc, id desc limit {TEXT_CAP}", *params)]
    if len(rows) >= TEXT_CAP:
        return rows
    extra, args = _filters(vector_space, kind, 3)
    near = []
    for r in await conn.fetch(
            f"select id, {FOLD_SQL} as folded from orion_memory where user_id = $1 and not ({FOLD_SQL} like $2)"
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


def _item(r, space, score=None, match=None, text_score=None) -> dict:
    item = {"id": r["id"], "text": r["text"], "kind": r["kind"], "vector_space": r["vector_space"],
            "source_kind": r["source_kind"], "source_chat_title": r["source_chat_title"],
            "created_at": r["created_at"].isoformat(), "searchable": r["vector_space"] == space}
    if r.get("media_id"):
        seconds = r.get("media_seconds")
        item.update(media_id=r["media_id"], media_mime=r.get("media_mime"),
                    media_seconds=None if seconds is None else round(float(seconds), 1))
    if match:
        item["match"] = match
        item["score"] = None if score is None else round(float(score), 4)
        if text_score is not None and match == "similar":
            item["text_score"] = round(float(text_score), 3)
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


async def _nearest(conn, user_id, space, vector, kind, count, offset=0) -> list[dict]:
    """The `count` rows of the current space nearest the query vector, from `offset`. No threshold: an image's score
    against a text is low (0.2-0.4) even when it is the right one, so only the order counts."""
    extra, args = _filters("all", kind, 6)
    return [dict(r) for r in await conn.fetch(
        f"select {_COLUMNS}, 1 - (embedding <=> $3::vector) as score from orion_memory "
        f"where user_id = $1 and vector_space = $2{extra} order by embedding <=> $3::vector, id limit $4 offset $5",
        user_id, space, vector, count, offset, *args)]


async def list_items(user_id: str, settings, q: str = "", limit: int = 50, offset: int = 0,
                     vector_space: str = "all", kind: str = "all") -> dict:
    """A page of this user's rows and a summary of all of them. Without `q`: newest first. With `q`: the `limit`
    rows nearest by meaning (current vector space, best first, no threshold), then the text matches among the rest
    (substring, then near spellings); each says its `match` (meaning, both, text, similar); `more` says whether
    more rows by meaning follow."""
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
    more = False
    try:
        await ensure_memory_table(conn)
        if q:
            meaning = []
            searchable = vector is not None and vector_space in ("all", space)
            if searchable:
                meaning = await _nearest(conn, user_id, space, vector, kind, limit + 1, offset)
                more, meaning = len(meaning) > limit, meaning[:limit]
            texts = await _text_rows(conn, user_id, q, vector_space, kind, space if searchable else None,
                                     vector if searchable else None)
            text_ids = {r["id"] for r in texts}
            meaning_ids = {r["id"] for r in meaning}
            merged = [_item(r, space, r["score"], "both" if r["id"] in text_ids else "meaning") for r in meaning]
            merged += [_item(r, space, r["score"], r["tmatch"], r["tscore"]) for r in texts
                       if r["id"] not in meaning_ids]
            items, matching = merged, len(merged)
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
        result["more"] = more
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
        from orion.kernel.media_store import unlink_media
        async with conn.transaction():
            rows = await conn.fetch(
                "delete from orion_memory where user_id = $1 and id = any($2::bigint[]) returning id, text, media_id",
                user_id, ids)
            for r in rows:
                if r.get("media_id"):
                    await unlink_media(r["media_id"], "memory", str(r["id"]), conn=conn)
    finally:
        await conn.close()
    return [{"id": r["id"], "text": r["text"]} for r in rows]


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


async def search(user_id: str, query: str, settings, page: int = 1, kind: str = "all") -> dict:
    """One page (PAGE rows, best first, no threshold) of this user's memory for the `hafizada_ara` tool. With a
    query: rows by meaning (current vector space) and text matches (substring or near spelling, any space) in one
    order. Without one: the newest rows (of `kind`). {"rows", "more", "other_spaces"[, "reason"]}."""
    query = (query or "").strip()
    kind = kind if kind in KINDS else "all"
    page = max(1, int(page))
    start = (page - 1) * PAGE
    if query:
        space, vector, reason = await _query_vector(query, settings)
    else:
        space, vector, reason = await current_space(settings), None, None
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        if query:
            meaning = await _nearest(conn, user_id, space, vector, kind, start + PAGE + 1) if vector else []
            texts = await _text_rows(conn, user_id, query, "all", kind, space, vector)
            ranked = _ranked(meaning, texts)
        else:
            extra, args = _filters("all", kind, 4)
            ranked = [None] * start + [{**dict(r), "score": None, "tmatch": None} for r in await conn.fetch(
                f"select {_COLUMNS} from orion_memory where user_id = $1{extra} order by created_at desc, id desc "
                f"limit $2 offset $3", user_id, PAGE + 1, start, *args)]
        other = await conn.fetchval(
            "select count(*) from orion_memory where user_id = $1 and vector_space <> $2", user_id, space) if space else 0
    finally:
        await conn.close()
    rows = []
    for r in ranked[start:start + PAGE]:
        # Every row of the space has a meaning score (no threshold), so a text match is what the label says.
        match = (r["tmatch"] or "meaning") if query else None
        rows.append({"id": r["id"], "text": r["text"], "date": r["created_at"].date().isoformat(),
                     "kind": r.get("kind") or "text", "media_id": r.get("media_id"),
                     "score": None if r["score"] is None else round(float(r["score"]), 3), "match": match})
    result = {"rows": rows, "more": len(ranked) > start + PAGE, "other_spaces": int(other or 0)}
    if query and reason:
        result["reason"] = reason
    return result


async def open_media(user_id: str, chat_id: str, ids: list[int], kinds: set[str]) -> list[dict]:
    """This user's rows among `ids`, in that order. The file of each image/audio row whose kind is in `kinds` is
    linked to the chat (owner 'chat'), so the chat's history can hand it to the model on every later turn and the
    cleaner keeps it while a message names it; such rows say `opened`."""
    from orion.kernel.media_store import link_media
    ids = [int(i) for i in ids][:3]
    conn = await _connect()
    try:
        await ensure_memory_table(conn)
        rows = {r["id"]: dict(r) for r in await conn.fetch(
            "select id, text, kind, media_id, media_mime from orion_memory where user_id = $1 "
            "and id = any($2::bigint[])", user_id, ids)}
        found, seen = [], set()
        for row_id in ids:
            row = rows.get(row_id)
            if row is None or row_id in seen:
                continue
            seen.add(row_id)
            row["opened"] = False
            if row["media_id"] and row["kind"] in kinds:
                try:
                    await link_media(row["media_id"], "chat", chat_id, conn=conn)
                    row["opened"] = True
                except LookupError:
                    row["missing"] = True
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
