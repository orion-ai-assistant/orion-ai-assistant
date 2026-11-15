import json
from datetime import datetime
from typing import Literal

from pydantic import Field

from .._base import ToolContext, ToolInput

KINDS = ("all", "text", "image", "audio", "video")
KIND_NAMES = {"text": "text", "image": "image", "audio": "audio recording", "video": "video"}
TRACKS = {"frames": "picture", "audio": "sound", "both": "picture and sound"}
MATCH_WORDS = {"meaning": "by meaning", "text": "its words contain the query",
               "similar": "its words are spelled like the query"}
MATCHED_BY = {"meaning": "meaning", "text": "words", "similar": "similar spelling"}
PAGE_SIZE = 10
CONTEXT_NOTE = ("A result is one memory (things saved together); 'id' is its best-matching part. 'part' says which "
                "piece of a long text, audio or video it is, 'time' that piece's minutes. memory_context reads the "
                "parts around it (neighbours=1-3), the whole memory, or the chat it was saved from.")
MEDIA_NOTE = ("Of an image, audio or video you see only its label and date here. open_memory_files with these ids "
              "(up to 3) shows you the file and puts it in front of the user; a video part opens as a few frames "
              "and its sound.")


class MemoryQuery(ToolInput):
    text: str = Field(default="", max_length=2000,
                      description="What to look for, a short phrase (e.g. 'the user's daughter'). Leave empty when "
                                  "'id' is set.")
    id: int | None = Field(default=None,
                           description="Instead of text: the id of a memory part from an earlier result. Its own "
                                       "vector is the query, so you find memories like that image, audio, video or "
                                       "text (its own memory is not listed).")
    like: bool = Field(default=True,
                       description="true: results should be like this. false: memories like this (or, for a text, "
                                   "containing it) are left out.")


class MemorySearchInput(ToolInput):
    queries: list[MemoryQuery] = Field(
        default_factory=list, max_length=8,
        description="What to search for: each query is a text or a memory id, liked or unliked. Example, 'like the "
                    "cat photo (id 41) but no dog': [{\"id\": 41}, {\"text\": \"dog\", \"like\": false}]. Empty: the "
                    "newest memories (of 'kind').")
    mode: Literal["together", "separate"] = Field(
        default="together",
        description="With several liked queries. together: one combined query, memories close to all of them rank "
                    "first (AND-like). separate: each query searched on its own and the lists merged, a memory "
                    "ranks by the query it matches best (OR); each result names that query.")
    kind: Literal["all", "text", "image", "audio", "video"] = Field(
        default="all", description="Only memories with parts of this kind.")
    model: str = Field(default="all", max_length=100,
                       description="Embedding model filter: 'all' (default: memories of other models are still found "
                                   "by their words), 'active' (only memories the active model can search by meaning).")
    page: int = Field(default=1, ge=1, le=100,
                      description="Result page: 1 = the best ones, 2 = the next ones... Look further only if "
                                  "'more' is true.")
    page_size: int = Field(default=PAGE_SIZE, ge=1, le=20, description="Results per page.")


def _clock(seconds: float) -> str:
    from orion.kernel.memory import clock
    return clock(seconds)


def _date(value) -> str | None:
    from orion.kernel.memory import local_time
    try:
        return local_time(datetime.fromisoformat(value)) if value else None
    except (TypeError, ValueError):
        return value


def _points(above: float) -> str:
    points = round(above * 100)
    if points >= 0:
        return f"{points} points above this search's average score"
    return f"{-points} points below this search's average score"


