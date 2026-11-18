import json
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from .._base import ToolContext, ToolInput

KINDS = ("all", "text", "image", "audio", "video")
KIND_NAMES = {"text": "text", "image": "image", "audio": "audio recording", "video": "video"}
TRACKS = {"frames": "picture", "audio": "sound", "both": "picture and sound"}
PAGE_SIZE = 10


class MemoryQuery(ToolInput):
    text: str = Field(default="", max_length=2000,
                      description="What to look for, a short phrase (e.g. 'the user's daughter'). Leave empty when "
                                  "'id' is set.")
    id: int | None = Field(default=None,
                           description="Instead of text: the id of a memory part from an earlier result. Its own "
                                       "vector is the query, so you find memories like that image, audio, video or "
                                       "text (its own memory is not listed).")
    keyword: str = Field(default="", max_length=200,
                         description="Instead of text: an exact word or phrase the memory must contain (no meaning).")
    like: bool = Field(default=True,
                       description="true: results should be like this. false: memories like this (or, for a text, "
                                   "containing it) are left out.")


class MemoryWhen(ToolInput):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    from_: str = Field(default="", max_length=10, alias="from",
                       description="Start: YYYY-MM-DD, YYYY-MM (its first day) or YYYY (its first day). Empty: no start.")
    to: str = Field(default="", max_length=10,
                    description="End: YYYY-MM-DD, YYYY-MM (its last day) or YYYY (its last day). Empty: no end. For one "
                                "month or year give the same value in both.")


class MemorySearchInput(ToolInput):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
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
    in_: list[str] = Field(
        default_factory=list, max_length=10, alias="in",
        description="Only these collections: 'me' (who the user is), 'experiences' (dated events), 'chats' (past "
                    "conversations), 'documents' (files) or a project/album by its name; 'chat:<chat id>' searches inside "
                    "one chat. A personal question: me, experiences; something said before: chats. Empty: the whole "
                    "memory except the past conversations (ask for 'chats' to search them). Ignored while the "
                    "user's memory has no collections.")
    when: MemoryWhen | None = Field(
        default=None,
        description="Only memories dated within this range: events (their day or period overlaps it), facts that were true "
                    "then, and the photos / videos linked to such events. Use it for 'what did I do in March 2026?' with a "
                    "short query or none. Undated memories (notes, files) are not returned while it is set. Ignored while "
                    "the user's memory has no collections.")
    sensitive: bool = Field(
        default=False,
        description="true ONLY when the user directly asks about a locked topic (their health, religion, politics, "
                    "sex life, ethnicity, union, legal trouble, money or ID documents): then the locked facts that "
                    "match the query words are added to the answer as 'locked_facts'. Locked facts are never in "
                    "ordinary results. Ignored while the user's memory has no collections.")
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


MATCH = {"meaning": "meaning", "text": "text", "similar": "spelling"}
NOTE = ("Compare scores between results, not against a fixed number. open_memory_files shows media (and gives "
        "the user the file); memory_context reads the parts around a part.")


LABEL_CHARS = 140  # A result's label is a snippet; memory_context reads the rest.
MEDIA_LISTED = 6  # Other media parts of a mixed memory named on its result.


