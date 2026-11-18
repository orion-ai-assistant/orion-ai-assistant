"""Events (docs/hafiza-tasarimi.md §4, §5, §9, §12, §16 step 5): the "Yaşadıklarım" collection. An event is a dated
experience: a title, when it happened (`occurred_on`, a day or the first day of a month / year / range, `occurred_end` the
last one; `precision` day | month | year | approx), what the user said ("bugün", "geçen yaz": `said_as`), who took part and
where (entities), and the photos / videos that belong to it (items). Behind the per-user `memory_v2` setting; with it off
nothing here runs.

Dates. The model writes an ISO date (YYYY-MM-DD, YYYY-MM or YYYY); the Hub checks it against the message's own time
(resolve_date): a relative word from a short exact table ("bugün", "dün", "yarın", "geçen cuma", today / yesterday /
tomorrow / last friday...) is resolved by the Hub from that time and wins over a date that disagrees (the answer says so),
every other phrase ("geçen yaz") keeps the model's date with `precision = approx`. Nothing here guesses: an event without a
date is refused (the answer says today's date). The original words and the precision are stored.

Versions, never overwrites (like facts). An event the user corrects is a new row (same `lineage`); the old one is closed
(`expired_at`, `superseded_by`) and loses its vector row; the links move to the new version. The model cannot change an
event, only add one.

How an event is searched. Its vector is an ordinary `orion_memory` row (kind 'text', source_kind 'event', its own group, main
collection `yasadiklarim`), embedded as "2026-11-17 Salı, Kasım 2026: <title>. Katılanlar: ... Yer: ...": the page, map,
counters, trash, model move and `search_memory` (`in: ["experiences"]`) need no new path. An event is live while that memory
has a live row (the page's own delete, or the trash, removes it; restoring brings it back; deleting for good removes the
event rows: after_purge). `search_memory(when=...)` is one SQL condition (when_sql) on the memory's group.

Links (`orion_mem_link`): event -> entity (role katılan | yer) and event -> item (role fotoğraf; the item is a memory, named by
its group key). An item removed to the trash stays linked and shows greyed; deleted for good, its link goes. Merging two
entities moves their links (memory_entities.merge_entities records them, undo puts them back).

Yearly recurrences (birthdays, anniversaries) are facts with `recurring = 'yearly:MM-DD'` (memory_entities): upcoming() lists
the next ones.

Every table has user_id and every read filters by it."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from orion.kernel import memory, memory_collections, memory_entities as me

logger = logging.getLogger(__name__)

EVENT = "event"  # source_kind of an event's vector row.
COLLECTION = "yasadiklarim"
PRECISIONS = ("day", "month", "year", "approx")
PARTICIPANT, PLACE, PHOTO = "katılan", "yer", "fotoğraf"
MAX_TITLE = 160
MAX_BODY = 2000  # Embedded as one row with the title: a longer account is a note (§6's windows for long diaries stay with notes).
MAX_PEOPLE = 20
MAX_ITEMS = 100
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

# Words that name a day by the message's own day: folded text -> days from it. Exact words only (no guessing).
RELATIVE_DAYS = {
    "today": 0, "bugun": 0, "yesterday": -1, "dun": -1, "day before yesterday": -2, "evvelsi gun": -2,
    "evvelki gun": -2, "onceki gun": -2, "tomorrow": 1, "yarin": 1, "day after tomorrow": 2, "obur gun": 2}
WEEKDAY_NAMES = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
                  "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
LAST_WEEKDAY = re.compile(r"^(?:gecen|last|onceki)\s+(\w+)$")
ISO = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def enabled(settings) -> bool:
    return me.enabled(settings)


def require(settings) -> None:
    me.require(settings)


def today_of(reference: datetime | None = None) -> date:
    """The day of `reference` (a message's time) in this PC's time zone; today when none."""
    moment = reference or datetime.now().astimezone()
    return (moment.astimezone() if moment.tzinfo else moment).date()


# --- Dates ------------------------------------------------------------------------------------------------------------

def _last_day(year: int, month: int) -> date:
    return (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)


def parse_iso(text: str | None) -> tuple[int, int | None, int | None] | None:
    """(year, month, day) of "YYYY", "YYYY-MM" or "YYYY-MM-DD" (month/day None when absent); None for an empty text;
    ValueError for anything else or an impossible date."""
    text = (text or "").strip()
    if not text:
        return None
    found = ISO.match(text)
    if found is None:
        raise ValueError(f"A date must be ISO: YYYY-MM-DD, YYYY-MM or YYYY, got {text!r}.")
    year, month, day = (int(g) if g else None for g in found.groups())
    try:
        date(year, month or 1, day or 1)
    except ValueError as exc:
        raise ValueError(f"{text!r} is not a real date.") from exc
    return year, month, day


def relative_day(said_as: str | None, today: date) -> date | None:
    """The day an exact relative word names, counted from `today` (the message's day): "bugün", "dün", "yarın",
    "geçen cuma" / "last friday" (the latest such weekday before today). None for any other words."""
    key = " ".join(memory.fold((said_as or "").strip().strip(".!")).split())
    if key in RELATIVE_DAYS:
        return today + timedelta(days=RELATIVE_DAYS[key])
    found = LAST_WEEKDAY.match(key)
    if found and found.group(1) in WEEKDAY_NAMES:
        return today - timedelta(days=(today.weekday() - WEEKDAY_NAMES[found.group(1)]) % 7 or 7)
    return None


def resolve_date(date_text: str | None, end_text: str | None = None, precision: str | None = None,
                 said_as: str | None = None, reference: datetime | None = None) -> dict:
    """The stored date of an event: {"on", "end", "precision", "said_as", "note"} from what was given.
    `date_text`: ISO (a month or a year are allowed); `said_as`: the user's words; `reference`: the time of the message
    the event was told in (now when None). An exact relative word in `said_as` is resolved from that time and wins over a
    date that disagrees (`note` says so). `precision`: day | month | year | approx; empty = what the date's form says;
    month / year cover the whole month / year (`end` = its last day); approx keeps the date (or the month / year) as a range
    when the form is partial or `end_text` is given; a day with `end_text` is a range of exact days. ValueError (English,
    for the model) for no date, a bad one, an unknown precision or an end before the start."""
    today = today_of(reference)
    said = " ".join((said_as or "").split())[:120] or None
    precision = (precision or "").strip().lower() or None
    if precision and precision not in PRECISIONS:
        raise ValueError(f"precision must be one of {', '.join(PRECISIONS)}.")
    form = parse_iso(date_text)
    end = parse_iso(end_text)
    if end is not None and (end[1] is None or end[2] is None):
        raise ValueError("end_date must be a full day (YYYY-MM-DD).")
    note = None
    relative = relative_day(said, today)
    if relative is not None:
        if form is not None and form[1] and form[2] and date(*form) != relative:
            note = (f"'{said}' counted from the message's day ({today.isoformat()}) is {relative.isoformat()}: "
                    f"that was used, not {date(*form).isoformat()}.")
        return {"on": relative, "end": None, "precision": "day", "said_as": said, "note": note}
    if form is None:
        raise ValueError(f"An event needs a date (YYYY-MM-DD; today is {today.isoformat()}, {WEEKDAYS[today.weekday()]}). "
                         "For a vague time ('last summer') give your best date and precision=approx.")
    year, month, day = form
    natural = "day" if day else "month" if month else "year"
    precision = precision or natural
    if precision == "day" and natural != "day":
        raise ValueError("precision=day needs a full date (YYYY-MM-DD); use month, year or approx for a partial one.")
    start = date(year, month or 1, day or 1)
    last = None
    if precision == "year":
        start, last = date(year, 1, 1), date(year, 12, 31)
    elif precision == "month":
        if month is None:
            raise ValueError("precision=month needs a month (YYYY-MM).")
        start, last = date(year, month, 1), _last_day(year, month)
    elif precision == "approx" and natural != "day":
        last = _last_day(year, month) if month else date(year, 12, 31)
    if end is not None and precision in ("day", "approx"):
        last = date(*end)
    if last is not None and last < start:
        raise ValueError("end_date is before the date.")
    return {"on": start, "end": last if last != start else None, "precision": precision, "said_as": said, "note": note}


def range_text(on: date, end: date | None, precision: str) -> str:
    """How a date reads in an event's embedded text: "2026-11-17 Salı, Kasım 2026", "Kasım 2026", "2026"."""
    if precision == "year":
        return str(on.year)
    if precision == "month":
        return f"{MONTHS[on.month - 1]} {on.year}"
    head = f"{on.isoformat()} {WEEKDAYS[on.weekday()]}, {MONTHS[on.month - 1]} {on.year}"
    return head if not end else f"{head} - {end.isoformat()}"


def parse_range(start: str | None, end: str | None) -> tuple[date | None, date | None] | None:
    """The `when` range of a search: a partial date in `start` is its first day, in `end` its last; either may be empty
    (open). None when both are empty. ValueError for a bad date or an end before the start."""
    first, last = parse_iso(start), parse_iso(end)
    if first is None and last is None:
        return None
    a = date(first[0], first[1] or 1, first[2] or 1) if first else None
    b = None
    if last:
        b = date(last[0], last[1] or 12, last[2] or (_last_day(last[0], last[1] or 12).day))
    if a and b and b < a:
        raise ValueError("when.to is before when.from.")
    return a, b


async def when_keys(conn, user_id: str, when: tuple[date | None, date | None]) -> tuple[list[str], list[int]]:
    """The memories dated within `when` (parse_range): an event whose days overlap it, a fact that was true then (it has a
    start; an open-ended one counts) and the items (photos, videos...) linked to such an event. As memory group keys:
    (real group ids, row ids of memories saved before groups). One query on the caller's connection; the keys are then the
    search's own candidates (when_sql), ranked exactly."""
    first, last = when
    params: list = [user_id]
    holds, valid = [], []
    if last is not None:
        params.append(last)
        holds.append(f"e.occurred_on <= ${len(params)}")
        valid.append(f"f.valid_from <= ${len(params)}")
    if first is not None:
        params.append(first)
        holds.append(f"coalesce(e.occurred_end, e.occurred_on) >= ${len(params)}")
        valid.append(f"(f.valid_to is null or f.valid_to >= ${len(params)})")
    inside = " and ".join(["e.user_id = $1", "e.superseded_by is null", *holds])
    fact = " and ".join(["f.user_id = $1", "f.superseded_by is null", "f.group_id is not null", "f.valid_from is not null",
                         *valid])
    rows = await conn.fetch(
        f"select e.group_id as k from orion_mem_event e where {inside} "
        f"union select f.group_id from orion_mem_fact f where {fact} "
        f"union select l.to_id from orion_mem_link l join orion_mem_event e on e.id = l.from_id and e.user_id = l.user_id "
        f"where l.user_id = $1 and l.from_type = 'event' and l.to_type = 'item' and {inside}", *params)
    return _keys([r["k"] for r in rows])


def when_sql(keys: tuple[list[str], list[int]], params: list) -> str:
    """The SQL condition (` and ...`) for rows of the memories `keys` (when_keys): plain lookups by the group and row indexes,
    so the cost follows the number of dated memories, not the size of the memory."""
    params.append(keys[0])
    first = len(params)
    params.append(keys[1])
    return f" and (group_id = any(${first}::text[]) or (group_id is null and id = any(${len(params)}::bigint[])))"


# --- Reading ----------------------------------------------------------------------------------------------------------

_EVENT = ("e.id, e.lineage, e.title, e.body, e.occurred_on, e.occurred_end, e.precision, e.said_as, e.mood, e.recorded_at, "
          "e.expired_at, e.superseded_by, e.origin, e.group_id")


def _live(alias: str) -> str:
    """An event (alias) whose memory has a live row (not removed to the trash)."""
    return (f"exists (select 1 from orion_memory m where m.user_id = {alias}.user_id and m.group_id = {alias}.group_id "
            f"and not m.whole and not exists (select 1 from orion_mem_trash t where t.memory_id = m.id))")


def _keys(groups: list[str]) -> tuple[list[str], list[int]]:
    """Memory group keys split into real group ids and "r<id>" keys of memories saved before groups (their row id)."""
    loose = [int(g[1:]) for g in groups if re.fullmatch(r"r\d+", g)]
    return [g for g in groups if not re.fullmatch(r"r\d+", g)], loose


def _item_live(conn_alias: str = "m") -> str:
    return f"not {conn_alias}.whole and not exists (select 1 from orion_mem_trash t where t.memory_id = {conn_alias}.id)"


def _short(r) -> dict:
    return {"id": r["id"], "title": r["title"], "date": r["occurred_on"].isoformat(),
            "end": r["occurred_end"].isoformat() if r["occurred_end"] else None, "precision": r["precision"],
            "said_as": r["said_as"]}


async def _decorate(conn, user_id: str, rows: list) -> list[dict]:
    """Events as the page and the tool read them: participants, place and items, three queries for the whole list."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    links = await conn.fetch("select from_id, to_type, to_id, role from orion_mem_link where user_id = $1 and "
                             "from_type = 'event' and from_id = any($2::bigint[]) order by created_at, to_id", user_id, ids)
    entity_ids = sorted({int(link["to_id"]) for link in links if link["to_type"] == "entity"})
    entities = {r["id"]: r for r in await conn.fetch(
        f"select {me._ENTITY} from orion_mem_entity where user_id = $1 and id = any($2::bigint[])", user_id, entity_ids)} \
        if entity_ids else {}
    groups = sorted({link["to_id"] for link in links if link["to_type"] == "item"})
    items: dict[str, dict] = {}
    if groups:
        for r in await conn.fetch(
                f"select coalesce(m.group_id, 'r' || m.id::text) as g, m.id, m.kind, m.source_kind, "
                f"left(coalesce(m.source_chat_title, m.text), 60) as name, {_item_live()} as live from orion_memory m "
                f"where m.user_id = $1 and (m.group_id = any($2::text[]) or (m.group_id is null and m.id = any($3::bigint[]))) "
                f"and not m.whole order by m.item_order, m.id", user_id, *_keys(groups)):
            kept = items.get(r["g"])
            better = kept is None or (not kept["live"] and r["live"]) or \
                (r["live"] == kept["live"] and kept["kind"] not in ("image", "video") and r["kind"] in ("image", "video"))
            if better:
                items[r["g"]] = {"group_id": r["g"], "row_id": r["id"], "kind": r["kind"], "name": r["name"], "live": r["live"]}
    out = []
    for r in rows:
        mine = [link for link in links if link["from_id"] == r["id"]]
        people = [me._entity_out(entities[int(link["to_id"])]) for link in mine
                  if link["to_type"] == "entity" and link["role"] == PARTICIPANT and int(link["to_id"]) in entities]
        place = next((me._entity_out(entities[int(link["to_id"])]) for link in mine
                      if link["to_type"] == "entity" and link["role"] == PLACE and int(link["to_id"]) in entities), None)
        photos = [{"group_id": link["to_id"], **{k: v for k, v in items[link["to_id"]].items() if k not in ("group_id", "live")},
                   "removed": not items[link["to_id"]]["live"]} if link["to_id"] in items
                  else {"group_id": link["to_id"], "row_id": None, "kind": None, "name": None, "removed": True}
                  for link in mine if link["to_type"] == "item"]
        out.append({**_short(r), "body": r["body"], "mood": r["mood"], "recorded_at": r["recorded_at"].isoformat(),
                    "origin": r["origin"], "group_id": r["group_id"], "current": r["superseded_by"] is None,
                    "participants": people, "place": place, "items": photos})
    return out


async def list_events(user_id: str, settings, start: str | None = None, end: str | None = None, entity_id: int | None = None,
                      limit: int = 50, offset: int = 0) -> dict:
    """The timeline: the user's live events, newest day first, {"events", "more", "years": [{year, count}] (the first
    page only; counted over the same filter)}. `start` / `end`: a range (parse_range); `entity_id`: events the entity took
    part in or was the place of. ValueError for a bad range."""
    require(settings)
    span = parse_range(start, end)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    params: list = [user_id]
    where = ["e.user_id = $1", "e.superseded_by is null", _live("e")]
    if span:
        if span[1] is not None:
            params.append(span[1])
            where.append(f"e.occurred_on <= ${len(params)}")
        if span[0] is not None:
            params.append(span[0])
            where.append(f"coalesce(e.occurred_end, e.occurred_on) >= ${len(params)}")
    if entity_id is not None:
        params.append(str(int(entity_id)))
        where.append(f"exists (select 1 from orion_mem_link l where l.user_id = e.user_id and l.from_type = 'event' and "
                     f"l.from_id = e.id and l.to_type = 'entity' and l.to_id = ${len(params)})")
    condition = " and ".join(where)
    async with me._db() as conn:
        rows = await conn.fetch(f"select {_EVENT} from orion_mem_event e where {condition} order by e.occurred_on desc, "
                                f"e.id desc limit {limit + 1} offset {offset}", *params)
        events = await _decorate(conn, user_id, rows[:limit])
        years = []
        if offset == 0:
            years = [{"year": int(r["y"]), "count": int(r["n"])} for r in await conn.fetch(
                f"select extract(year from e.occurred_on)::int as y, count(*) as n from orion_mem_event e where {condition} "
                f"group by 1 order by 1 desc", *params)]
    return {"events": events, "more": len(rows) > limit, "years": years}


async def get_event(user_id: str, settings, event_id: int) -> dict:
    """One event (any version's id) with its links, sources and how many past versions it has. LookupError when it is
    not this user's or is removed."""
    require(settings)
    async with me._db() as conn:
        row = await conn.fetchrow(f"select {_EVENT} from orion_mem_event e where e.user_id = $1 and e.id = $2", user_id,
                                  event_id)
        if row is None:
            raise LookupError(event_id)
        head = row if row["superseded_by"] is None else await conn.fetchrow(
            f"select {_EVENT} from orion_mem_event e where e.user_id = $1 and e.lineage = $2 and e.superseded_by is null",
            user_id, row["lineage"])
        if head is None or not await conn.fetchval(f"select {_live('e')} from orion_mem_event e where e.id = $1", head["id"]):
            raise LookupError(event_id)
        event = (await _decorate(conn, user_id, [head]))[0]
        past = await conn.fetch(f"select {_EVENT} from orion_mem_event e where e.user_id = $1 and e.lineage = $2 and "
                                "e.id <> $3 order by e.id", user_id, head["lineage"], head["id"])
        sources = await conn.fetch(
            "select chat_id, first_message, last_message, item_id, by_user, created_at from orion_mem_source where "
            "user_id = $1 and target_type = 'event' and target_id = any($2::bigint[]) order by id", user_id,
            [head["id"], *[p["id"] for p in past]])
    return {**event, "past_versions": [_short(p) for p in past],
            "sources": [{"chat_id": s["chat_id"], "first_message": s["first_message"], "last_message": s["last_message"],
                         "item_id": s["item_id"], "by_user": s["by_user"], "at": s["created_at"].isoformat()}
                        for s in sources]}


# --- Writing ----------------------------------------------------------------------------------------------------------

def check_text(title: str, body: str | None) -> tuple[str, str | None]:
    title = " ".join((title or "").split())
    body = (body or "").strip() or None
    if not title and body:
        title = " ".join(body.split())[:MAX_TITLE].rstrip()
    if not title:
        raise ValueError("An event needs a title (what happened).")
    if len(title) > MAX_TITLE:
        raise ValueError(f"An event's title is short: at most {MAX_TITLE} characters (put the rest in the text).")
    if body and len(body) > MAX_BODY:
        raise ValueError(f"An event's text is at most {MAX_BODY} characters: keep a longer account as a note.")
    if body and " ".join(body.split()) == title:
        body = None
    return title, body


def embedded_text(title: str, body: str | None, when: dict, people: list[str], place: str | None) -> str:
    said = f" ({when['said_as']})" if when.get("said_as") else ""
    text = f"{range_text(when['on'], when['end'], when['precision'])}{said}: {title}."
    if body:
        text += f" {body}"
    if people:
        text += f" Katılanlar: {', '.join(people)}."
    if place:
        text += f" Yer: {place}."
    return text


async def _store_vector(conn, user_id: str, text: str, title: str, space: str, vector: str, source: dict) -> str:
    """The event's memory (one row, the Yaşadıklarım collection); its group id."""
    group, item = memory.new_group(), memory.new_group()
    await conn.fetchval(
        memory.ROW_SQL, user_id, text, space, vector, EVENT, title, "text", None, None, None, group,
        source.get("chat_id"), None, item, False, None, None, None, None, memory.item_order(), None,
        memory.fold(memory.match_text(text, title, None, EVENT)), None, True)
    if await conn.fetchval("select 1 from orion_mem_collection where user_id = $1 and id = $2", user_id, COLLECTION):
        await conn.execute("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) "
                           "values ($1, $2, $3, true) on conflict do nothing", user_id, COLLECTION, group)
    return group


async def groups_of_rows(conn, user_id: str, row_ids: list[int]) -> list[str]:
    """The memory group keys of memory rows (ids as search results show them) that are this user's and not removed;
    LookupError naming the ids that are not."""
    ids = list(dict.fromkeys(int(i) for i in row_ids))
    found = {r["id"]: r["g"] for r in await conn.fetch(
        f"select m.id, coalesce(m.group_id, 'r' || m.id::text) as g from orion_memory m where m.user_id = $1 and "
        f"m.id = any($2::bigint[]) and {_item_live()}", user_id, ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise LookupError(f"There is no memory with id {', '.join(map(str, missing))}.")
    return list(dict.fromkeys(found[i] for i in ids))


async def groups_exist(conn, user_id: str, groups: list[str]) -> list[str]:
    """`groups` that are memories of this user and not removed (order kept); LookupError for the others."""
    keys = list(dict.fromkeys(str(g)[:64] for g in groups))
    have = {r["g"] for r in await conn.fetch(
        f"select distinct coalesce(m.group_id, 'r' || m.id::text) as g from orion_memory m where m.user_id = $1 and "
        f"(m.group_id = any($2::text[]) or (m.group_id is null and m.id = any($3::bigint[]))) and "
        f"{_item_live()}", user_id, *_keys(keys))}
    missing = [k for k in keys if k not in have]
    if missing:
        raise LookupError(", ".join(missing))
    return keys


async def _resolve_people(conn, user_id: str, people: list[str], place: str | None) -> tuple[list[dict], dict | None]:
    """The entities named as participants / place: an exact name or relation, a new one when unknown (a person; a place
    for `place`); Ambiguous when a name fits several. One creation at a time per user."""
    await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", me.ENTITY_LOCK, user_id)
    found = []
    for name in people:
        entity, _ = await me.resolve_entity(conn, user_id, name, "person")
        if entity["id"] not in [e["id"] for e in found]:
            found.append(entity)
    where = None
    if place and place.strip():
        where, _ = await me.resolve_entity(conn, user_id, place, "place")
    return found, where


async def _link(conn, user_id: str, event_id: int, to_type: str, to_id: str, role: str) -> None:
    await conn.execute("insert into orion_mem_link (user_id, from_type, from_id, to_type, to_id, role) values "
                       "($1, 'event', $2, $3, $4, $5) on conflict do nothing", user_id, event_id, to_type, str(to_id), role)


async def _vector_for(settings, text: str):
    target, space, local = await memory.embedding_target(settings)
    vector = (await memory._embed(text, target, "document", local))[0]
    return space, memory._vector(vector)


def _check_source(source: dict | None) -> dict:
    return me._check_source(source)


async def add_event(user_id: str, settings, title: str, when: dict, *, body: str | None = None, mood: str | None = None,
                    participants: list[str] | None = None, place: str | None = None, items: list[str] | None = None,
                    origin: str, source: dict, album: str | None = None) -> dict:
    """Write one event, or find that it is already there. `when`: resolve_date's answer; `participants` / `place`: names
    (resolve_entity); `items`: memory group keys of photos etc. to link (this user's, not removed); `album`: a
    collection id the event's memory also joins. {"event": {...}} or {"same_as": id} (an event with the same title and day
    exists: the new source is added to it and the new links with it). ValueError: no source, an empty title, a bad date;
    LookupError: an item that is not this user's; Ambiguous. Nothing is written when embedding fails (MemoryUnavailable)."""
    require(settings)
    source = _check_source(source)
    title, body = check_text(title, body)
    mood = " ".join((mood or "").split())[:40] or None
    names = [n for n in (" ".join((n or "").split())[:me.MAX_NAME] for n in participants or []) if n][:MAX_PEOPLE]
    async with me._db() as conn:
        await memory_collections.ensure_user(conn, user_id)
        same = await conn.fetchrow(
            f"select e.id, e.title from orion_mem_event e where e.user_id = $1 and e.superseded_by is null and "
            f"e.occurred_on = $2 and e.precision = $3 and {_live('e')}", user_id, when["on"], when["precision"])
        async with conn.transaction():
            people, where = await _resolve_people(conn, user_id, names, place)
            groups = await groups_exist(conn, user_id, items or []) if items else []
        if same and memory.fold(same["title"]) == memory.fold(title):
            await memory_collections.add_source(conn, user_id, "event", same["id"], chat_id=source.get("chat_id"),
                                                first_message=source.get("first_message"),
                                                last_message=source.get("last_message"), item_id=source.get("item_id"),
                                                by_user=bool(source.get("by_user")))
            async with conn.transaction():
                for entity in people:
                    await _link(conn, user_id, same["id"], "entity", entity["id"], PARTICIPANT)
                if where:
                    await _link(conn, user_id, same["id"], "entity", where["id"], PLACE)
                for group in groups:
                    await _link(conn, user_id, same["id"], "item", group, PHOTO)
            return {"same_as": same["id"]}
        text = embedded_text(title, body, when, [me.label_of(e) for e in people], where["name"] if where else None)
        space, vector = await _vector_for(settings, text)
        async with conn.transaction():
            group = await _store_vector(conn, user_id, text, title, space, vector, source)
            event_id = await conn.fetchval(
                "insert into orion_mem_event (user_id, title, body, occurred_on, occurred_end, precision, said_as, mood, "
                "origin, group_id) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning id", user_id, title, body,
                when["on"], when["end"], when["precision"], when["said_as"], mood, origin, group)
            await conn.execute("update orion_mem_event set lineage = id where id = $1", event_id)
            await memory_collections.add_source(
                conn, user_id, "event", event_id, chat_id=source.get("chat_id"), first_message=source.get("first_message"),
                last_message=source.get("last_message"), item_id=source.get("item_id"), by_user=bool(source.get("by_user")))
            for entity in people:
                await _link(conn, user_id, event_id, "entity", entity["id"], PARTICIPANT)
            if where:
                await _link(conn, user_id, event_id, "entity", where["id"], PLACE)
            for item in groups:
                await _link(conn, user_id, event_id, "item", item, PHOTO)
            if album:
                await conn.execute("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) values "
                                   "($1, $2, $3, false) on conflict do nothing", user_id, album, group)
            row = await conn.fetchrow(f"select {_EVENT} from orion_mem_event e where e.id = $1", event_id)
        event = (await _decorate(conn, user_id, [row]))[0]
    return {"event": event}


async def replace_event(user_id: str, settings, event_id: int, *, title: str | None = None, body: str | None = None,
                        when: dict | None = None, mood: str | None = None, participants: list[str] | None = None,
                        place: str | None = None, origin: str = "user", source: dict | None = None) -> dict:
    """The user corrects an event: a new version (the old one closed, its vector row removed), the links moved to it.
    Left as they were: what is None (`participants` / `place`: names; an empty list / "" clears them). LookupError when not
    this user's or removed; ValueError for a refused value. {"event": {...}, "replaced": old id}."""
    require(settings)
    source = _check_source(source or {"by_user": True})
    async with me._db() as conn:
        old = await conn.fetchrow(f"select {_EVENT} from orion_mem_event e where e.user_id = $1 and e.id = $2", user_id,
                                  event_id)
        if old is None or old["superseded_by"] is not None or not await conn.fetchval(
                f"select {_live('e')} from orion_mem_event e where e.id = $1", event_id):
            raise LookupError(event_id)
        new_title, new_body = check_text(title if title is not None else old["title"],
                                         body if body is not None else old["body"])
        when = when or {"on": old["occurred_on"], "end": old["occurred_end"], "precision": old["precision"],
                        "said_as": old["said_as"]}
        async with conn.transaction():
            names = [n for n in (" ".join((n or "").split())[:me.MAX_NAME] for n in participants or []) if n][:MAX_PEOPLE]
            named, named_place = await _resolve_people(conn, user_id, names, place)
            current = await conn.fetch(
                "select to_id, role from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2 and "
                "to_type = 'entity'", user_id, event_id)

            async def entities(role):
                ids = sorted(int(link["to_id"]) for link in current if link["role"] == role)
                return [dict(r) for r in await conn.fetch(
                    f"select {me._ENTITY} from orion_mem_entity where user_id = $1 and id = any($2::bigint[]) order by id",
                    user_id, ids)] if ids else []
            people = named if participants is not None else await entities(PARTICIPANT)
            where = named_place if place is not None else next(iter(await entities(PLACE)), None)
        text = embedded_text(new_title, new_body, when, [me.label_of(e) for e in people], where["name"] if where else None)
        space, vector = await _vector_for(settings, text)
        async with conn.transaction():
            old = await conn.fetchrow(f"select {_EVENT} from orion_mem_event e where e.user_id = $1 and e.id = $2 for update "
                                      "of e", user_id, event_id)
            if old["superseded_by"] is not None:
                raise ValueError("This event was already replaced by a newer version; change the current one.")
            group = await _store_vector(conn, user_id, text, new_title, space, vector, source)
            new_id = await conn.fetchval(
                "insert into orion_mem_event (user_id, lineage, title, body, occurred_on, occurred_end, precision, said_as, "
                "mood, origin, group_id) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) returning id", user_id,
                old["lineage"], new_title, new_body, when["on"], when["end"], when["precision"], when["said_as"],
                (" ".join((mood if mood is not None else old["mood"] or "").split())[:40] or None), origin, group)
            await conn.execute("update orion_mem_event set expired_at = now(), superseded_by = $3 where user_id = $1 and id = $2",
                               user_id, event_id, new_id)
            # The new version takes the item links and the places in albums; the old vector row and membership go.
            await conn.execute("delete from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2 and "
                               "to_type = 'entity'", user_id, new_id)
            for entity in people:
                await _link(conn, user_id, new_id, "entity", entity["id"], PARTICIPANT)
            if where:
                await _link(conn, user_id, new_id, "entity", where["id"], PLACE)
            await conn.execute("update orion_mem_link set from_id = $3 where user_id = $1 and from_type = 'event' and "
                               "from_id = $2 and to_type = 'item'", user_id, event_id, new_id)
            await conn.execute("delete from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2",
                               user_id, event_id)
            await conn.execute(
                "insert into orion_mem_collection_item (user_id, collection_id, group_id, main) select user_id, collection_id, "
                "$3, false from orion_mem_collection_item where user_id = $1 and group_id = $2 and not main "
                "on conflict do nothing", user_id, old["group_id"], group)
            await memory.delete_rows(conn, user_id, "group_id = $2", old["group_id"])
            await conn.execute("delete from orion_mem_collection_item where user_id = $1 and group_id = $2", user_id,
                               old["group_id"])
            await memory_collections.add_source(conn, user_id, "event", new_id, by_user=bool(source.get("by_user")),
                                                chat_id=source.get("chat_id"), first_message=source.get("first_message"),
                                                last_message=source.get("last_message"), item_id=source.get("item_id"))
            row = await conn.fetchrow(f"select {_EVENT} from orion_mem_event e where e.id = $1", new_id)
        event = (await _decorate(conn, user_id, [row]))[0]
    return {"event": event, "replaced": event_id}


async def remove_event(user_id: str, settings, event_id: int) -> dict:
    """The user removes an event: it goes to the trash with its memory ({"trash_id"}: restore through the trash).
    LookupError when not this user's."""
    require(settings)
    from orion.kernel import memory_trash
    async with me._db() as conn:
        row = await conn.fetchrow("select lineage from orion_mem_event where user_id = $1 and id = $2", user_id, event_id)
        if row is None:
            raise LookupError(event_id)
        head = await conn.fetchrow("select id, group_id from orion_mem_event e where e.user_id = $1 and e.lineage = $2 and "
                                   f"e.superseded_by is null and {_live('e')}", user_id, row["lineage"])
        if head is None:
            raise LookupError(event_id)
    removed = await memory_trash.trash_groups(user_id, [head["group_id"]])
    return {"trash_id": removed["trash_id"]}


async def set_items(user_id: str, settings, event_id: int, add: list[str] | None = None, remove: list[str] | None = None,
                    role: str = PHOTO) -> dict:
    """Link memories (photos, videos...: group keys) to the event / unlink them. Adding needs the memory to be this user's
    and not removed (LookupError otherwise); the event must be the user's live one. {"linked", "unlinked"}."""
    require(settings)
    async with me._db() as conn:
        head = await conn.fetchrow(f"select e.id from orion_mem_event e where e.user_id = $1 and e.id = $2 and "
                                   f"e.superseded_by is null and {_live('e')}", user_id, event_id)
        if head is None:
            raise LookupError(event_id)
        async with conn.transaction():
            groups = await groups_exist(conn, user_id, add or []) if add else []
            for group in groups:
                await _link(conn, user_id, event_id, "item", group, role)
            gone = [str(g)[:64] for g in remove or []]
            removed = await conn.execute(
                "delete from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2 and "
                "to_type = 'item' and to_id = any($3::text[])", user_id, event_id, gone) if gone else "DELETE 0"
    return {"linked": len(groups), "unlinked": int(removed.split()[-1])}


# --- What search and memory_context tell about an event ----------------------------------------------------------------

async def events_on(conn, user_id: str, groups: list[str]) -> dict[str, dict]:
    """{memory group: {"id", "date", "end", "precision", "title", "with", "place"}} for the memories among `groups` that
    are current events: one indexed lookup on the caller's connection (a search card says what its event is)."""
    if not groups:
        return {}
    rows = await conn.fetch(
        "select e.id, e.group_id, e.title, e.occurred_on, e.occurred_end, e.precision, "
        "coalesce(array_agg(en.name order by l.created_at, en.id) filter (where l.role = 'katılan'), '{}') as people, "
        "max(en.name) filter (where l.role = 'yer') as place, count(*) filter (where l.to_type = 'item') as photos "
        "from orion_mem_event e left join orion_mem_link l on l.user_id = e.user_id and l.from_type = 'event' and "
        "l.from_id = e.id left join orion_mem_entity en on en.user_id = l.user_id and l.to_type = 'entity' and "
        "en.id = case when l.to_type = 'entity' then l.to_id::bigint end where e.user_id = $1 and "
        "e.group_id = any($2::text[]) and e.superseded_by is null group by e.id", user_id, groups)
    return {r["group_id"]: {"id": r["id"], "date": r["occurred_on"].isoformat(),
                            "end": r["occurred_end"].isoformat() if r["occurred_end"] else None,
                            "precision": r["precision"], "title": r["title"], "with": list(r["people"]),
                            "place": r["place"], "photos": int(r["photos"])} for r in rows}


async def event_of_group(user_id: str, group: str) -> int | None:
    async with me._db() as conn:
        return await conn.fetchval("select id from orion_mem_event where user_id = $1 and group_id = $2", user_id, group)


async def events_of_item(user_id: str, group: str) -> list[dict]:
    """The live events a memory (a photo...) is linked to: for memory_context."""
    async with me._db() as conn:
        rows = await conn.fetch(
            f"select {_EVENT} from orion_mem_event e join orion_mem_link l on l.user_id = e.user_id and l.from_type = 'event' "
            f"and l.from_id = e.id and l.to_type = 'item' and l.to_id = $2 where e.user_id = $1 and e.superseded_by is null "
            f"and {_live('e')} order by e.occurred_on desc, e.id desc limit 20", user_id, group)
    return [_short(r) for r in rows]


async def event_context(user_id: str, settings, event_id: int) -> dict | None:
    """For memory_context: an event's own fields, links and sources (None when it is not live)."""
    try:
        got = await get_event(user_id, settings, event_id)
    except (LookupError, ValueError):  # Not the user's / removed / the collections are off.
        return None
    return {"id": got["id"], "title": got["title"], "text": got["body"], "date": got["date"], "end": got["end"],
            "precision": got["precision"], "said_as": got["said_as"], "with": [p["name"] for p in got["participants"]],
            "place": got["place"]["name"] if got["place"] else None,
            "photos": [{"id": i["row_id"], "kind": i["kind"]} for i in got["items"] if not i["removed"]],
            "sources": got["sources"]}


# --- Yearly recurrences (facts with `recurring`) ----------------------------------------------------------------------

def next_occurrence(recurring: str, today: date) -> date | None:
    """The next day (today included) a 'yearly:MM-DD' recurrence falls on; 29 February is 28 February in a year without
    one. None for a value that is not a yearly recurrence."""
    found = re.fullmatch(r"yearly:(\d{2})-(\d{2})", recurring or "")
    if not found:
        return None
    month, day = int(found.group(1)), int(found.group(2))
    for year in (today.year, today.year + 1):
        try:
            when = date(year, month, day)
        except ValueError:
            when = date(year, month, 28)
        if when >= today:
            return when
    return None


async def upcoming(user_id: str, settings, days: int = 30, today: date | None = None) -> list[dict]:
    """The recurring facts (birthdays, anniversaries) that fall in the next `days` days, soonest first:
    [{"fact_id", "entity_id", "about", "statement", "recurring", "next", "in_days"}]. Locked facts never appear."""
    require(settings)
    today = today or today_of()
    days = max(0, min(int(days), 366))
    async with me._db() as conn:
        rows = await conn.fetch(
            f"select f.id, f.entity_id, f.statement, f.recurring, e.name, e.relation, e.kind from orion_mem_fact f join "
            f"orion_mem_entity e on e.id = f.entity_id and e.user_id = f.user_id where f.user_id = $1 and "
            f"f.recurring is not null and f.superseded_by is null and not f.sensitive and {me._live('f')}", user_id)
    out = []
    for r in rows:
        when = next_occurrence(r["recurring"], today)
        if when and (when - today).days <= days:
            out.append({"fact_id": r["id"], "entity_id": r["entity_id"], "about": me.label_of(dict(r)), "statement": r["statement"],
                        "recurring": r["recurring"], "next": when.isoformat(), "in_days": (when - today).days})
    out.sort(key=lambda u: (u["in_days"], u["fact_id"]))
    return out


# --- After a deletion for good ----------------------------------------------------------------------------------------

async def after_purge(user_id: str) -> int:
    """Rows were deleted for good: events whose memory has no row left go with their past versions, sources and links; links
    to items with no row left and album memberships of memories that are gone go too. How many events went."""
    async with me._db() as conn:
        async with conn.transaction():
            gone = [r["lineage"] for r in await conn.fetch(
                "select e.lineage from orion_mem_event e where e.user_id = $1 and e.superseded_by is null and not exists "
                "(select 1 from orion_memory m where m.user_id = e.user_id and m.group_id = e.group_id)", user_id)]
            if gone:
                ids = [r["id"] for r in await conn.fetch(
                    "delete from orion_mem_event where user_id = $1 and lineage = any($2::bigint[]) returning id", user_id,
                    gone)]
                await conn.execute("delete from orion_mem_source where user_id = $1 and target_type = 'event' and "
                                   "target_id = any($2::bigint[])", user_id, ids)
                await conn.execute("delete from orion_mem_link where user_id = $1 and from_type = 'event' and "
                                   "from_id = any($2::bigint[])", user_id, ids)
            await conn.execute(
                "delete from orion_mem_link l where l.user_id = $1 and l.to_type = 'item' and not exists (select 1 from "
                "orion_memory m where m.user_id = l.user_id and m.group_id = l.to_id) and not exists (select 1 from "
                "orion_memory m where m.user_id = l.user_id and m.group_id is null and m.id = case when l.to_id ~ '^r[0-9]+$' "
                "then substr(l.to_id, 2)::bigint end)", user_id)
            await conn.execute(
                "delete from orion_mem_collection_item i where i.user_id = $1 and not i.main and not exists (select 1 from "
                "orion_memory m where m.user_id = i.user_id and m.group_id = i.group_id) and not exists (select 1 from "
                "orion_memory m where m.user_id = i.user_id and m.group_id is null and m.id = case when i.group_id ~ "
                "'^r[0-9]+$' then substr(i.group_id, 2)::bigint end)", user_id)
            return len(gone)