def _result(card: dict, labels: list[str], words: str) -> dict:
    """One memory as the chat model reads it: its best part (id, text or label), the score, which query gave it and
    how far it stands above the search's average, in plain words."""
    best = card.get("best_part") or {}
    kind = best.get("kind") or ("text" if card.get("text") else card.get("kind", "text"))
    row_id = best.get("id") or card.get("matched_part") or card.get("text_part")
    if row_id is None and card.get("parts"):
        row_id = card["parts"][0]["id"]
    item = {"id": row_id, "kind": KIND_NAMES.get(kind, kind), "date": _date(card.get("created_at"))}
    text = best.get("text") if best else None
    if text is None:
        text = card.get("text") if kind == "text" else next(
            (p["text"] for p in card.get("parts") or [] if p["id"] == row_id), None)
    if text is not None:
        item["text" if kind == "text" else "label"] = text
    if (best.get("pieces") or 0) > 1:
        item["part"] = f"{best['piece']} of {best['pieces']}"
    if best.get("span_start") is not None:
        item["time"] = f"{_clock(best['span_start'])}–{_clock(best['span_end'])}"
    if best.get("track") in TRACKS:
        item["track"] = TRACKS[best["track"]]
    if card.get("source_kind") == "file" and card.get("title"):
        item["file"] = card["title"]
    if card.get("score") is not None:
        item["score"] = round(float(card["score"]), 3)
        match = card.get("match") or "meaning"
        if card.get("via"):
            source = card["via"]
        elif match != "meaning" and words:
            source = json.dumps(words[:60], ensure_ascii=False)
        elif len(labels) == 1:
            source = labels[0]
        else:
            source = "all queries together"
        item["score_from"] = source
        item["matched_by"] = MATCHED_BY.get(match, "meaning")
        why = f"{round(float(card['score']) * 100)}% similar to {source} {MATCH_WORDS.get(match, 'by meaning')}"
        if match != "meaning":
            why = (f"{MATCH_WORDS[match]} ({source}); meaning similarity {round(float(card['score']) * 100)}%")
        if card.get("above") is not None:
            item["above_average"] = round(float(card["above"]), 3)
            why += f"; {_points(float(card['above']))}"
        item["why"] = why
    counts = card.get("counts") or {}
    if card.get("part_count", 1) > 1:
        item["memory_has"] = ", ".join(f"{n} {KIND_NAMES.get(k, k)}{'s' if n > 1 and k != 'audio' else ''}"
                                       for k, n in counts.items())
        if card.get("matched_parts", 0) > 1:
            item["parts_matched"] = card["matched_parts"]
    return item



async def search_memory(args: MemorySearchInput, context: ToolContext) -> dict:
    from orion.kernel import memory, memory_groups
    from orion.kernel.config import get_runtime_settings
    queries = [{"text": q.text, "id": q.id, "like": q.like} for q in args.queries
               if q.id is not None or q.text.strip()]
    size = args.page_size
    try:
        found = await memory_groups.search_queries(
            context.user_id, await get_runtime_settings(None, context.user_id), queries,
            mode="each" if args.mode == "separate" else "together", kind=args.kind,
            vector_space=args.model.strip() or "all", limit=size, offset=(args.page - 1) * size)
    except memory.MemoryUnavailable as exc:
        return {"error": str(exc)}
    except memory_groups.QueryError as exc:
        return {"error": str(exc)}
    labels = found.get("labels") or []
    words = ", ".join(q["text"].strip() for q in queries if q["like"] and q["id"] is None)
    results = [_result(card, labels, words) for card in found["groups"]]
    result = {"page": args.page, "results": results, "more": found["more"]}
    searching = any(q["like"] for q in queries)
    if searching and results:
        result["order"] = ("Best first. 'score' is the meaning similarity (0-1; image/audio against text is often "
                           "0.2-0.4, so compare results, not the number alone). 'score_from' names the query that "
                           "gave it; 'above_average' is how far it stands above what this search scores on average "
                           "against all memories. A memory whose words contain a text query ranks higher.")
        result["matching"] = found.get("matching")
    elif not searching:
        result["order"] = "Newest first."
    if any(r["kind"] != "text" for r in results):
        result["media_note"] = MEDIA_NOTE
    if any(r.get("memory_has") or r.get("part") for r in results):
        result["context_note"] = CONTEXT_NOTE
    if found.get("exclude_terms") or found.get("excluded"):
        result["left_out"] = f"{found.get('excluded', 0)} memories like the like=false queries were left out"
    if not results:
        result["note"] = "No memories on this page." if args.page > 1 else "No memories for this search."
    if found.get("semantic_reason"):
        result["meaning_search_off"] = f"Only word matches: {found['semantic_reason']}"
    other = (found.get("summary") or {}).get("not_searchable") or 0
    if searching and other:
        result["other_models"] = (f"{other} memories were saved with another embedding model: they are found only "
                                  "by their words" + (" (model='all')" if args.model.strip() == "active" else ""))
    return result


class MemoryAddInput(ToolInput):
    text: str = Field(min_length=1, max_length=4000,
                      description="What to remember, one self-contained sentence (e.g. 'The user's daughter is "
                                  "called Elif.').")