def _snippet(text: str | None, spans: list[dict] | None = None, limit: int = LABEL_CHARS) -> str | None:
    """`text` as a short one-line snippet: whitespace collapsed, at most `limit` characters; around the first matched
    word of the text (`spans`, offsets into `text`) when it sits past the start."""
    if text is None:
        return None
    hit = next((s for s in spans or [] if s.get("field") == "text" and s["end"] <= len(text)), None)
    start = max(0, hit["start"] - limit // 3) if hit and hit["start"] > limit // 2 else 0
    flat = " ".join(text[start:start + limit * 3].split())
    if start and flat:
        flat = "…" + flat
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _media_of(card: dict, shown_id) -> list[dict]:
    """The memory's other media parts as {id, kind, name} (no captions), those the query's words hit first, so the
    model can open the right file without another call."""
    hits = set(card.get("hit_parts") or [])
    media = []
    for part in card.get("parts") or []:
        if part["id"] == shown_id or part.get("kind") == "text":
            continue
        name = (part.get("text") or "").split(" — ", 1)[0]
        entry = {"id": part["id"], "kind": KIND_NAMES.get(part["kind"], part["kind"]), "name": _snippet(name, None, 80)}
        if part["id"] in hits:
            entry["matched"] = True
        media.append(entry)
    media.sort(key=lambda e: not e.get("matched"))
    return media[:MEDIA_LISTED]


def _result(card: dict, labels: list[str], words: str) -> dict:
    """One memory as the chat model reads it, lean: its best part (id, label), the score as a percent, the query
    that gave it and how it matched."""
    best = card.get("best_part") or {}
    kind = best.get("kind") or ("text" if card.get("text") else card.get("kind", "text"))
    row_id = best.get("id") or card.get("matched_part") or card.get("text_part")
    if row_id is None and card.get("parts"):
        row_id = card["parts"][0]["id"]
    item = {"id": row_id, "kind": KIND_NAMES.get(kind, kind), "date": _date(card.get("created_at"))}
    label = best.get("text") if best else None
    if label is None:
        label = card.get("text") if kind == "text" else next(
            (p["text"] for p in card.get("parts") or [] if p["id"] == row_id), None)
    if label is not None:
        item["label"] = _snippet(label, card.get("spans") if row_id == card.get("matched_part") else None)
    if best.get("chat"):  # A past conversation: where it is, so it can be opened (memory_context chat=true).
        item["chat"] = {"id": best["chat"]["id"], "title": card.get("title"), "message_id": best["chat"]["message"]}
    if card.get("source_kind") == "file" and card.get("title") and kind == "text":
        item["label"] = _snippet(f"{card['title']}: {item.get('label') or ''}")
    if card.get("part_count", 1) > 1:
        media = _media_of(card, row_id)
        if media:
            item["media"] = media
    if card.get("score") is not None:
        match = card.get("match") or "meaning"
        item["score"] = round(float(card["score"]) * 100)
        if card.get("via"):
            item["query"] = card["via"]
        elif match != "meaning" and words:
            item["query"] = json.dumps(words[:60], ensure_ascii=False)
        elif len(labels) == 1:
            item["query"] = labels[0]
        else:
            item["query"] = "all together"
        item["match"] = MATCH.get(match, "meaning")
    counts = card.get("counts") or {}
    if card.get("part_count", 1) > 1:
        item["memory_has"] = ", ".join(f"{n} {KIND_NAMES.get(k, k)}{'s' if n > 1 and k != 'audio' else ''}"
                                       for k, n in counts.items())
    if (best.get("pieces") or 0) > 1:
        item["part"] = f"{best['piece']} of {best['pieces']}"
    if best.get("span_start") is not None:
        item["time"] = f"{_clock(best['span_start'])}–{_clock(best['span_end'])}"
    if (card.get("matched_parts") or 0) > 1:
        item["parts_matched"] = card["matched_parts"]
    return item


async def search_memory(args: MemorySearchInput, context: ToolContext) -> dict:
    from orion.kernel import memory, memory_entities, memory_groups
    from orion.kernel.config import get_runtime_settings
    queries = [{"text": q.text, "id": q.id, "keyword": q.keyword, "like": q.like} for q in args.queries
               if q.id is not None or q.text.strip() or q.keyword.strip()]
    size = args.page_size
    settings = await get_runtime_settings(None, context.user_id)
    extra = {}
    if args.when is not None:
        from orion.kernel import memory_events
        try:
            span = memory_events.parse_range(args.when.from_, args.when.to)
        except ValueError as exc:
            return {"error": str(exc)}
        if span:
            extra["when"] = span
    try:
        found = await memory_groups.search_queries(
            context.user_id, settings, queries, **extra,
            mode="each" if args.mode == "separate" else "together", kind=args.kind,
            vector_space=args.model.strip() or "all", limit=size, offset=(args.page - 1) * size,
            **({"collections": wanted} if (wanted := [c for c in args.in_ if c.strip()]) else {}))
    except (memory.MemoryUnavailable, ValueError) as exc:  # QueryError and an unknown collection are ValueErrors.
        return {"error": str(exc)}
    labels = found.get("labels") or []
    words = ", ".join(q["text"].strip() or q["keyword"].strip() for q in queries if q["like"] and q["id"] is None)
    results = [_result(card, labels, words) for card in found["groups"]]
    for card, item in zip(found["groups"], results):
        if card.get("fact"):  # A fact: who it is about, its kind and since when (memory_groups adds it to the card).
            item["fact"] = card["fact"]
        if card.get("event"):  # An event: its day (precision), who took part, where (memory_groups adds it).
            item["event"] = card["event"]
    result = {"page": args.page, "results": results, "more": found["more"]}
    if args.sensitive and memory_entities.enabled(settings):
        result["locked_facts"] = await memory_entities.search_locked(context.user_id, settings, words)
        result["locked_note"] = ("Locked facts match by words only, and only because the user asked. Tell them what "
                                 "they asked for; do not bring these up on your own.")
    if results:
        result["note"] = NOTE if any(q["like"] for q in queries) else "Newest first. " + NOTE
    else:
        result["note"] = "No memories on this page." if args.page > 1 else "No memories for this search."
    if found.get("excluded_list"):
        result["left_out"] = found["excluded_list"]
    if found.get("semantic_reason"):
        result["meaning_search_off"] = found["semantic_reason"]
    return result


class MemoryAddInput(ToolInput):
    text: str = Field(min_length=1, max_length=2 * 1024 * 1024,
                      description="What to remember: a self-contained sentence (e.g. 'The user's daughter is called "
                                  "Elif.') or a longer note.")
    title: str = Field(default="", max_length=120,
                       description="Optional name for the memory, shown as its title (e.g. 'Elif'). Leave empty for "
                                   "a short fact.")
    type: Literal["note", "fact", "event"] = Field(
        default="note",
        description="note (default): a note saved as it is. fact: ONE short sentence about a person, place, pet or the "
                    "user, filed under 'me' with its history (needs the user's collections to be on). Use fact for "
                    "'my mother's birthday is 17 November'; note for anything longer. event: something that happened on a "
                    "date ('we went to Cappadocia in June', 'today was my mother's birthday'), filed under 'experiences' "
                    "(needs the user's collections to be on). 'text' is what happened; give 'date'.")
    about: str = Field(
        default="", max_length=120,
        description="type=fact: who or what the fact is about: a name or relation as the user says it ('Ayşe', "
                    "'my mother'); empty or 'me' for the user. An unknown name makes a new entity; if two entities "
                    "fit, the answer lists them: pass '#<id>' to choose one.")
    about_kind: Literal["person", "place", "org", "pet", "thing"] = Field(
        default="person", description="type=fact: what a NEW entity is (ignored for one that exists).")
    relation: str = Field(default="", max_length=120,
                          description="type=fact: how a new entity relates to the user ('mother', 'colleague').")
    category: Literal["identity", "relationship", "preference", "opinion", "work", "place", "date", "other", "health",
                      "sexual_life", "religion", "political", "ethnicity", "union", "biometric_genetic", "legal",
                      "finance", "id_number"] = Field(
        default="other",
        description="type=fact: what kind of fact. The last ten (health ... id_number) are locked kinds: say them only "
                    "when the user themselves said it; they are never saved without the user's press on a card, and "
                    "ID or card numbers are never stored, only that one exists.")
    since: str = Field(default="", max_length=10,
                       description="type=fact: ISO date (YYYY-MM-DD) from which it is true, if the user said (resolve "
                                   "'since March' yourself); empty if unknown.")
    date: str = Field(
        default="", max_length=10,
        description="type=event: when it happened: YYYY-MM-DD (or YYYY-MM, YYYY when only that is known). The Hub checks it "
                    "against the time of the message: a relative word in 'said_as' (today, yesterday, tomorrow, last "
                    "friday; bugün, dün, yarın, geçen cuma) is resolved by the Hub itself. For anything vaguer ('last "
                    "summer') give your best date and precision=approx. type=fact with repeats=yearly: the day it repeats "
                    "on (YYYY-MM-DD or MM-DD).")
    end_date: str = Field(default="", max_length=10,
                          description="type=event: the last day of a trip or period (YYYY-MM-DD); empty for one day.")
    precision: Literal["", "day", "month", "year", "approx"] = Field(
        default="", description="type=event: how exact the date is. Empty: what the date's form says (a full date = day).")
    said_as: str = Field(default="", max_length=120,
                         description="type=event: the user's own words for the time ('bugün', 'geçen yaz'), kept with the "
                                     "event.")
    participants: list[str] = Field(
        default_factory=list, max_length=20,
        description="type=event: who took part, as the user names them ('Ayşe', 'my mother'; 'me' for the user). An unknown "
                    "name makes a new person; if two entities fit, the answer lists them: pass '#<id>'.")
    place: str = Field(default="", max_length=120, description="type=event: where it happened ('Cappadocia').")
    mood: str = Field(default="", max_length=40, description="type=event: how the user felt, if they said.")
    items: list[int] = Field(
        default_factory=list, max_length=50,
        description="type=event: ids of existing memories (from search_memory) that belong to the event, such as its "
                    "photos or videos.")
    event: int | None = Field(
        default=None,
        description="type=event: the id of an existing event (from search_memory's 'event'): then nothing new is written, "
                    "'items' are linked to that event.")
    collection: str = Field(
        default="", max_length=80,
        description="Optional: the name of one of the user's albums or projects the new memory also joins. You do not "
                    "create albums; an unknown name is an error. Needs the user's collections.")
    repeats: Literal["none", "yearly"] = Field(
        default="none",
        description="type=fact: yearly when it falls on the same day every year (a birthday, an anniversary); then 'date' "
                    "is that day.")
    replaces: int | None = Field(
        default=None,
        description="type=fact: the id of a current fact (from search_memory's 'fact') that this sentence replaces "
                    "because things changed ('I moved to Istanbul'). You never close or rewrite a fact: this opens a "
                    "card for the user, who keeps the old one as history when they accept.")


class MemoryDeleteInput(ToolInput):
    ids: list[int] = Field(default_factory=list, max_length=20, description="Ids from search_memory results.")
    whole_memory: bool = Field(default=False,
                               description="false: only these parts. true: the whole memory each id belongs to "
                                           "(everything saved with it).")
    confirmation_id: str = Field(default="", max_length=64,
                                 description="Only to ask what became of an earlier delete request: its "
                                             "confirmation_id. Leave empty (and give ids) to request a delete.")


class MemoryOpenInput(ToolInput):
    ids: list[int] = Field(min_length=1, max_length=50,
                           description="Ids from search_memory results: images, audio, video, text files, notes. As "
                                       "many open as fit the chat model's context (about 40% of it); the rest are "
                                       "reported as not opened and can be asked for again.")


async def add_fact(args: MemoryAddInput, context: ToolContext, settings) -> dict:
    """add_memory type=fact: a clearly stated fact is written at once with the chat as its source; a replacement, or a
    locked kind, only opens a card the user answers (§8). {"added": {...}}, {"same_as": id} or {"proposal": id}."""
    from orion.kernel import memory, memory_entities as me
    if not me.enabled(settings):
        return {"error": "Facts need the user's memory collections (the 'memory_v2' setting), which are off: save "
                         "it as a note (type=note) instead."}
    if not context.chat_id:
        return {"error": "A fact needs a source chat; nothing was saved."}
    try:
        category = me.category_of(args.category)
        recurring = None
        if args.repeats == "yearly":
            if not args.date:
                return {"error": "repeats=yearly needs 'date': the day it repeats on (YYYY-MM-DD or MM-DD)."}
            recurring = me.recurring_of(args.date)
        if args.replaces is not None:
            done = await me.propose_supersede(context.user_id, settings, args.replaces, args.text, since=args.since or None,
                                              chat_id=context.chat_id, category=category if args.category != "other" else None,
                                              recurring=recurring)
        else:
            done = await me.write_by_model(context.user_id, settings, context.chat_id, args.about, args.about_kind,
                                           args.relation or None, category, args.text, since=args.since or None,
                                           recurring=recurring)
    except me.Ambiguous as exc:
        return {"error": str(exc), "candidates": exc.candidates}
    except LookupError:
        return {"error": "There is no current fact with that id (the user may have removed it); search_memory again."}
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"error": str(exc)}
    if "proposal" in done:
        return {"status": "awaiting_user_confirmation", "proposal_id": done["proposal"],
                "instruction": "NOTHING is saved or changed yet. A card with Onayla / Düzenle / Reddet was shown to the "
                               "user. Tell them briefly what you suggested and that it waits for them; do not say it "
                               "was saved."}
    if "same_as" in done:
        return {"same_as": done["same_as"], "note": "This was already known (a new source was added to it)."}
    fact = done["fact"]
    added = {"fact_id": fact["id"], "about": done["entity"]["name"], "statement": args.text,
             "since": fact["since"], "category": fact["category"]}
    if fact.get("recurring"):
        added["repeats"] = fact["recurring"]
    return {"added": added}


