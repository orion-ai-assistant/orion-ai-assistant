"""Entities and facts (docs/hafiza-tasarimi.md §4, §5, §8, §11.1, §16 step 4): the "Ben" collection. An entity is a person,
place, organisation, pet, thing or the user (`self`); a fact is one short, self-contained sentence about an entity
("Annemin doğum günü 17 Kasım."). Behind the per-user `memory_v2` setting; with it off nothing here runs.

Versions, never overwrites. A fact that changes is a new row (same `lineage`); the old row is closed: `valid_to` (when it
stopped being true in the world, from the new fact's `valid_from`), `expired_at` (when Orion stopped counting it) and
`superseded_by`. "Now" = the head of a lineage (`superseded_by is null`). Past versions are kept as rows of
`orion_mem_fact` (text, dates, sources) and read on the page / by `memory_context`; they have no vector row: only the
current version is searched by meaning (the design's `current` flag would rewrite a vector row and its index entries; deleting
the closed version's row is cheap and keeps every search path untouched). Searching the past by words/dates is step 8.

One source or no fact. Every fact is linked to where it came from (`orion_mem_source`: a chat, an item or "the user wrote
it", memory_collections.add_source); `add_fact` refuses without one.

How a fact is searched. A current non-sensitive fact is a memory like any other: one `orion_memory` row (`kind 'text'`,
`source_kind 'fact'`, its own group, in the Ben collection), embedded as "<entity label>: <statement>" with the entity's name
as its title, so the page, the map, the counters, the trash, the model move and `search_memory` (`in: ["me"]`) all work on
it with no new code path. `orion_mem_fact.group_id` points at that memory; a fact is live while that memory has a live row
(a removal through the page's own delete, or the trash, therefore removes the fact; restoring brings it back; deleting it
for good removes the fact rows too: after_purge).

Sensitive facts (§11.1: health, sex life, religion, politics, ethnicity, union, biometric/genetic, legal, finance, id
numbers) are LOCKED: they have no vector row at all, so no search, map, count, profile, extraction input or trash card can
show them. The page lists them blurred (`locked`), reading the text is an explicit request (`reveal`), the model gets them
only from `search_memory(sensitive=true)` (word match in this table), and a model never writes one: it opens a card.

Merging two entities moves the facts and the names (kept as aliases of the survivor) in one transaction and records what
moved (`orion_mem_entity_merge`), so it can be undone. The model never merges: duplicates are found by exact folded names
and offered as one batch card (orion_mem_proposal).

Every table has user_id and every read filters by it."""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date

from orion.kernel import memory, memory_collections

logger = logging.getLogger(__name__)

FACT = "fact"  # source_kind of a fact's vector row.
COLLECTION = "ben"
ENTITY_LOCK = 51730206  # With hashtext(user id): one entity creation at a time per user.
MAX_STATEMENT = 500
MAX_NAME = 120
KINDS = ("person", "place", "org", "pet", "thing", "self")
CATEGORIES = ("kimlik", "ilişki", "tercih", "görüş", "iş", "yer", "tarih", "diğer")
SENSITIVE = ("sağlık", "cinsel_yaşam", "din_inanç", "siyasi_görüş", "etnik_köken", "sendika_dernek", "biyometrik_genetik",
             "adli", "finans", "kimlik_no")
# The tool speaks English; the stored vocabulary is the design's.
CATEGORY_NAMES = {
    "identity": "kimlik", "relationship": "ilişki", "preference": "tercih", "opinion": "görüş", "work": "iş",
    "place": "yer", "date": "tarih", "other": "diğer",
    "health": "sağlık", "sexual_life": "cinsel_yaşam", "religion": "din_inanç", "political": "siyasi_görüş",
    "ethnicity": "etnik_köken", "union": "sendika_dernek", "biometric_genetic": "biyometrik_genetik", "legal": "adli",
    "finance": "finans", "id_number": "kimlik_no"}
SELF_NAMES = {"", "me", "ben", "self", "myself", "i", "user", "the user", "kendim", "ben kendim"}
ID_DIGITS = 8  # kimlik_no: a number this long (TC kimlik, passport, card) is never stored, only that one exists.


class Ambiguous(ValueError):
    """Two or more entities fit a name: `candidates` lists them so the caller can say which."""

    def __init__(self, name: str, candidates: list[dict]):
        super().__init__(f"More than one entity is called {name!r}: " +
                         ", ".join(f"#{c['id']} {c['name']}" + (f" ({c['relation']})" if c.get("relation") else "")
                                   for c in candidates) + ". Pass about=\"#<id>\" to choose.")
        self.candidates = candidates


@asynccontextmanager
async def _db(conn=None):
    """A connection of the memory (closed after), or the caller's own `conn` (left open): several reads of one search share
    one connection."""
    if conn is not None:
        yield conn
        return
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        yield conn
    finally:
        await conn.close()


def enabled(settings) -> bool:
    return memory_collections.enabled(settings)


def require(settings) -> None:
    if not enabled(settings):
        raise ValueError("Hafıza koleksiyonları kapalı: Ayarlar'da açın (memory_v2).")


def category_of(value: str | None) -> str:
    """The stored category for a name in either language; 'diğer' when empty; ValueError for an unknown one."""
    key = memory.fold(" ".join((value or "").split())).replace(" ", "_")
    if not key:
        return "diğer"
    for name in CATEGORIES + SENSITIVE:
        if memory.fold(name) == key:
            return name
    if key in CATEGORY_NAMES:
        return CATEGORY_NAMES[key]
    raise ValueError(f"Unknown category {value!r}. Use one of: " + ", ".join(CATEGORY_NAMES) + ".")


def is_sensitive(category: str) -> bool:
    return category in SENSITIVE


def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _day(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"A date must be ISO (YYYY-MM-DD), got {value!r}.") from exc


def keys_of(name: str, relation: str | None, aliases: list[str]) -> list[str]:
    return sorted({k for k in (memory.fold(_clean(v, MAX_NAME)) for v in [name, relation or "", *aliases]) if k})


def label_of(entity: dict) -> str:
    """How the entity is named inside a fact's text: "Annem (Ayşe)", or just the name."""
    name, relation = entity["name"], entity.get("relation")
    if entity["kind"] == "self":
        return name
    return f"{relation[:1].upper()}{relation[1:]} ({name})" if relation and memory.fold(relation) != memory.fold(name) \
        else name


# --- Entities ---------------------------------------------------------------------------------------------------------

_ENTITY = "id, kind, name, relation, aliases, merged_into, created_at"


def _entity_out(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "name": row["name"], "relation": row["relation"],
            "aliases": list(row["aliases"] or [])}


async def _self_entity(conn, user_id: str) -> dict:
    row = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and kind = 'self' "
                              "and merged_into is null and deleted_at is null order by id limit 1", user_id)
    if row is None:
        row = await conn.fetchrow(
            f"insert into orion_mem_entity (user_id, kind, name, keys) values ($1, 'self', 'Ben', $2) returning {_ENTITY}",
            user_id, keys_of("Ben", None, ["me", "myself"]))
    return dict(row)