class MemoryDeleteInput(ToolInput):
    ids: list[int] = Field(min_length=1, max_length=20, description="Ids from search_memory results.")
    whole_memory: bool = Field(default=False,
                               description="false: delete only these parts. true: delete the whole memory each id "
                                           "belongs to (everything saved with it).")


class MemoryOpenInput(ToolInput):
    ids: list[int] = Field(min_length=1, max_length=3,
                           description="Ids from search_memory results (up to 3): images, audio, video, text "
                                       "files, notes.")


async def add_memory(args: MemoryAddInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    try:
        added = await memory.add_note(context.user_id, args.text, await get_runtime_settings(None, context.user_id),
                                      source_kind="assistant")
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"error": str(exc)}
    return {"added": args.text} if added else {"note": "This was already in memory.", "text": args.text}


async def delete_memory(args: MemoryDeleteInput, context: ToolContext) -> dict:
    from orion.kernel import memory, memory_groups
    try:
        if args.whole_memory:
            groups = []
            for row_id in dict.fromkeys(args.ids):
                found = await memory_groups.row_vector(context.user_id, row_id, None)
                if found is not None:
                    groups.append(found["group"])
            deleted = await memory_groups.delete_groups(context.user_id, groups) if groups else []
        else:
            deleted = await memory.delete_items(context.user_id, args.ids)
    except memory.MemoryUnavailable as exc:
        return {"error": str(exc)}
    if not deleted:
        return {"note": "No memory has these ids; nothing was deleted."}
    seen, shown = set(), []
    for row in deleted:
        if row["text"] not in seen:
            seen.add(row["text"])
            shown.append({"id": row["id"], "text": row["text"][:300]})
    return {"deleted": shown[:20], "deleted_parts": len(deleted),
            "instruction": "Tell the user briefly what you deleted."}


def attachment_of(row: dict, chat_id: str) -> dict:
    """An opened memory file as the chat shows a user's attachment: {media_id, mime_type, name, memory_id}, a
    reference to the one stored copy (linked to the chat; `media_path` loads it as a user's attachment), never its
    bytes."""
    import mimetypes
    named = (row.get("name") or (row.get("text") or "").split(" — ")[0]).strip()
    if not named or "." not in named[-6:] or len(named) > 120:
        ext = mimetypes.guess_extension(row.get("media_mime") or "") or ""
        label = {"image": "Görsel", "audio": "Ses kaydı", "video": "Video"}.get(row["kind"], "Dosya")
        named = f"{label} {row['id']}{ext}"
    from orion.kernel.media_store import media_path
    return {"media_id": row["media_id"], "mime_type": row.get("media_mime") or "application/octet-stream",
            "name": named, "memory_id": row["id"], "media_path": media_path(chat_id, row["media_id"])}


DOCUMENT_TYPES = {".md": "text/markdown", ".markdown": "text/markdown"}


async def _open_document(row_id: int, context: ToolContext, settings, earlier: dict) -> dict | None:
    """A text memory as a file under the turn (its pieces joined: the text that was saved), plus its text for the
    model: whole when it fits memory_context's budget, else the asked part with two neighbours each side and how to
    read on. None for a row that is not text."""
    import hashlib
    from orion.kernel import memory, memory_groups
    from orion.kernel.media_store import hold_media, link_media
    doc = await memory_groups.item_document(context.user_id, row_id, settings)
    if doc is None:
        return None
    raw = doc["text"].encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    ext = "." + doc["name"].rsplit(".", 1)[-1].lower() if "." in doc["name"] else ""
    mime = DOCUMENT_TYPES.get(ext, "text/plain")
    row = {"id": row_id, "kind": "text", "media_id": sha, "media_mime": mime, "name": doc["name"]}
    entry = {"id": row_id, "kind": "text document", "name": doc["name"]}
    if sha in earlier:
        return {"entry": {**entry, "message": earlier[sha],
                          "note": f"Already opened in message {earlier[sha]} of this chat; its text is there."},
                "attachment": {**attachment_of(row, context.chat_id), "already": True}, "again": True}
    conn = await memory._connect()
    try:
        async with conn.transaction():
            await hold_media(conn, sha, mime, raw)
            await link_media(sha, "chat", context.chat_id, conn=conn)
    finally:
        await conn.close()
    pieces, asked = doc["pieces"], doc["asked"]
    if len(doc["text"]) <= memory_groups.GROUP_TEXT_CAP:
        entry["text"] = doc["text"]
    else:
        lo, hi = max(0, asked - 2), min(len(pieces), asked + 3)
        entry["parts"] = [{"id": p["id"], "part": f"{i + 1} of {len(pieces)}", "text": p["text"]}
                          for i, p in enumerate(pieces[lo:hi], lo)]
        entry["more"] = (f"The document has {len(pieces)} parts; only parts {lo + 1}-{hi} (around the one asked) are "
                         "here. To read on, call memory_context with the id of the first or last part shown and "
                         "neighbours=3; the user has the whole file under this answer.")
    return {"entry": entry, "attachment": attachment_of(row, context.chat_id),
            "ref": {"memory_id": row_id, "kind": "file", "media_id": sha, "mime_type": mime}}