def _span(event: dict) -> str:
    return event["date"] + (f" to {event['end']}" if event.get("end") else "")


async def add_event(args: MemoryAddInput, context: ToolContext, settings) -> dict:
    """add_memory type=event: written at once with the chat as its source (§8: an event the user said clearly); the date is
    checked against the message's time. With `event`: only links `items` to that event. {"added": {...}}, {"same_as": id}
    or {"linked": n}."""
    from orion.kernel import memory, memory_albums, memory_events as ev, memory_entities as me
    if not ev.enabled(settings):
        return {"error": "Events need the user's memory collections (the 'memory_v2' setting), which are off: save "
                         "it as a note (type=note) instead."}
    if not context.chat_id:
        return {"error": "An event needs a source chat; nothing was saved."}
    try:
        async with me._db() as conn:
            groups = await ev.groups_of_rows(conn, context.user_id, args.items) if args.items else []
            album = await memory_albums.album_id_of(conn, context.user_id, args.collection) if args.collection else None
        if args.event is not None:
            if not groups:
                return {"error": "Give 'items': the ids of the memories to link to that event."}
            done = await ev.set_items(context.user_id, settings, args.event, add=groups)
            return {"linked": done["linked"], "event_id": args.event}
        when = ev.resolve_date(args.date or None, args.end_date or None, args.precision or None, args.said_as or None)
        done = await ev.add_event(context.user_id, settings, args.title, when, body=args.text, mood=args.mood or None,
                                  participants=args.participants, place=args.place or None, items=groups, origin="model",
                                  source={"chat_id": context.chat_id}, album=album)
    except me.Ambiguous as exc:
        return {"error": str(exc), "candidates": exc.candidates}
    except LookupError as exc:
        return {"error": str(exc.args[0]) if exc.args and isinstance(exc.args[0], str) else
                "There is no event with that id (the user may have removed it); search_memory again."}
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"error": str(exc)}
    if "same_as" in done:
        return {"same_as": done["same_as"], "note": "This event was already known (the new source and links were added to it)."}
    event = done["event"]
    added = {"event_id": event["id"], "title": event["title"], "date": _span(event), "precision": event["precision"]}
    if event.get("said_as"):
        added["said_as"] = event["said_as"]
    if event["participants"]:
        added["with"] = [p["name"] for p in event["participants"]]
    if event["place"]:
        added["place"] = event["place"]["name"]
    if event["items"]:
        added["photos"] = len(event["items"])
    result = {"added": added}
    if when.get("note"):
        result["date_note"] = when["note"]
    return result