async def find_entities(conn, user_id: str, about: str) -> list[dict]:
    """The user's live entities whose name, relation or an alias is exactly `about` (folded); `#<id>` names one by id."""
    about = _clean(about, MAX_NAME)
    if about.startswith("#") and about[1:].isdigit():
        rows = await conn.fetch(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2 and "
                                "merged_into is null and deleted_at is null", user_id, int(about[1:]))
    else:
        rows = await conn.fetch(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and $2 = any(keys) and "
                                "merged_into is null and deleted_at is null order by id", user_id, memory.fold(about))
    return [dict(r) for r in rows]


async def resolve_entity(conn, user_id: str, about: str | None, kind: str = "person", relation: str | None = None,
                         create: bool = True) -> tuple[dict | None, bool]:
    """The entity `about` names: the user (empty, "me"...), one exact match, or a new one (`create`). Ambiguous when
    several fit; (None, False) when none fits and `create` is off. (entity, created)."""
    if memory.fold(_clean(about or "", MAX_NAME)) in SELF_NAMES:
        return await _self_entity(conn, user_id), False
    found = await find_entities(conn, user_id, about)
    if len(found) > 1:
        raise Ambiguous(about, found)
    if found:
        return found[0], False
    if not create:
        return None, False
    if (about or "").strip().startswith("#"):
        raise ValueError(f"There is no entity {about}.")
    if kind not in KINDS or kind == "self":
        raise ValueError(f"An entity kind must be one of {[k for k in KINDS if k != 'self']}.")
    name = _clean(about, MAX_NAME)
    relation = _clean(relation or "", MAX_NAME) or None
    row = await conn.fetchrow(
        f"insert into orion_mem_entity (user_id, kind, name, relation, keys) values ($1, $2, $3, $4, $5) returning {_ENTITY}",
        user_id, kind, name, relation, keys_of(name, relation, []))
    return dict(row), True


async def create_entity(user_id: str, settings, name: str, kind: str = "person", relation: str | None = None,
                        aliases: list[str] | None = None) -> dict:
    """A new entity the user made on the page (never merged into one by name: two Ayşe may be two people)."""
    require(settings)
    name = _clean(name, MAX_NAME)
    if not name:
        raise ValueError("Bir ad gerekli.")
    if kind not in KINDS or kind == "self":
        raise ValueError(f"kind must be one of {[k for k in KINDS if k != 'self']}")
    relation = _clean(relation or "", MAX_NAME) or None
    names = [a for a in (_clean(a, MAX_NAME) for a in aliases or []) if a][:20]
    async with _db() as conn:
        row = await conn.fetchrow(
            f"insert into orion_mem_entity (user_id, kind, name, relation, aliases, keys) values ($1, $2, $3, $4, $5, $6) "
            f"returning {_ENTITY}", user_id, kind, name, relation, names, keys_of(name, relation, names))
    return _entity_out(row)


async def update_entity(user_id: str, settings, entity_id: int, name: str | None = None, relation: str | None = None,
                        aliases: list[str] | None = None) -> dict:
    """The user edits an entity's name, relation or aliases (kept as given; the facts' texts keep the label they were embedded with)."""
    require(settings)
    async with _db() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2 and "
                                      "merged_into is null and deleted_at is null for update", user_id, entity_id)
            if row is None:
                raise LookupError(entity_id)
            new_name = _clean(name, MAX_NAME) if name is not None else row["name"]
            if not new_name:
                raise ValueError("Bir ad gerekli.")
            new_relation = (_clean(relation, MAX_NAME) or None) if relation is not None else row["relation"]
            new_aliases = [a for a in (_clean(a, MAX_NAME) for a in aliases) if a][:20] if aliases is not None \
                else list(row["aliases"] or [])
            row = await conn.fetchrow(
                f"update orion_mem_entity set name = $3, relation = $4, aliases = $5, keys = $6 where user_id = $1 and id = $2 "
                f"returning {_ENTITY}", user_id, entity_id, new_name, new_relation, new_aliases,
                keys_of(new_name, new_relation, new_aliases))
    return _entity_out(row)


def _live(alias: str) -> str:
    """A fact (alias) that is not removed: its memory has a live row; a locked fact (no row) has no `deleted_at`."""
    return (f"({alias}.deleted_at is null and ({alias}.group_id is null or exists (select 1 from orion_memory m "
            f"where m.user_id = {alias}.user_id and m.group_id = {alias}.group_id and not m.whole and "
            f"not exists (select 1 from orion_mem_trash t where t.memory_id = m.id))))")


async def list_entities(user_id: str, settings, q: str = "") -> list[dict]:
    """The user's entities (the user first, then by name) with how many current facts (`facts`), how many of them are
    locked (`locked`) and the pending proposals naming them; `q`: names containing it (folded)."""
    require(settings)
    async with _db() as conn:
        rows = await conn.fetch(f"select {_ENTITY}, keys from orion_mem_entity where user_id = $1 and merged_into is null "
                                "and deleted_at is null", user_id)
        counts = {r["entity_id"]: r for r in await conn.fetch(
            f"select f.entity_id, count(*) as n, count(*) filter (where f.sensitive) as locked from orion_mem_fact f "
            f"where f.user_id = $1 and f.superseded_by is null and {_live('f')} group by 1", user_id)}
    wanted = memory.fold(_clean(q, MAX_NAME))
    out = []
    for r in rows:
        if wanted and not any(wanted in k for k in r["keys"]):
            continue
        c = counts.get(r["id"])
        out.append({**_entity_out(r), "facts": int(c["n"]) if c else 0, "locked": int(c["locked"]) if c else 0})
    out.sort(key=lambda e: (e["kind"] != "self", memory.fold(e["name"]), e["id"]))
    return out


# --- Facts ------------------------------------------------------------------------------------------------------------

_FACT = ("f.id, f.entity_id, f.lineage, f.category, f.statement, f.valid_from, f.valid_to, f.recorded_at, f.expired_at, "
         "f.superseded_by, f.sensitive, f.origin, f.group_id, f.deleted_at, f.recurring")


def _fact_out(r, reveal: bool = False) -> dict:
    locked = bool(r["sensitive"]) and not reveal
    return {"id": r["id"], "entity_id": r["entity_id"], "category": r["category"], "sensitive": bool(r["sensitive"]),
            "locked": locked, "statement": None if locked else r["statement"],
            "since": r["valid_from"].isoformat() if r["valid_from"] else None,
            "until": r["valid_to"].isoformat() if r["valid_to"] else None,
            "recorded_at": r["recorded_at"].isoformat(), "current": r["superseded_by"] is None,
            "origin": r["origin"], "recurring": r["recurring"]}


def recurring_of(value: str | None) -> str | None:
    """The stored 'yearly:MM-DD' of a day a fact repeats on: "2026-11-17", "11-17" or "--11-17" (the year, if any, is
    dropped). None for an empty value; ValueError for anything else or a day that never exists (29 February is allowed)."""
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("yearly:"):
        text = text[len("yearly:"):]
    found = re.fullmatch(r"(?:\d{4}-|--)?(\d{2})-(\d{2})", text)
    if not found:
        raise ValueError(f"A repeating day must be a date or MM-DD, got {value!r}.")
    month, day = int(found.group(1)), int(found.group(2))
    try:
        date(2024, month, day)  # A leap year: 29 February exists.
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a real day.") from exc
    return f"yearly:{month:02d}-{day:02d}"