async def open_memory_files(args: MemoryOpenInput, context: ToolContext) -> dict:
    """Puts memory files in front of the user (under this turn, as attachments they can open) and in front of the
    model: images/audio/video it can take go in `_model_media` (a tool message cannot carry media on
    OpenAI-compatible APIs: the tool runner keeps them on the tool message as references and hands them to the model
    in a user message right after it, on this and every later turn, see tool_history.media_message); a text memory
    (a .txt/.md file, a note) comes back as its text."""
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    settings = await get_runtime_settings(None, context.user_id)
    can = await memory.chat_model_media(settings)
    kinds = {kind for kind in ("image", "audio") if can[kind]}
    ids = list(dict.fromkeys(args.ids))
    try:
        # Opened earlier in this chat: it is in the conversation already (sent every turn), not attached again.
        earlier = memory.opened_in_chat((await memory._chat_messages(context.chat_id))[0]) if context.chat_id else {}
        rows = await memory.open_media(context.user_id, context.chat_id, ids, kinds, earlier)
    except memory.MemoryUnavailable as exc:
        return {"error": str(exc)}
    by_id = {row["id"]: row for row in rows}
    opened, closed, media, attachments, again, refs = [], [], [], [], [], []
    for row_id in ids:
        row = by_id.get(row_id)
        if row is None:
            continue
        entry = {"id": row["id"], "kind": KIND_NAMES.get(row["kind"], row["kind"]), "label": row["text"][:300]}
        if not row["media_id"]:
            try:
                doc = await _open_document(row_id, context, settings, earlier)
            except memory.MemoryUnavailable as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - one file failing does not hide the others
                closed.append({**entry, "reason": f"The text could not be opened: {exc}"[:200]})
                continue
            if doc is None:
                closed.append({**entry, "reason": "Nothing to open."})
                continue
            (again if doc.get("again") else opened).append(doc["entry"])
            attachments.append(doc["attachment"])
            if doc.get("ref"):
                refs.append(doc["ref"])
            continue
        if row.get("already"):
            again.append({**entry, "message": row["already"],
                          "note": f"Already opened in message {row['already']} of this chat; look at it there."})
            attachments.append({**attachment_of(row, context.chat_id), "already": True})
            continue
        if row["opened"]:
            shown = row.get("media") or []
            if row["kind"] == "video" or len(shown) > 1:
                entry["opened_as"] = ", ".join(
                    f"{sum(1 for m in shown if m['kind'] == k)} {'frames' if k == 'image' else 'sound'}"
                    for k in ("image", "audio") if any(m["kind"] == k for m in shown))
            opened.append(entry)
            attachments.append(attachment_of(row, context.chat_id))
            for m in shown:
                media.append({"memory_id": row["id"], "source": row["media_id"], "kind": m["kind"],
                              "media_id": m["media_id"], "mime_type": m["mime"], "label": row["text"][:300]})
        elif row.get("shown"):
            sense = "hear audio" if row["kind"] == "audio" else "see images"
            opened.append({**entry, "note": f"The user has the file; the current chat model cannot {sense}, so you "
                                            "know only its label."})
            attachments.append(attachment_of(row, context.chat_id))
            refs.append({"memory_id": row["id"], "kind": "file", "media_id": row["media_id"],
                         "mime_type": row.get("media_mime")})
        elif row.get("missing"):
            closed.append({**entry, "reason": "Its file is gone; only the label is left."})
        else:
            closed.append({**entry, "reason": "The file could not be opened; only the label is there."})
    result = {}
    if opened:
        result["opened"] = opened
        result["note"] = ("The user sees the opened files under your answer and can open them. Images/audio you can "
                          "take come in the user message right after this result (the tool added it, not the user) "
                          "and stay in the chat; a text's content is above. Answer from them.")
    if again:
        result["already_open"] = again
    if media or refs:
        result["_model_media"] = media + refs
    if attachments:
        result["_show_user"] = attachments  # The tool runner shows them to the user under this turn.
    if closed:
        result["not_opened"] = closed
    missing = [i for i in ids if i not in by_id]
    if missing:
        result["not_found"] = missing
    return result


