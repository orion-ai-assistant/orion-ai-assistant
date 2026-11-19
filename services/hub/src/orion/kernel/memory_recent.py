"""Recently added (docs/hafiza-tasarimi.md §7.3, §12, §16 step 6): what Orion wrote into the memory by itself (the model's
`add_memory`, later the extraction of step 7), with where it came from and a one-press undo ("Geri al"). Behind the
per-user `memory_v2` setting.

What counts: a fact or an event whose `origin` is not the user (`model`, `extraction`) and that is the FIRST version of its
lineage (a version that replaced an older one was accepted by the user on a card: their choice, not an automatic write),
and a note the model saved (`source_kind 'assistant'`). What the user writes on the page is never listed (their own change).

Undo is the ordinary removal: the addition goes to the trash with its memory (`memory_trash`, restorable through the
answer's `trash_id`; a locked fact is marked removed, restorable through `restore`), so nothing here deletes for good and the
trash's own rules (days, purge, user separation) apply. A fact that is locked (§11.1) is listed without its text.

No table of its own: the facts, events and memories already hold `origin`, the time and the sources; three small partial
indexes (`memory._index_plan`) keep the list cheap however big the memory is. Every read filters by user_id."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orion.kernel import memory, memory_entities as me, memory_events as ev, memory_groups

DAYS = 30  # How far back the list goes.
PAGE = 30  # The list shows the last 30 additions of the last 30 days (days, limit: parameters).
SNIPPET = 240

TYPES = ("fact", "event", "note")


async def _chat_titles(conn, user_id: str, chat_ids: list[str]) -> dict[str, str]:
    """Titles of the chats the additions came from ({} when the chat store is not there)."""
    if not chat_ids or not await conn.fetchval("select to_regclass('orion_chats') is not null"):
        return {}
    return {r["id"]: r["title"] for r in await conn.fetch(
        "select id, title from orion_chats where user_id = $1 and id = any($2::text[])", user_id, chat_ids)}


def _snippet(text: str | None, limit: int = SNIPPET) -> str:
    return " ".join((text or "").split())[:limit]


async def recent(user_id: str, settings, days: int = DAYS, limit: int = PAGE, offset: int = 0) -> dict:
    """{"items": [...], "more": bool}, newest first. An item: {type: fact | event | note, id, key "type:id", title, text
    (none for a locked fact), locked, origin, added_at, chat: {id, title} | null, ...} (fact: about, category; event: date,
    precision)."""
    me.require(settings)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 365)))
    want = offset + limit + 1
    async with me._db() as conn:
        facts = await conn.fetch(
            "select f.id, f.statement, f.category, f.sensitive, f.recorded_at, f.origin, e.name, e.relation, e.kind, "
            "(select s.chat_id from orion_mem_source s where s.user_id = f.user_id and s.target_type = 'fact' and "
            " s.target_id = f.id and s.chat_id is not null order by s.id limit 1) as chat_id "
            "from orion_mem_fact f join orion_mem_entity e on e.id = f.entity_id and e.user_id = f.user_id "
            f"where f.user_id = $1 and f.origin <> 'user' and f.superseded_by is null and coalesce(f.lineage, f.id) = f.id "
            f"and f.recorded_at >= $2 and {me._live('f')} order by f.recorded_at desc, f.id desc limit $3",
            user_id, since, want)
        events = await conn.fetch(
            "select v.id, v.title, v.body, v.occurred_on, v.occurred_end, v.precision, v.recorded_at, v.origin, "
            "(select s.chat_id from orion_mem_source s where s.user_id = v.user_id and s.target_type = 'event' and "
            " s.target_id = v.id and s.chat_id is not null order by s.id limit 1) as chat_id "
            "from orion_mem_event v where v.user_id = $1 and v.origin <> 'user' and v.superseded_by is null and "
            f"coalesce(v.lineage, v.id) = v.id and v.recorded_at >= $2 and {ev._live('v')} "
            "order by v.recorded_at desc, v.id desc limit $3", user_id, since, want)
        notes = await conn.fetch(
            f"select {memory_groups.GROUP_KEY} as g, max(created_at) as at, (array_agg(text order by id))[1] as text, "
            "(array_agg(source_chat_title order by id))[1] as title from orion_memory where user_id = $1 and "
            f"source_kind = 'assistant' and not whole and created_at >= $2 and {memory.LIVE} "
            "group by 1 order by at desc limit $3", user_id, since, want)
        titles = await _chat_titles(conn, user_id, sorted({r["chat_id"] for r in [*facts, *events] if r["chat_id"]}))
    items = []

    def chat(r):
        return {"id": r["chat_id"], "title": titles.get(r["chat_id"])} if r["chat_id"] else None

    for r in facts:
        label = me.label_of({"name": r["name"], "relation": r["relation"], "kind": r["kind"]})
        items.append({"type": "fact", "id": r["id"], "key": f"fact:{r['id']}", "title": label,
                      "text": None if r["sensitive"] else _snippet(r["statement"]), "locked": bool(r["sensitive"]),
                      "category": r["category"], "origin": r["origin"], "added_at": r["recorded_at"], "chat": chat(r)})
    for r in events:
        items.append({"type": "event", "id": r["id"], "key": f"event:{r['id']}", "title": r["title"],
                      "text": _snippet(r["body"]) or None, "locked": False, "date": r["occurred_on"].isoformat(),
                      "end": r["occurred_end"].isoformat() if r["occurred_end"] else None, "precision": r["precision"],
                      "origin": r["origin"], "added_at": r["recorded_at"], "chat": chat(r)})
    for r in notes:
        items.append({"type": "note", "id": r["g"], "key": f"note:{r['g']}", "title": r["title"] or "Not",
                      "text": _snippet(r["text"]), "locked": False, "origin": "model", "added_at": r["at"], "chat": None})
    items.sort(key=lambda i: (i["added_at"], str(i["key"])), reverse=True)
    page = items[offset:offset + limit]
    for i in page:
        i["added_at"] = i["added_at"].isoformat()
    return {"items": page, "more": len(items) > offset + limit}


async def count(user_id: str, settings, days: int = 7) -> int:
    """How many additions of the last `days` days (the badge). Counted exactly, capped at 99."""
    return min(99, len((await recent(user_id, settings, days=days, limit=100))["items"]))


async def undo(user_id: str, settings, type_: str, ident: str) -> dict:
    """Geri al: take one automatic addition back. {"trash_id"} (restore: POST /memory/trash/restore) or, for a locked fact,
    {"restore": {"fact": id}}. LookupError when it is not this user's, is the user's own writing, or is already gone."""
    me.require(settings)
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    async with me._db() as conn:
        if type_ == "fact":
            ok = await conn.fetchval(
                f"select 1 from orion_mem_fact f where f.user_id = $1 and f.id = $2 and f.origin <> 'user' and "
                f"f.superseded_by is null and coalesce(f.lineage, f.id) = f.id and {me._live('f')}", user_id, int(ident))
        elif type_ == "event":
            ok = await conn.fetchval(
                f"select 1 from orion_mem_event v where v.user_id = $1 and v.id = $2 and v.origin <> 'user' and "
                f"v.superseded_by is null and coalesce(v.lineage, v.id) = v.id and {ev._live('v')}", user_id, int(ident))
        else:
            ok = await conn.fetchval(
                f"select 1 from orion_memory where user_id = $1 and {memory_groups.GROUP_KEY} = $2 and "
                f"source_kind = 'assistant' and not whole and {memory.LIVE} limit 1", user_id, str(ident)[:64])
    if not ok:
        raise LookupError(ident)
    if type_ == "fact":
        done = await me.remove_fact(user_id, settings, int(ident))
        return {"restore": {"fact": int(ident)}} if "restore" in done else {"trash_id": done["trash_id"]}
    if type_ == "event":
        return {"trash_id": (await ev.remove_event(user_id, settings, int(ident)))["trash_id"]}
    from orion.kernel import memory_trash
    removed = await memory_trash.trash_groups(user_id, [str(ident)[:64]])
    return {"trash_id": removed["trash_id"]}


async def review(user_id: str, settings) -> dict:
    """The page's two badges: {"pending": cards waiting, "recent": additions of the last 7 days}."""
    from orion.kernel import memory_proposals
    await memory_proposals.approve_pending(user_id, settings)  # memory_auto_approve: the waiting ones are applied.
    return {"pending": await memory_proposals.count_pending(user_id, settings),
            "recent": await count(user_id, settings), "auto_approve": bool(getattr(settings, "memory_auto_approve", False))}