def check_statement(statement: str, category: str) -> str:
    text = _clean(statement, MAX_STATEMENT + 1)
    if not text:
        raise ValueError("A fact needs a sentence.")
    if len(text) > MAX_STATEMENT:
        raise ValueError(f"A fact is one short sentence: at most {MAX_STATEMENT} characters. Keep longer text as a note.")
    if category == "kimlik_no":
        runs = (re.sub(r"\D", "", run) for run in re.findall(r"\d[\d .\-]*", text))
        if any(len(digits) >= ID_DIGITS for digits in runs):
            raise ValueError("Numbers of ID documents, accounts and cards are never stored: say only that it exists "
                             "(e.g. 'The user has a passport').")
    return text


def _embedded(entity: dict, statement: str) -> str:
    """What is embedded and shown: the statement itself, in the user's words. No label is prepended (it would be a
    composed text the user never wrote); the entity's name still reaches search as the row's title."""
    return statement


async def _source(conn, user_id: str, target_id: int, source: dict) -> None:
    await memory_collections.add_source(
        conn, user_id, "fact", target_id, chat_id=source.get("chat_id"), first_message=source.get("first_message"),
        last_message=source.get("last_message"), item_id=source.get("item_id"), by_user=bool(source.get("by_user")))


def _check_source(source: dict | None) -> dict:
    if not source or not (source.get("chat_id") or source.get("item_id") or source.get("by_user")):
        raise ValueError("A fact needs a source (a chat, an item or the user writing it): nothing was written.")
    return source


async def _vector_for(settings, entity: dict, statement: str):
    target, space, local = await memory.embedding_target(settings)
    vector = (await memory._embed(_embedded(entity, statement), target, "document", local))[0]
    return space, memory._vector(vector)


async def _store_vector(conn, user_id: str, entity: dict, statement: str, space: str, vector: str,
                        source: dict) -> str:
    """The fact's memory (one row, the Ben collection); its group id."""
    group, item = memory.new_group(), memory.new_group()
    await conn.fetchval(
        memory.ROW_SQL, user_id, _embedded(entity, statement), space, vector, FACT, entity["name"] or None, "text", None,
        None, None, group, source.get("chat_id"), None, item, False, None, None, None, None, memory.item_order(), None,
        memory.fold(memory.match_text(_embedded(entity, statement), entity["name"], None, FACT)), None, True)
    if await conn.fetchval("select 1 from orion_mem_collection where user_id = $1 and id = $2", user_id, COLLECTION):
        await conn.execute("insert into orion_mem_collection_item (user_id, collection_id, group_id, main) "
                           "values ($1, $2, $3, true) on conflict do nothing", user_id, COLLECTION, group)
    return group


async def _current(conn, user_id: str, entity_id: int) -> list:
    return await conn.fetch(f"select {_FACT} from orion_mem_fact f where f.user_id = $1 and f.entity_id = $2 and "
                            f"f.superseded_by is null and {_live('f')}", user_id, entity_id)


async def add_fact(user_id: str, settings, entity: dict, category: str, statement: str, *, origin: str, source: dict,
                   since=None, until=None, recurring: str | None = None, run_id: int | None = None) -> dict:
    """Write one fact about `entity` (a dict from resolve_entity), or find that it is already there. Returns
    {"fact": {...}} or {"same_as": id} (the same sentence is already current for this entity: the new source is added to
    it). `recurring`: the day it repeats every year ("11-17": birthdays, anniversaries; memory_events.upcoming). `run_id`: the
    extraction run that wrote it (memory_extract).
    ValueError: no source, an empty/long sentence, a locked kind from an automatic origin. Nothing is written
    when embedding fails (MemoryUnavailable)."""
    require(settings)
    source = _check_source(source)
    category = category_of(category)
    recurring = recurring_of(recurring)
    sensitive = is_sensitive(category)
    if origin == "extraction" and sensitive:
        raise ValueError("A locked (sensitive) fact is never written automatically.")
    text = check_statement(statement, category)
    since, until = _day(since), _day(until)
    same = memory.fold(text)
    async with _db() as conn:
        await memory_collections.ensure_user(conn, user_id)
        for r in await _current(conn, user_id, entity["id"]):
            if memory.fold(r["statement"]) == same:
                await _source(conn, user_id, r["id"], source)
                if recurring and not r["recurring"]:  # The same sentence, now known to repeat: that is all it adds.
                    await conn.execute("update orion_mem_fact set recurring = $3 where user_id = $1 and id = $2", user_id,
                                       r["id"], recurring)
                return {"same_as": r["id"]}
        space = vector = None
        if not sensitive:
            space, vector = await _vector_for(settings, entity, text)
        async with conn.transaction():
            group = await _store_vector(conn, user_id, entity, text, space, vector, source) if not sensitive else None
            fact_id = await conn.fetchval(
                "insert into orion_mem_fact (user_id, entity_id, category, statement, valid_from, valid_to, sensitive, "
                "origin, group_id, recurring, run_id) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) returning id",
                user_id, entity["id"], category, text, since, until, sensitive, origin, group, recurring, run_id)
            await conn.execute("update orion_mem_fact set lineage = id where id = $1", fact_id)
            await _source(conn, user_id, fact_id, source)
            row = await conn.fetchrow(f"select {_FACT} from orion_mem_fact f where f.id = $1", fact_id)
    return {"fact": _fact_out(row, reveal=True), "entity": _entity_out(entity)}


async def _head(conn, user_id: str, fact_id: int, lock: bool = False):
    """The user's fact, which must be the current version of a live fact: LookupError when it is not theirs or is
    removed, ValueError when a newer version replaced it."""
    row = await conn.fetchrow(f"select {_FACT} from orion_mem_fact f where f.user_id = $1 and f.id = $2"
                              f"{' for update of f' if lock else ''}", user_id, fact_id)
    if row is None:
        raise LookupError(fact_id)
    if row["superseded_by"] is not None:
        raise ValueError("This fact was already replaced by a newer version; replace the current one.")
    if not await conn.fetchval(f"select {_live('f')} from orion_mem_fact f where f.id = $1", fact_id):
        raise LookupError(fact_id)
    return row


async def supersede(user_id: str, settings, fact_id: int, statement: str, *, origin: str, source: dict, since=None,
                    category: str | None = None, recurring: str | None = None) -> dict:
    """The fact changed: a new version, the old one closed (`valid_to` = the new `since`, `expired_at` now,
    `superseded_by`), its vector row removed (only the current version is searched). Same sentence: {"same_as"}. The new
    version repeats on the old one's day unless `recurring` says another ("" stops it).
    LookupError for a fact that is not this user's/not live; ValueError when it is already replaced."""
    require(settings)
    source = _check_source(source)
    async with _db() as conn:
        old = await _head(conn, user_id, fact_id)
        category = category_of(category) if category else old["category"]
        sensitive = bool(old["sensitive"]) or is_sensitive(category)
        if origin == "extraction" and sensitive:
            raise ValueError("A locked (sensitive) fact is never written automatically.")
        text = check_statement(statement, category)
        if memory.fold(text) == memory.fold(old["statement"]):
            await _source(conn, user_id, old["id"], source)
            return {"same_as": old["id"]}
        entity = dict(await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2",
                                          user_id, old["entity_id"]))
        since = _day(since)
        repeats = old["recurring"] if recurring is None else recurring_of(recurring)
        space = vector = None
        if not sensitive:
            space, vector = await _vector_for(settings, entity, text)
        async with conn.transaction():
            old = await _head(conn, user_id, fact_id, lock=True)
            group = await _store_vector(conn, user_id, entity, text, space, vector, source) if not sensitive else None
            new_id = await conn.fetchval(
                "insert into orion_mem_fact (user_id, entity_id, lineage, category, statement, valid_from, sensitive, origin, "
                "group_id, recurring) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning id",
                user_id, old["entity_id"], old["lineage"], category, text, since, sensitive, origin, group, repeats)
            await conn.execute("update orion_mem_fact set valid_to = $3, expired_at = now(), superseded_by = $4 "
                               "where user_id = $1 and id = $2", user_id, old["id"], since, new_id)
            if old["group_id"]:  # Its vector row and its place in the collection go; the text stays in the fact table.
                await memory.delete_rows(conn, user_id, "group_id = $2", old["group_id"])
                await conn.execute("delete from orion_mem_collection_item where user_id = $1 and group_id = $2",
                                   user_id, old["group_id"])
            await _source(conn, user_id, new_id, source)
            row = await conn.fetchrow(f"select {_FACT} from orion_mem_fact f where f.id = $1", new_id)
    return {"fact": _fact_out(row, reveal=True), "replaced": old["id"], "entity": _entity_out(entity)}