async def add_memory(args: MemoryAddInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    settings = await get_runtime_settings(None, context.user_id)
    if args.type == "fact":
        return await add_fact(args, context, settings)
    if args.type == "event":
        return await add_event(args, context, settings)
    if args.collection:  # A note that also joins one of the user's albums.
        return await add_note_to_album(args, context, settings)
    try:
        added = await memory.add_note(context.user_id, args.text, settings, source_kind="assistant", title=args.title)
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"error": str(exc)}
    return {"added": args.text} if added else {"note": "This was already in memory.", "text": args.text}


async def add_note_to_album(args: MemoryAddInput, context: ToolContext, settings) -> dict:
    from orion.kernel import memory, memory_albums, memory_collections, memory_entities as me
    if not memory_collections.enabled(settings):
        return {"error": "Albums need the user's memory collections (the 'memory_v2' setting), which are off."}
    try:
        async with me._db() as conn:
            album = await memory_albums.album_id_of(conn, context.user_id, args.collection)
        saved = await memory.add_note_to(context.user_id, args.text, settings, "assistant", title=args.title)
        if saved.get("group_id"):
            await memory_albums.set_members(context.user_id, settings, album, add=[saved["group_id"]])
    except LookupError as exc:
        return {"error": str(exc.args[0]) if exc.args and isinstance(exc.args[0], str) else str(exc)}
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"error": str(exc)}
    if not (saved["added"] or saved.get("job")):
        return {"note": "This was already in memory.", "text": args.text}
    return {"added": args.text, "album": args.collection}