class MemoryContextInput(ToolInput):
    id: int = Field(description="The id of a part from a search_memory result.")
    chat: bool = Field(default=False, description="true: also the messages of the chat it was saved from.")
    message_count: int = Field(default=6, ge=1, le=20, description="How many chat messages.")
    chat_position: Literal["around", "start"] = Field(
        default="around", description="around: the saved message and those near it; start: the chat's first ones.")
    start: int = Field(default=1, ge=1, le=1000,
                       description="Continue a long memory from this part (when an earlier result said 'rest').")
    neighbours: int = Field(default=0, ge=0, le=5,
                            description="0: the whole memory. 1-5: only this part and this many parts before and "
                                        "after it (around the matching place of a long text, audio or video).")


def _context_part(part: dict) -> dict:
    item = {"id": part["id"], "index": part["index"], "kind": KIND_NAMES.get(part["kind"], part["kind"]),
            ("text" if part["kind"] == "text" else "label"): part["text"]}
    if part.get("pieces"):
        item["part"] = f"{part['piece']} of {part['pieces']}"
    if part.get("span_start") is not None:
        item["time"] = f"{_clock(part['span_start'])}–{_clock(part['span_end'])}"
    if part.get("track") in TRACKS:
        item["track"] = TRACKS[part["track"]]
    if part.get("caption"):
        item["caption"] = part["caption"]
    if part.get("asked"):
        item["asked"] = True
    return item


CHAT_STATES = {
    "unknown": "It is not known which chat this memory came from (a note, a file or an old memory).",
    "deleted": "The chat it came from was deleted; only the memory's text is left.",
    "cleared": "The chat it came from was cleared; those messages are gone.",
}
SOURCES = {"chat_save": "saved from a chat", "file": "added as a file", "manual": "added as a note",
           "assistant": "added by the assistant"}


async def memory_context(args: MemoryContextInput, context: ToolContext) -> dict:
    """A memory row's whole group (what was saved with it, full texts) and, on request, the conversation it was
    saved from."""
    from orion.kernel import memory, memory_groups
    from orion.kernel.config import get_runtime_settings
    try:
        found = await memory_groups.memory_context(
            context.user_id, args.id, await get_runtime_settings(None, context.user_id), with_chat=args.chat,
            count=args.message_count, where=args.chat_position, start=args.start - 1, neighbours=args.neighbours)
    except memory.MemoryUnavailable as exc:
        return {"error": str(exc)}
    if found is None:
        return {"error": "There is no memory with this id."}
    result = {"id": args.id, "source": SOURCES.get(found["source_kind"], found["source_kind"]),
              "date": found["date"], "part_count": found["part_count"],
              "parts": [_context_part(part) for part in found["parts"]]}
    if found.get("neighbours"):
        result["scope"] = f"only {args.id} and {found['neighbours']} parts before and after it"
    if found["title"]:
        result["file" if found["source_kind"] == "file" else "chat_title"] = found["title"]
    if found["rest"]:
        result["rest"] = (f"{found['rest']} more parts; call again with start="
                          f"{found['parts'][-1]['index'] + 1} to continue.")
    if any(part["kind"] != "text" for part in found["parts"]):
        result["media_note"] = "To see or hear an image/audio/video, use open_memory_files."
    if found["source_kind"] == "file":
        result["file_note"] = "To give the user the file itself, use open_memory_files."
    chat = found.get("chat")
    if chat is not None:
        if chat["state"] == "ok":
            result["chat"] = {"title": chat.get("title"), "messages": [
                {"role": m["role"], "text": m["text"], "date": m["date"],
                 **({"saved": True} if m.get("saved") else {})} for m in chat["messages"]]}
        else:
            result["chat_state"] = CHAT_STATES[chat["state"]]
    return result