async def history(user_id: str, settings, fact_id: int, reveal: bool = False) -> dict:
    """Every version of a fact (any version's id), oldest first, each with its sources; locked text only with `reveal`.
    LookupError when the fact is not this user's or its lineage is removed."""
    require(settings)
    async with _db() as conn:
        row = await conn.fetchrow("select lineage, entity_id from orion_mem_fact where user_id = $1 and id = $2",
                                  user_id, fact_id)
        if row is None:
            raise LookupError(fact_id)
        versions = await conn.fetch(
            f"select {_FACT} from orion_mem_fact f where f.user_id = $1 and f.lineage = $2 order by f.id", user_id,
            row["lineage"])
        head = next((v for v in versions if v["superseded_by"] is None), None)
        if head is None or not await conn.fetchval(f"select {_live('f')} from orion_mem_fact f where f.id = $1",
                                                   head["id"]):
            raise LookupError(fact_id)
        sources: dict[int, list] = {}
        for s in await conn.fetch(
                "select target_id, chat_id, first_message, last_message, item_id, by_user, created_at from orion_mem_source "
                "where user_id = $1 and target_type = 'fact' and target_id = any($2::bigint[]) order by id",
                user_id, [v["id"] for v in versions]):
            sources.setdefault(s["target_id"], []).append(
                {"chat_id": s["chat_id"], "first_message": s["first_message"], "last_message": s["last_message"],
                 "item_id": s["item_id"], "by_user": s["by_user"], "at": s["created_at"].isoformat()})
    return {"fact_id": fact_id, "entity_id": row["entity_id"],
            "versions": [{**_fact_out(v, reveal), "sources": sources.get(v["id"], [])} for v in versions]}


async def close_people(conn, user_id: str, people: int = 10, facts: int = 5) -> list[dict]:
    """The user's own card also lists the people linked to them by a relation ("annem", "iş arkadaşım": the entity's
    `relation` is how it relates to the user): [{"id", "name", "relation", "facts": [{"id", "category", "statement"}]}],
    unlocked current facts only, the newest people first. Empty when there are none."""
    rows = await conn.fetch(
        f"select {_ENTITY} from orion_mem_entity where user_id = $1 and kind <> 'self' and relation is not null and "
        "merged_into is null and deleted_at is null order by id desc limit $2", user_id, people)
    out = []
    for e in rows:
        listed = await conn.fetch(
            f"select {_FACT} from orion_mem_fact f where f.user_id = $1 and f.entity_id = $2 and f.superseded_by is null "
            f"and not f.sensitive and {_live('f')} order by f.id desc limit $3", user_id, e["id"], facts)
        out.append({"id": e["id"], "name": e["name"], "relation": e["relation"],
                    "facts": [{"id": f["id"], "category": f["category"], "statement": f["statement"]} for f in listed]})
    return out


async def entity_card(user_id: str, settings, entity_id: int, reveal: bool = False, conn=None) -> dict:
    """An entity with its current facts by category (a fact says how many past versions it has and how many sources);
    locked facts without their text unless `reveal`. LookupError for an entity that is not this user's."""
    require(settings)
    async with _db(conn) as conn:
        e = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2 and "
                                "merged_into is null and deleted_at is null", user_id, entity_id)
        if e is None:
            raise LookupError(entity_id)
        facts = await conn.fetch(
            f"select {_FACT}, (select count(*) from orion_mem_fact p where p.user_id = f.user_id and p.lineage = f.lineage "
            f"and p.id <> f.id) as past, (select count(*) from orion_mem_source s where s.user_id = f.user_id and "
            f"s.target_type = 'fact' and s.target_id = f.id) as sources from orion_mem_fact f where f.user_id = $1 "
            f"and f.entity_id = $2 and f.superseded_by is null and {_live('f')} order by f.category, f.id", user_id,
            entity_id)
        merges = await conn.fetch(
            "select m.id, m.from_id, m.merged_at, e.name from orion_mem_entity_merge m join orion_mem_entity e on "
            "e.id = m.from_id and e.user_id = m.user_id where m.user_id = $1 and m.into_id = $2 and m.undone_at is null "
            "order by m.id", user_id, entity_id)
        close = await close_people(conn, user_id) if e["kind"] == "self" else None
    return {**_entity_out(e), **({"close": close} if close else {}), "facts": [{**_fact_out(f, reveal), "past_versions": int(f["past"]),
                                         "sources": int(f["sources"])} for f in facts],
            "merges": [{"id": m["id"], "from_id": m["from_id"], "name": m["name"], "merged_at": m["merged_at"].isoformat()}
                       for m in merges]}


async def remove_fact(user_id: str, settings, fact_id: int) -> dict:
    """The user removes a fact (and its past versions): a normal one goes to the trash with its memory ({"trash_id"}:
    restore through the trash), a locked one is marked removed ({"restore": id}: restore_fact; both are deleted for
    good after the user's trash days). LookupError when not this user's."""
    require(settings)
    from orion.kernel import memory_trash
    async with _db() as conn:
        row = await conn.fetchrow("select lineage from orion_mem_fact where user_id = $1 and id = $2", user_id, fact_id)
        if row is None:
            raise LookupError(fact_id)
        head = await _head(conn, user_id, await conn.fetchval(
            "select id from orion_mem_fact where user_id = $1 and lineage = $2 and superseded_by is null", user_id,
            row["lineage"]))
        if not head["group_id"]:
            await conn.execute("update orion_mem_fact set deleted_at = now() where user_id = $1 and id = $2", user_id,
                               head["id"])
            return {"restore": head["id"]}
    removed = await memory_trash.trash_groups(user_id, [head["group_id"]])
    return {"trash_id": removed["trash_id"]}


async def restore_fact(user_id: str, settings, fact_id: int) -> dict:
    """Bring back a locked fact removed with remove_fact (a normal one comes back through the trash)."""
    require(settings)
    async with _db() as conn:
        done = await conn.fetchval("update orion_mem_fact set deleted_at = null where user_id = $1 and id = $2 and "
                                   "group_id is null and deleted_at is not null returning id", user_id, fact_id)
    if done is None:
        raise LookupError(fact_id)
    return {"restored": fact_id}