async def delete_memory(args: MemoryDeleteInput, context: ToolContext) -> dict:
    """Never deletes: records what would go and shows the user a confirmation card (memory_pending); the user's Sil
    press moves it to the trash (restorable). With `confirmation_id`: the outcome so far (pending, deleted,
    declined, expired)."""
    from orion.kernel import memory, memory_pending
    try:
        if args.confirmation_id:
            found = await memory_pending.status(context.user_id, args.confirmation_id.strip())
            if found is None:
                return {"note": "No such confirmation."}
            outcome = {"pending": "The user has not answered yet; nothing is deleted.",
                       "claimed": "The user confirmed; the delete is being carried out.",
                       "deleted": "The user confirmed: it was moved to the memory's trash (Çöp kutusu), where they "
                                  "can restore it until it is deleted for good.",
                       "declined": "The user declined: nothing was deleted. Do not ask again unless they do.",
                       "expired": "Nobody answered in time; nothing was deleted."}[found["state"]]
            return {"state": found["state"], "deleted_ids": found["deleted"], "note": outcome}
        if not args.ids:
            return {"error": "Give the ids to delete (from search_memory), or a confirmation_id to check."}
        pending = await memory_pending.create(context.user_id, context.chat_id, args.ids, args.whole_memory)
    except memory.MemoryUnavailable as exc:
        return {"error": str(exc)}
    if pending is None:
        return {"note": "No memory has these ids; nothing to delete."}
    return {"status": "awaiting_user_confirmation", "confirmation_id": pending["token"],
            "will_delete": pending["will_delete"][:20], "parts": pending["parts"],
            "whole_memory": pending["whole_memory"],
            "instruction": "NOTHING is deleted yet. A confirmation card with a Sil button was shown to the user. Tell "
                           "them briefly what you asked to delete and that it waits for their press; do not say it "
                           "was deleted. If they say they answered, call delete_memory with this confirmation_id to "
                           "see the outcome."}


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
    entry["parts"] = len(pieces)
    sent, sent_sha = raw, sha
    if len(doc["text"]) <= memory_groups.GROUP_TEXT_CAP:
        entry["you_get"] = "the whole text, in the message right after this result"
    else:
        # Too long for the context at once: the asked part with two neighbours each side reaches the model; the
        # user has the whole file.
        lo, hi = max(0, asked - 2), min(len(pieces), asked + 3)
        sent = memory.join_pieces(pieces[lo:hi]).encode("utf-8")
        sent_sha = hashlib.sha256(sent).hexdigest()
        conn = await memory._connect()
        try:
            async with conn.transaction():
                await hold_media(conn, sent_sha, mime, sent)
                await link_media(sent_sha, "chat", context.chat_id, conn=conn)
        finally:
            await conn.close()
        entry["you_get"] = (f"parts {lo + 1}-{hi} of {len(pieces)} (around the one asked), in the message right "
                            "after this result")
        entry["read_more"] = (f"memory_context with id {pieces[lo]['id']} or {pieces[hi - 1]['id']} and "
                              "neighbours=3 reads the parts before or after")
    return {"entry": entry, "attachment": attachment_of(row, context.chat_id),
            "ref": {"memory_id": row_id, "kind": "document", "media_id": sent_sha, "source": sha,
                    "mime_type": mime, "label": doc["name"]}}