async def after_purge(user_id: str) -> int:
    """Rows were deleted for good: facts whose memory has no row left go with their past versions and sources. Facts
    removed while locked are deleted for good after `days` (purge_removed)."""
    async with _db() as conn:
        async with conn.transaction():
            gone = [r["lineage"] for r in await conn.fetch(
                "select f.lineage from orion_mem_fact f where f.user_id = $1 and f.superseded_by is null and "
                "f.group_id is not null and not exists (select 1 from orion_memory m where m.user_id = f.user_id "
                "and m.group_id = f.group_id)", user_id)]
            return await _delete_lineages(conn, user_id, gone)


async def _delete_lineages(conn, user_id: str, lineages: list[int]) -> int:
    if not lineages:
        return 0
    ids = [r["id"] for r in await conn.fetch("delete from orion_mem_fact where user_id = $1 and lineage = any($2::bigint[]) "
                                             "returning id", user_id, lineages)]
    if ids:
        await conn.execute("delete from orion_mem_source where user_id = $1 and target_type = 'fact' and "
                           "target_id = any($2::bigint[])", user_id, ids)
    return len(lineages)


async def purge_removed(user_id: str, days: int) -> int:
    """Locked facts removed more than `days` ago are deleted for good."""
    async with _db() as conn:
        async with conn.transaction():
            old = [r["lineage"] for r in await conn.fetch(
                "select lineage from orion_mem_fact where user_id = $1 and superseded_by is null and group_id is null and "
                "deleted_at < now() - make_interval(days => $2)", user_id, days)]
            return await _delete_lineages(conn, user_id, old)


async def purge_removed_all(settings_of) -> int:
    async with _db() as conn:
        users = [r["user_id"] for r in await conn.fetch(
            "select distinct user_id from orion_mem_fact where group_id is null and deleted_at is not null")]
    from orion.kernel import memory_trash
    count = 0
    for user_id in users:
        count += await purge_removed(user_id, memory_trash.days_of(await settings_of(user_id)))
    return count


# --- What search and memory_context tell about a fact ------------------------------------------------------------------

async def facts_on(conn, user_id: str, groups: list[str]) -> dict[str, dict]:
    """{memory group: {"id", "about", "category", "since"}} for the memories among `groups` that are current facts: one
    indexed lookup on the caller's connection (memory_groups.list_groups: a search card says who its fact is about)."""
    if not groups:
        return {}
    rows = await conn.fetch(
        "select f.group_id, f.id, f.category, f.valid_from, e.name, e.relation from orion_mem_fact f join "
        "orion_mem_entity e on e.id = f.entity_id and e.user_id = f.user_id where f.user_id = $1 and "
        "f.group_id = any($2::text[]) and f.superseded_by is null", user_id, groups)
    return {r["group_id"]: {"id": r["id"], "about": r["name"] if not r["relation"] else f"{r['name']} ({r['relation']})",
                            "category": r["category"], "since": r["valid_from"].isoformat() if r["valid_from"] else None}
            for r in rows}


async def fact_of_group(user_id: str, group: str) -> int | None:
    async with _db() as conn:
        return await conn.fetchval("select id from orion_mem_fact where user_id = $1 and group_id = $2", user_id, group)


async def search_locked(user_id: str, settings, words: str = "", about: str | None = None, limit: int = 20) -> list[dict]:
    """Locked facts the user asked about directly: those whose sentence, category or entity name contains a word of
    `words` (folded, 3+ letters); with no words, the newest. Word matching only: they have no vectors."""
    require(settings)
    terms = [w for w in re.findall(r"\w+", memory.fold(words)) if len(w) >= 3]
    async with _db() as conn:
        rows = await conn.fetch(
            f"select {_FACT}, e.name, e.relation from orion_mem_fact f join orion_mem_entity e on e.id = f.entity_id and "
            f"e.user_id = f.user_id where f.user_id = $1 and f.sensitive and f.superseded_by is null and {_live('f')} "
            f"order by f.id desc", user_id)
    wanted = None
    if about:
        async with _db() as conn:
            found = await find_entities(conn, user_id, about) if memory.fold(about) not in SELF_NAMES else \
                [await _self_entity(conn, user_id)]
        wanted = {e["id"] for e in found}
    out = []
    for r in rows:
        if wanted is not None and r["entity_id"] not in wanted:
            continue
        hay = memory.fold(f"{r['statement']} {r['category']} {r['name']} {r['relation'] or ''}")
        if terms and not any(t in hay for t in terms):
            continue
        out.append({"id": r["id"], "about": r["name"], "category": r["category"], "statement": r["statement"],
                    "since": r["valid_from"].isoformat() if r["valid_from"] else None})
        if len(out) >= limit:
            break
    return out


# --- Search: time, the past, the entity card (step 8; memory_groups.search_queries and the search_memory tool) ----------------

async def not_yet_groups(conn, user_id: str, as_of: date) -> set[str]:
    """The memories of current facts that only became true AFTER `as_of` (a start date later than it): left out of a search
    "as of" that day. A fact with no start date counts as always true."""
    return {r["group_id"] for r in await conn.fetch(
        "select group_id from orion_mem_fact where user_id = $1 and superseded_by is null and group_id is not null "
        "and valid_from > $2", user_id, as_of)}


def _end_of(r) -> date | None:
    """The day a past version stopped being true: its `valid_to`; an end nobody gave falls back to the day Orion stopped
    counting it (`expired_at`)."""
    return r["valid_to"] or (r["expired_at"].date() if r["expired_at"] else None)


def _true_on(r, as_of: date | None) -> bool:
    """Was this past version true on `as_of`? From its start (unknown = always) to its end."""
    if as_of is None:
        return True
    start, end = r["valid_from"], _end_of(r)
    return (start is None or start <= as_of) and (end is None or end > as_of)


PAST_LIMIT = 20  # Past versions listed by one search.


async def search_past(user_id: str, settings, terms: list[str], entity_ids: list[int] | None = None,
                      as_of: date | None = None, limit: int = PAST_LIMIT, conn=None) -> list[dict]:
    """Past versions of facts (replaced ones; the vector rows of a version go when it is replaced, so they are found by words
    and dates, never by meaning, and carry no score): [{"id", "about", "category", "statement", "since", "until", "past": True}].
    Only unlocked facts of lineages that still live. `terms`: the query's words (folded, 3+ letters): a version counts when it
    holds at least one, the more the earlier; no words: all of them (of `entity_ids` if given), the newest end first. `as_of`:
    only the versions that were true that day."""
    require(settings)
    words = [t for t in dict.fromkeys(w for text in terms for w in re.findall(r"\w+", memory.fold(text))) if len(t) >= 3]
    params: list = [user_id]
    where = ["f.user_id = $1", "f.superseded_by is not null", "not f.sensitive", "f.deleted_at is null",
             f"exists (select 1 from orion_mem_fact h where h.user_id = f.user_id and h.lineage = f.lineage and "
             f"h.superseded_by is null and {_live('h')})"]
    if entity_ids is not None:
        params.append(list(entity_ids))
        where.append(f"f.entity_id = any(${len(params)}::bigint[])")
    async with _db(conn) as conn:
        rows = await conn.fetch(
            f"select {_FACT}, e.name, e.relation from orion_mem_fact f join orion_mem_entity e on e.id = f.entity_id and "
            f"e.user_id = f.user_id where {' and '.join(where)} order by f.id desc limit 2000", *params)
    scored = []
    for r in rows:
        if not _true_on(r, as_of):
            continue
        hay = memory.fold(f"{r['statement']} {r['name']} {r['relation'] or ''}")
        hits = sum(1 for w in words if w in hay)
        if words and not hits:
            continue
        scored.append((hits, r))
    scored.sort(key=lambda x: (-x[0], -(_end_of(x[1]) or date.min).toordinal(), -x[1]["id"]))
    return [{"id": r["id"], "about": r["name"] if not r["relation"] else f"{r['name']} ({r['relation']})",
             "category": r["category"], "statement": r["statement"],
             "since": r["valid_from"].isoformat() if r["valid_from"] else None,
             "until": _end_of(r).isoformat() if _end_of(r) else None,
             "past": True} for _, r in scored[:limit]]


async def named_entities(user_id: str, settings, texts: list[str], conn=None) -> list[dict]:
    """The user's entities a query names: a name, relation or alias that is a whole word (or words) of the query, with a
    suffix of up to four letters allowed on a name of four letters or more (Turkish: "annemin", "Ayşe'nin"). The user's own
    entity only when the whole query is one of its names ("me", "ben"): "ben" inside any Turkish sentence is not a question
    about the user. One read of the (small) entity table. [{"id", "kind", "name", "relation", "aliases"}]."""
    require(settings)
    queries = [q for q in (memory.fold(" ".join((t or "").split())) for t in texts) if q]
    if not queries:
        return []
    async with _db(conn) as conn:
        rows = await conn.fetch(f"select {_ENTITY}, keys from orion_mem_entity where user_id = $1 and merged_into is null "
                                "and deleted_at is null", user_id)
    found = []
    for r in rows:
        for key in r["keys"]:
            if not key:
                continue
            if r["kind"] == "self":
                hit = key in queries
            else:
                tail = r"\w{0,4}" if len(key) >= 4 else ""
                hit = any(re.search(rf"(?<!\w){re.escape(key)}{tail}(?!\w)", q) for q in queries)
            if hit:
                found.append(_entity_out(r))
                break
    return found


async def search_card(user_id: str, settings, entity_id: int, events: int = 3, facts: int = 12, conn=None) -> dict:
    """The entity as a search answers it (the tool's `entity`): who it is, its current unlocked facts (the newest `facts`;
    how many past versions each has), how many locked facts there are (no text), its newest `events` events and how many
    facts were cut. LookupError for an entity that is not this user's."""
    from orion.kernel import memory_events
    card = await entity_card(user_id, settings, entity_id, conn=conn)
    open_facts = [f for f in card["facts"] if not f["locked"]]
    listed = await memory_events.list_events(user_id, settings, entity_id=entity_id, limit=events, conn=conn)
    out = {"id": card["id"], "name": card["name"], "kind": card["kind"], "relation": card["relation"],
           "aliases": card["aliases"],
           "facts": [{"id": f["id"], "category": f["category"], "statement": f["statement"], "since": f["since"],
                      **({"past_versions": f["past_versions"]} if f["past_versions"] else {})}
                     for f in open_facts[:facts]],
           "recent_events": [{"id": e["id"], "date": e["date"], "precision": e["precision"], "title": e["title"]}
                             for e in listed["events"]]}
    if card.get("close"):
        out["close_people"] = [{"name": c["name"], "relation": c["relation"], "facts": [f["statement"] for f in c["facts"]]}
                               for c in card["close"]]
    if len(open_facts) > facts:
        out["more_facts"] = len(open_facts) - facts
    if any(f["locked"] for f in card["facts"]):
        out["locked_facts"] = sum(1 for f in card["facts"] if f["locked"])
    return out


async def search_extras(user_id: str, settings, texts: list[str], entity_id: int | None = None, as_of: date | None = None,
                        include_past: bool = False, about_given: bool = False, conn=None) -> dict:
    """Everything a search adds beside its ranked results, on ONE connection (the search's own: memory_groups.list_groups's `side`):
    the entity card (`entity_id`, else the one entity the query names), the entities a query names when several
    fit (`candidates`, no card), and the past versions (`past`) when asked for (`include_past`, or `as_of`). {"card",
    "candidates", "past"}."""
    out: dict = {"card": None, "candidates": [], "past": None}
    async with _db(conn) as conn:
        if entity_id is None and texts:
            named = await named_entities(user_id, settings, texts, conn=conn)
            if len(named) == 1:
                entity_id = named[0]["id"]
            elif named:
                out["candidates"] = [{"id": e["id"], "name": e["name"], "relation": e["relation"]} for e in named[:5]]
        if entity_id is not None:
            try:
                out["card"] = await search_card(user_id, settings, entity_id, conn=conn)
            except LookupError:
                pass
        if include_past or as_of is not None:
            out["past"] = await search_past(user_id, settings, texts, [entity_id] if about_given and entity_id else None,
                                            as_of, conn=conn)
    return out


async def fact_context(user_id: str, fact_id: int) -> dict | None:
    """For memory_context: a fact's entity, its category, and its history and sources (never locked text: a locked
    fact has no memory row to ask about)."""
    async with _db() as conn:
        row = await conn.fetchrow("select lineage, entity_id from orion_mem_fact where user_id = $1 and id = $2 and "
                                  "not sensitive", user_id, fact_id)
        if row is None:
            return None
        entity = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2", user_id,
                                     row["entity_id"])
        versions = await conn.fetch(f"select {_FACT} from orion_mem_fact f where f.user_id = $1 and f.lineage = $2 "
                                    "order by f.id", user_id, row["lineage"])
        sources = await conn.fetch(
            "select target_id, chat_id, first_message, last_message, item_id, by_user from orion_mem_source where "
            "user_id = $1 and target_type = 'fact' and target_id = any($2::bigint[]) order by id", user_id,
            [v["id"] for v in versions])
    return {"entity": _entity_out(entity), "category": versions[-1]["category"],
            "versions": [_fact_out(v) for v in versions],
            "sources": [{"version": s["target_id"], "chat_id": s["chat_id"], "first_message": s["first_message"],
                         "last_message": s["last_message"], "item_id": s["item_id"], "by_user": s["by_user"]}
                        for s in sources]}


# --- Merging entities -------------------------------------------------------------------------------------------------