async def open_memory_files(args: MemoryOpenInput, context: ToolContext) -> dict:
    """Puts memory files in front of the user (under this turn, as attachments they can open) and in front of the
    model: images/audio/video it can take go in `_model_media` (a tool message cannot carry media on
    OpenAI-compatible APIs: the tool runner keeps them on the tool message as references and hands them to the model
    in a user message right after it, on this and every later turn, see tool_history.media_message); a text memory
    (a .txt/.md file, a note) reaches the model the same way, as a text attachment: the result holds only what was
    opened (no file content), so the text is in the conversation once."""
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    settings = await get_runtime_settings(None, context.user_id)
    can = await memory.chat_model_media(settings)
    kinds = {kind for kind in ("image", "audio") if can[kind]}
    asked = list(dict.fromkeys(args.ids))
    ids, later, plan = await memory.open_plan(context.user_id, asked, settings)
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
        result["note"] = ("The user sees the opened files under your answer and can open them. What you can take "
                          "(images, audio, texts) comes in the user message right after this result (the tool added "
                          "it, not the user) and stays in the chat. Answer from it.")
    if again:
        result["already_open"] = again
    if media or refs:
        result["_model_media"] = media + refs
    if attachments:
        result["_show_user"] = attachments  # The tool runner shows them to the user under this turn.
    if closed:
        result["not_opened"] = closed
    missing = [i for i in asked if i not in by_id and i not in later]
    if missing:
        result["not_found"] = missing
    if later:
        result["not_opened_yet"] = {
            "ids": later, "reason": f"Not opened: they would not fit the chat model's context (about {plan['budget']} "
                                    "tokens for files at once). Ask again for these, once you have used what is open."}
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
           "assistant": "added by the assistant", "fact": "a fact about a person, place or the user",
           "event": "an event with a date"}


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
    if found["source_kind"] == "fact":  # A fact: who it is about, its past versions and where it came from.
        from orion.kernel import memory_entities
        fact_id = await memory_entities.fact_of_group(context.user_id, found["group"])
        about = await memory_entities.fact_context(context.user_id, fact_id) if fact_id else None
        if about:
            result["fact"] = {"id": fact_id, "about": about["entity"]["name"], "category": about["category"],
                              "versions": [{"id": v["id"], "statement": v["statement"], "since": v["since"],
                                            "until": v["until"], "current": v["current"]} for v in about["versions"]],
                              "sources": about["sources"]}
    if found["source_kind"] == "event":  # An event: its date, who took part, where and its photos.
        from orion.kernel import memory_events
        event_id = await memory_events.event_of_group(context.user_id, found["group"])
        about = await memory_events.event_context(context.user_id, await get_runtime_settings(None, context.user_id),
                                                  event_id) if event_id else None
        if about:
            result["event"] = about
    elif found["source_kind"] != "fact":  # Any other memory (a photo...): the events it belongs to.
        from orion.kernel import memory_events
        events = await memory_events.events_of_item(context.user_id, found["group"])
        if events:
            result["events"] = events
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