async def merge_entities(user_id: str, settings, from_id: int, into_id: int, conn=None) -> dict:
    """Merge entity `from_id` into `into_id` in one transaction: its facts move, its name, relation and aliases become
    aliases of the survivor; what moved is recorded so undo_merge can put it back. ValueError for the user's own entity
    mixed with another kind, the same entity, or one already merged; LookupError for one that is not this user's.
    {"merge_id", "into", "moved_facts"}."""
    require(settings)
    if from_id == into_id:
        raise ValueError("An entity cannot be merged into itself.")
    async with _db() as own:
        db = conn or own
        async with db.transaction():
            rows = {r["id"]: r for r in await db.fetch(
                f"select {_ENTITY}, keys from orion_mem_entity where user_id = $1 and id = any($2::bigint[]) and "
                f"deleted_at is null order by id for update", user_id, [from_id, into_id])}
            if len(rows) != 2:
                raise LookupError("entity")
            src, dst = rows[from_id], rows[into_id]
            if src["merged_into"] is not None or dst["merged_into"] is not None:
                raise ValueError("One of these entities was already merged.")
            if (src["kind"] == "self") != (dst["kind"] == "self"):
                raise ValueError("The user's own entity cannot be merged with another one.")
            moved = [r["id"] for r in await db.fetch(
                "update orion_mem_fact set entity_id = $3 where user_id = $1 and entity_id = $2 returning id", user_id,
                from_id, into_id)]
            links = await _move_links(db, user_id, from_id, into_id)
            have = {memory.fold(a) for a in [dst["name"], dst["relation"] or "", *dst["aliases"]]}
            added = []
            for name in [src["name"], src["relation"] or "", *src["aliases"]]:
                if name and memory.fold(name) not in have:
                    have.add(memory.fold(name))
                    added.append(name)
            aliases = [*dst["aliases"], *added]
            await db.execute("update orion_mem_entity set aliases = $3, keys = $4 where user_id = $1 and id = $2",
                             user_id, into_id, aliases, keys_of(dst["name"], dst["relation"], aliases))
            await db.execute("update orion_mem_entity set merged_into = $3 where user_id = $1 and id = $2", user_id,
                             from_id, into_id)
            merge_id = await db.fetchval(
                "insert into orion_mem_entity_merge (user_id, from_id, into_id, moved) values ($1, $2, $3, $4) returning id",
                user_id, from_id, into_id, json.dumps({"facts": moved, "links": links, "aliases": added}))
    return {"merge_id": merge_id, "into": into_id, "moved_facts": len(moved), "moved_links": len(links), "aliases": added}


async def _move_links(db, user_id: str, from_id: int, into_id: int) -> list[dict]:
    """The events linked to entity `from_id` are linked to `into_id` instead (an event already linked to both keeps one
    link). Returns what moved: [{"event", "role", "dup"}] (`dup`: the survivor was already linked, the link only went)."""
    moved = []
    for r in await db.fetch("select from_id, role from orion_mem_link where user_id = $1 and from_type = 'event' and "
                            "to_type = 'entity' and to_id = $2 order by from_id", user_id, str(from_id)):
        have = await db.fetchval("select 1 from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2 "
                                 "and to_type = 'entity' and to_id = $3", user_id, r["from_id"], str(into_id))
        if have:
            await db.execute("delete from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = $2 and "
                             "to_type = 'entity' and to_id = $3", user_id, r["from_id"], str(from_id))
        else:
            await db.execute("update orion_mem_link set to_id = $4 where user_id = $1 and from_type = 'event' and "
                             "from_id = $2 and to_type = 'entity' and to_id = $3", user_id, r["from_id"], str(from_id),
                             str(into_id))
        moved.append({"event": r["from_id"], "role": r["role"], "dup": bool(have)})
    return moved


async def undo_merge(user_id: str, settings, merge_id: int) -> dict:
    """Put a merge back: the facts it moved return to the entity they came from (a fact moved on since stays where it is),
    the aliases it added go, the entity is its own again. ValueError when already undone or the entity was merged on."""
    require(settings)
    async with _db() as conn:
        async with conn.transaction():
            m = await conn.fetchrow("select * from orion_mem_entity_merge where user_id = $1 and id = $2 for update",
                                    user_id, merge_id)
            if m is None:
                raise LookupError(merge_id)
            if m["undone_at"] is not None:
                raise ValueError("This merge was already undone.")
            src = await conn.fetchrow(f"select {_ENTITY}, keys from orion_mem_entity where user_id = $1 and id = $2 for "
                                      "update", user_id, m["from_id"])
            if src is None or src["merged_into"] != m["into_id"]:
                raise ValueError("The merged entity was merged again since; undo that merge first.")
            moved = json.loads(m["moved"]) if isinstance(m["moved"], str) else m["moved"]
            await conn.execute("update orion_mem_fact set entity_id = $3 where user_id = $1 and entity_id = $4 and "
                               "id = any($2::bigint[])", user_id, [int(i) for i in moved.get("facts", [])], m["from_id"],
                               m["into_id"])
            for link in moved.get("links", []):  # An event moved on since (replaced by a newer version) keeps its links.
                if link["dup"]:
                    await conn.execute("insert into orion_mem_link (user_id, from_type, from_id, to_type, to_id, role) "
                                       "select $1, 'event', $2, 'entity', $3, $4 where exists (select 1 from orion_mem_event "
                                       "where user_id = $1 and id = $2) on conflict do nothing", user_id, int(link["event"]),
                                       str(m["from_id"]), link["role"])
                else:
                    await conn.execute("update orion_mem_link set to_id = $5 where user_id = $1 and from_type = 'event' and "
                                       "from_id = $2 and to_type = 'entity' and to_id = $3 and role is not distinct from $4",
                                       user_id, int(link["event"]), str(m["into_id"]), link["role"], str(m["from_id"]))
            dst = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2 for update",
                                      user_id, m["into_id"])
            drop = {memory.fold(a) for a in moved.get("aliases", [])}
            aliases = [a for a in dst["aliases"] if memory.fold(a) not in drop]
            await conn.execute("update orion_mem_entity set aliases = $3, keys = $4 where user_id = $1 and id = $2",
                               user_id, m["into_id"], aliases, keys_of(dst["name"], dst["relation"], aliases))
            await conn.execute("update orion_mem_entity set merged_into = null where user_id = $1 and id = $2", user_id,
                               m["from_id"])
            await conn.execute("update orion_mem_entity_merge set undone_at = now() where id = $1", merge_id)
    return {"merge_id": merge_id, "entity_id": m["from_id"], "restored_facts": len(moved.get("facts", [])),
            "restored_links": len(moved.get("links", []))}


async def duplicate_pairs(conn, user_id: str) -> list[dict]:
    """Entities of one kind that share an exact folded name, relation or alias (never the user's own entity): the pairs a
    batch card may offer, the one with fewer facts first (it would be merged into the other). Pairs the user already
    decided (a merge proposal accepted or rejected) are left out."""
    rows = await conn.fetch("select e.id, e.kind, e.name, e.relation, e.keys, (select count(*) from orion_mem_fact f "
                            "where f.user_id = e.user_id and f.entity_id = e.id and f.superseded_by is null) as n "
                            "from orion_mem_entity e where e.user_id = $1 and e.merged_into is null and e.deleted_at is "
                            "null and e.kind <> 'self' order by e.id", user_id)
    decided = set()
    for r in await conn.fetch("select payload from orion_mem_proposal where user_id = $1 and action = 'merge_entities' "
                              "and state <> 'pending'", user_id):
        p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
        decided |= {(int(p["from_id"]), int(p["into_id"])), (int(p["into_id"]), int(p["from_id"]))}
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a["kind"] == b["kind"] and set(a["keys"]) & set(b["keys"]) and (a["id"], b["id"]) not in decided:
                small, big = sorted((a, b), key=lambda r: (r["n"], -r["id"]))
                pairs.append({"from_id": small["id"], "into_id": big["id"], "from_name": small["name"],
                              "into_name": big["name"], "from_facts": int(small["n"]), "into_facts": int(big["n"])})
    return pairs


# --- Proposals: the cards (§8). The card machinery is kernel/memory_proposals.py; this module only knows what its own
# actions (add, supersede, merge_entities) do when the user presses Onayla (carry_out). ---------------------------------------

async def propose(user_id: str, settings, action: str, payload: dict, reason: str | None = None,
                  chat_id: str | None = None, batch_id: int | None = None, conn=None) -> int:
    from orion.kernel import memory_proposals
    return await memory_proposals.propose(user_id, settings, action, payload, reason, chat_id, batch_id, conn=conn)


async def proposals(user_id: str, settings, state: str | None = "pending", ids: list[int] | None = None) -> list[dict]:
    from orion.kernel import memory_proposals
    return await memory_proposals.proposals(user_id, settings, state, ids)


async def decide(user_id: str, settings, accept: list[int] = (), reject: list[int] = (),
                 edits: dict[int, str] | None = None) -> list[dict]:
    from orion.kernel import memory_proposals
    return await memory_proposals.decide(user_id, settings, accept, reject, edits)


async def _pending_like(conn, user_id: str, action: str, payload: dict) -> int | None:
    from orion.kernel import memory_proposals
    return await memory_proposals.pending_like(conn, user_id, action, payload)


async def propose_supersede(user_id: str, settings, fact_id: int, statement: str, *, since=None, chat_id: str | None = None,
                            reason: str | None = None, category: str | None = None, recurring: str | None = None) -> dict:
    """The model found a fact changed: never closes it, opens a card (old -> new). {"proposal": id} or {"same_as": id}
    (the new sentence is the current one already). LookupError / ValueError as supersede (checked now, not at the press)."""
    require(settings)
    async with _db() as conn:
        old = await _head(conn, user_id, fact_id)
        cat = category_of(category) if category else old["category"]
        text = check_statement(statement, cat)
        if memory.fold(text) == memory.fold(old["statement"]):
            return {"same_as": old["id"]}
        entity = await conn.fetchrow(f"select {_ENTITY} from orion_mem_entity where user_id = $1 and id = $2", user_id,
                                     old["entity_id"])
        pid = await propose(user_id, settings, "supersede", {
            "fact_id": old["id"], "entity_id": old["entity_id"], "about": label_of(dict(entity)), "category": cat,
            "old": old["statement"], "statement": text, "since": since and str(_day(since)),
            "recurring": recurring_of(recurring), "source": {"chat_id": chat_id}}, reason, chat_id, conn=conn)
    return {"proposal": pid}


async def propose_add(user_id: str, settings, about: str, kind: str, relation: str | None, category: str, statement: str,
                      *, since=None, chat_id: str | None = None, reason: str | None = None,
                      recurring: str | None = None) -> dict:
    """A fact the model may not write itself (a locked kind): a card with the sentence; the entity is made when the user
    presses Onayla. Ambiguous when the name fits several entities."""
    require(settings)
    cat = category_of(category)
    text = check_statement(statement, cat)
    async with _db() as conn:
        entity, _ = await resolve_entity(conn, user_id, about, kind, relation, create=False)
    pid = await propose(user_id, settings, "add", {
        "about": about, "entity_id": entity["id"] if entity else None, "kind": kind, "relation": relation,
        "category": cat, "statement": text, "since": since and str(_day(since)), "recurring": recurring_of(recurring),
        "source": {"chat_id": chat_id}}, reason, chat_id)
    return {"proposal": pid}


async def propose_merges(user_id: str, settings) -> dict:
    """The duplicate entities found by exact names as ONE batch card (each pair checked on its own by the user). {"batch_id",
    "proposals": [ids]}; empty when there is nothing new to offer."""
    require(settings)
    async with _db() as conn:
        async with conn.transaction():
            pairs = await duplicate_pairs(conn, user_id)
            if not pairs:
                return {"batch_id": None, "proposals": []}
            open_ones = {}
            for p in pairs:
                found = await _pending_like(conn, user_id, "merge_entities", p)
                if found:
                    open_ones[found] = p
            batch = await conn.fetchval("select coalesce(max(batch_id), 0) + 1 from orion_mem_proposal where user_id = $1",
                                        user_id)
            fresh = [p for p in pairs if p not in open_ones.values()]
            ids = [await propose(user_id, settings, "merge_entities", p, "Aynı ad veya takma ad.", None, batch, conn=conn)
                   for p in fresh]
            if not fresh:  # Nothing new: the card that is already open.
                batch = await conn.fetchval("select batch_id from orion_mem_proposal where id = $1", min(open_ones))
    return {"batch_id": batch, "proposals": sorted([*open_ones, *ids])}


async def carry_out(user_id: str, settings, card: dict, edit: str | None) -> dict:
    p, action = card["payload"], card["action"]
    source = {**(p.get("source") or {}), "by_user": True} if edit or not (p.get("source") or {}).get("chat_id") \
        else p["source"]
    if action == "merge_entities":
        return await merge_entities(user_id, settings, int(p["from_id"]), int(p["into_id"]))
    text = (edit or p["statement"]).strip()
    if action == "supersede":
        done = await supersede(user_id, settings, int(p["fact_id"]), text, origin="user" if edit else "model",
                               source=source, since=p.get("since"), category=p.get("category"),
                               recurring=p.get("recurring"))
        return {"fact_id": (done.get("fact") or {}).get("id") or done.get("same_as")}
    if action == "add":
        async with _db() as conn:
            async with conn.transaction():
                await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ENTITY_LOCK, user_id)
                entity, _ = await resolve_entity(conn, user_id, p["about"] if not p.get("entity_id") else f"#{p['entity_id']}",
                                                 p.get("kind") or "person", p.get("relation"))
        done = await add_fact(user_id, settings, entity, p["category"], text, origin="user" if edit else "model",
                              source=source, since=p.get("since"), recurring=p.get("recurring"))
        return {"fact_id": (done.get("fact") or {}).get("id") or done.get("same_as")}
    raise ValueError(f"Unknown proposal action {action!r}")


async def write_by_user(user_id: str, settings, about: str, category: str, statement: str, *, kind: str = "person",
                        relation: str | None = None, since=None, entity_id: int | None = None,
                        recurring: str | None = None) -> dict:
    """The user writes a fact on the page: no card (their own change), source "by_user"."""
    require(settings)
    async with _db() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ENTITY_LOCK, user_id)
            entity, _ = await resolve_entity(conn, user_id, f"#{entity_id}" if entity_id else about, kind, relation)
    return await add_fact(user_id, settings, entity, category, statement, origin="user", source={"by_user": True},
                          since=since, recurring=recurring)


async def write_by_model(user_id: str, settings, chat_id: str, about: str, kind: str, relation: str | None, category: str,
                         statement: str, since=None, recurring: str | None = None) -> dict:
    """The model's add_memory(type=fact): written at once when it is a normal, clearly stated fact; a locked kind opens a
    card instead (§8: sensitive at most a card). Sourced by the chat. {"fact"}, {"same_as"} or {"proposal"}."""
    require(settings)
    cat = category_of(category)
    if is_sensitive(cat):
        return await propose_add(user_id, settings, about, kind, relation, cat, statement, since=since, chat_id=chat_id,
                                 recurring=recurring)
    async with _db() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1, hashtext($2))", ENTITY_LOCK, user_id)
            entity, _ = await resolve_entity(conn, user_id, about, kind, relation)
    return await add_fact(user_id, settings, entity, cat, statement, origin="model", source={"chat_id": chat_id},
                          since=since, recurring=recurring)
