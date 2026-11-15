"""Memory cards: the rows saved together (`group_id`: one chat save, one note, one file) shown and handled as one
memory, on the page, on the map and in the tools. A row's copies in other vector spaces (memory_move) share its
group and its content key, so a memory is listed once and each of its parts once, the copy in the preferred space
(the active model's, or the filtered one's) standing for the others."""
from __future__ import annotations

import json
import logging

from orion.kernel import memory, registry
from orion.kernel.memory import CONTENT_KEY, KINDS, content_key, group_key, prefer_space

logger = logging.getLogger(__name__)

GROUP_KEY = "coalesce(group_id, 'r' || id::text)"
GAPS = {"chat_save": 180, "file": 60}  # Seconds between rows that still count as one save (media embed slowly).
NOTE_GAP = 3  # A note's chunks are inserted together; two notes a few seconds apart are two memories.
STRONG, WEAK = 0.8, 0.55  # Share of the best result's rank from which a card is strong / below which it is weak.
WEAK_SCORE = {"text": 0.25, "image": 0.12, "audio": 0.12}  # Meaning score below which a match is weak anyway.
CARD_TEXT = 600
PREVIEW_MEDIA = 6
MAP_NODES = 300
_ROW = ("id, text, kind, vector_space, source_kind, source_chat_title, created_at, media_id, media_mime, "
        "media_seconds, group_id, copied_from, source_chat_id, source_message_id")


# --- Backfill -------------------------------------------------------------------------------------------------------

def plan_groups(rows: list[dict]) -> dict[int, str]:
    """A group for each row saved before groups existed (rows in save order): rows of one user, space, source and
    title follow each other within GAPS seconds (a chat save's text, then its images and audio; a file's chunks);
    an image or audio added as a file is one group with its audio pieces."""
    plan, open_groups = {}, {}
    for r in rows:
        if r["source_kind"] == "file" and r["kind"] != "text" and r.get("media_id"):
            key, gap = ("file-media", r["user_id"], r["vector_space"], r["media_id"]), None
        else:
            key = (r["source_kind"], r["user_id"], r["vector_space"], r.get("title") or r.get("source_chat_title") or "")
            gap = GAPS.get(r["source_kind"], NOTE_GAP)
        current = open_groups.get(key)
        if current and (gap is None or (r["created_at"] - current[1]).total_seconds() <= gap):
            group = current[0]
        else:
            group = memory.new_group()
        open_groups[key] = (group, r["created_at"])
        plan[r["id"]] = group
    _join_named_media(rows, plan)
    return plan


def _join_named_media(rows: list[dict], plan: dict[int, str]) -> None:
    """A chat save's image or audio that a later save stored alone (the text was there already, so only the file
    was new) joins the text that names it: same user, space and chat title, the file name in the text."""
    members: dict[str, list[dict]] = {}
    for r in rows:
        members.setdefault(plan[r["id"]], []).append(r)
    for r in rows:
        if r["source_kind"] != "chat_save" or r["kind"] == "text" or not r.get("text"):
            continue
        if any(m["kind"] == "text" for m in members[plan[r["id"]]]):
            continue
        name = r["text"].split(" — ", 1)[0].strip()
        if len(name) < 3:
            continue
        title = r.get("title") or r.get("source_chat_title") or ""
        owner = next((t for t in rows if t["kind"] == "text" and t["source_kind"] == "chat_save"
                      and t["user_id"] == r["user_id"] and t["vector_space"] == r["vector_space"]
                      and (t.get("title") or t.get("source_chat_title") or "") == title and name in t["text"]), None)
        if owner is not None:
            plan[r["id"]] = plan[owner["id"]]


async def backfill_groups(conn) -> int:
    """Give every row without a group one (see plan_groups); returns how many rows got one."""
    rows = await conn.fetch(
        "select id, user_id, vector_space, source_kind, source_chat_title as title, kind, media_id, created_at, text "
        "from orion_memory where group_id is null order by created_at, id")
    if not rows:
        return 0
    members: dict[str, list[int]] = {}
    for row_id, group in plan_groups([dict(r) for r in rows]).items():
        members.setdefault(group, []).append(row_id)
    await conn.executemany("update orion_memory set group_id = $2 where id = any($1::bigint[]) and group_id is null",
                           [(ids, group) for group, ids in members.items()])
    logger.info("Memory: %d rows grouped into %d memories", len(rows), len(members))
    return len(rows)


# --- Cards ----------------------------------------------------------------------------------------------------------

def _part(r: dict, spaces: list[str] | None = None, full: bool = False) -> dict:
    text = r["text"] if full or r["kind"] == "text" else r["text"][:200]
    part = {"id": r["id"], "kind": r["kind"], "text": text, "vector_space": r["vector_space"]}
    if r.get("media_id"):
        seconds = r.get("media_seconds")
        part.update(media=True, media_mime=r.get("media_mime"),
                    media_seconds=None if seconds is None else round(float(seconds), 1))
    if spaces is not None:
        part["spaces"] = spaces
    return part


def card(group: str, rows: list[dict], prefer, active, full: bool = False, matched: int | None = None) -> dict:
    """One memory as the page shows it. `full`: every part with its whole text and the spaces it is in (the detail
    panel); else the text of one text part (the matched one, else the first) and the first media parts."""
    parts = prefer_space(rows, prefer)
    first = min(rows, key=lambda r: r["id"])
    counts = {kind: sum(1 for p in parts if p["kind"] == kind) for kind in KINDS}
    counts = {kind: n for kind, n in counts.items() if n}
    spaces = sorted({r["vector_space"] for r in rows})
    active_keys = {content_key(r) for r in rows if r["vector_space"] == active}
    item = {"group_id": group, "kind": next(iter(counts)) if len(counts) == 1 else "mixed", "counts": counts,
            "part_count": len(parts), "source_kind": first["source_kind"], "title": first["source_chat_title"],
            "created_at": min(r["created_at"] for r in rows).isoformat(), "vector_spaces": spaces,
            "searchable": bool(active_keys), "missing_in_active": sum(1 for p in parts if content_key(p) not in active_keys)}
    texts = [p for p in parts if p["kind"] == "text"]
    if full:
        in_spaces: dict[int, list[str]] = {}
        for r in rows:
            in_spaces.setdefault(content_key(r), []).append(r["vector_space"])
        item["parts"] = [_part(p, sorted(set(in_spaces[content_key(p)])), True) for p in parts]
        # The models it can be searched with: one memory with vectors from one or more models, never "copies".
        item["models"] = [{"space": s, "active": s == active,
                           "parts": sum(1 for p in parts if s in in_spaces[content_key(p)])}
                          for s in sorted(spaces, key=lambda s: (s != active, s))]
        chat = next((r for r in rows if r.get("source_chat_id")), None)
        item["source_chat_id"] = chat["source_chat_id"] if chat else None
        return item
    shown = next((p for p in texts if p["id"] == matched), texts[0] if texts else None)
    if shown is not None:
        item["text"] = shown["text"][:CARD_TEXT]
        item["text_part"] = shown["id"]
        if len(texts) > 1:
            item["text_index"] = texts.index(shown) + 1
    item["parts"] = [_part(p) for p in parts if p["kind"] != "text"][:PREVIEW_MEDIA]
    return item


async def _group_rows(conn, user_id: str, groups: list[str]) -> dict[str, list[dict]]:
    real = [g for g in groups if not g.startswith("r")]
    loose = [int(g[1:]) for g in groups if g.startswith("r") and g[1:].isdigit()]
    found: dict[str, list[dict]] = {}
    for r in await conn.fetch(f"select {_ROW} from orion_memory where user_id = $1 and "
                              "(group_id = any($2::text[]) or (group_id is null and id = any($3::bigint[]))) "
                              "order by id", user_id, real, loose):
        found.setdefault(group_key(r), []).append(dict(r))
    return found


def _filters(vector_space: str, kind: str, params: list) -> str:
    sql = ""
    if vector_space and vector_space != "all":
        params.append(vector_space)
        sql += f" and vector_space = ${len(params)}"
    if kind and kind != "all":
        params.append(kind)
        sql += f" and kind = ${len(params)}"
    return sql


def _rank(entry: dict) -> float:
    bonus = {"text": memory.TEXT_BONUS, "similar": 0.8 * memory.TEXT_BONUS}.get(entry["tmatch"], 0.0)
    return (float(entry["score"]) if entry["score"] is not None else 0.0) + bonus + 0.01 * float(entry["tscore"])


def _strength(entry: dict, top: float) -> str:
    share = _rank(entry) / top if top > 0 else 0.0
    row = entry["row"]
    low = (not entry["tmatch"] and entry["score"] is not None
           and float(entry["score"]) < WEAK_SCORE.get(row.get("kind") or "text", 0.25))
    if share < WEAK or low:
        return "weak"
    return "strong" if share >= STRONG else "medium"


def merge_matches(meaning: list[dict], texts: list[dict]) -> list[dict]:
    """Meaning and text matches per memory, best first: {"group", "row" (the part that matched), "score", "tmatch",
    "tscore", "match" (text, similar or meaning), "strength"}. A memory ranks by its best part (memory._ranked's
    measure)."""
    pool: dict[str, dict] = {}
    for r in meaning:
        pool.setdefault(group_key(r), {"group": group_key(r), "row": r, "score": r["score"], "tmatch": None,
                                       "tscore": 0.0})
    order = {"text": 2, "similar": 1}
    for r in texts:
        g = group_key(r)
        entry = pool.setdefault(g, {"group": g, "row": r, "score": r["score"], "tmatch": None, "tscore": 0.0})
        if entry["score"] is None and r["score"] is not None:
            entry["score"] = r["score"]
        better = (order[r["tmatch"]], float(r["tscore"])) > (order.get(entry["tmatch"], 0), float(entry["tscore"]))
        if better:
            entry.update(tmatch=r["tmatch"], tscore=float(r["tscore"]), row=r)
    ranked = sorted(pool.values(), key=lambda e: (-_rank(e), -e["row"]["id"]))
    top = _rank(ranked[0]) if ranked else 0.0
    for e in ranked:
        e["match"] = e["tmatch"] or "meaning"  # The score says how close the meaning is; this, what the words did.
        e["strength"] = _strength(e, top)
    return ranked


async def list_groups(user_id: str, settings, q: str = "", limit: int = 20, offset: int = 0,
                      vector_space: str = "all", kind: str = "all") -> dict:
    """A page of this user's memories (cards) and a summary; `vector_space` filters by a model ("active": the
    active one's, "all": every model's). Without `q`: newest first. With `q`: by their best
    part's meaning score plus a text-match bonus (as the tool ranks), each with `match` (meaning, text, similar:
    what the words did; `score` says the meaning), `score` and `strength` (strong, medium, weak: next to the best result)."""
    q = (q or "").strip()
    kind = kind if kind in KINDS else "all"
    vector_space = vector_space or "all"
    reason, vector = None, None
    if q:
        space, vector, reason = await memory._query_vector(q, settings)
    else:
        space = await memory.current_space(settings)
    if vector_space == "active":  # The page's default: whichever model is active now.
        vector_space = space or "all"
    prefer = vector_space if vector_space != "all" else space
    conn = await memory._connect()
    more, search = False, {}
    try:
        await memory.ensure_memory_table(conn)
        if q:
            searchable = vector is not None and vector_space in ("all", space)
            meaning = []
            if searchable:
                params = [user_id, space, vector]
                extra = _filters("all", kind, params)
                meaning = [dict(r) for r in await conn.fetch(
                    f"select * from (select distinct on ({GROUP_KEY}) {_ROW}, 1 - (embedding <=> $3::vector) as score "
                    f"from orion_memory where user_id = $1 and vector_space = $2{extra} "
                    f"order by {GROUP_KEY}, embedding <=> $3::vector, id) best order by score desc, id desc "
                    f"limit {offset + limit + 1}", *params)]
            texts = await memory._text_rows(conn, user_id, q, vector_space, kind, space if searchable else None,
                                            vector if searchable else None)
            ranked = merge_matches(meaning, texts)
            more = len(ranked) > offset + limit
            picked = ranked[offset:offset + limit]
            search = {e["group"]: e for e in picked}
            groups = [e["group"] for e in picked]
            matching = len(ranked)
        else:
            params = [user_id]
            extra = _filters(vector_space, kind, params)
            groups = [r["g"] for r in await conn.fetch(
                f"select {GROUP_KEY} as g, max(created_at) as at from orion_memory where user_id = $1{extra} "
                f"group by g order by at desc, g desc limit {int(limit)} offset {int(offset)}", *params)]
            matching = await conn.fetchval(
                f"select count(distinct {GROUP_KEY}) from orion_memory where user_id = $1{extra}", *params)
            more = offset + len(groups) < int(matching or 0)
        members = await _group_rows(conn, user_id, groups)
        summary = await _summary(conn, user_id, space)
    finally:
        await conn.close()
    cards = []
    for g in groups:
        rows = members.get(g)
        if not rows:
            continue
        entry = search.get(g)
        matched = None
        if entry:
            key = content_key(entry["row"])
            shown = prefer_space([r for r in rows if content_key(r) == key], prefer)
            matched = shown[0]["id"] if shown else entry["row"]["id"]
        item = card(g, rows, prefer, space, matched=matched)
        if entry:
            item.update(match=entry["match"], strength=entry["strength"], matched_part=matched,
                        score=None if entry["score"] is None else round(float(entry["score"]), 4))
        cards.append(item)
    result = {"groups": cards, "matching": int(matching or 0), "more": more, "summary": summary}
    if q:
        result["semantic"] = vector is not None
        if reason:
            result["semantic_reason"] = reason
    return result


async def _summary(conn, user_id: str, space) -> dict:
    total = await conn.fetchval(f"select count(distinct {GROUP_KEY}) from orion_memory where user_id = $1", user_id)
    per_space = {r["key"]: int(r["n"]) for r in await conn.fetch(
        f"select vector_space as key, count(distinct {GROUP_KEY}) as n from orion_memory where user_id = $1 "
        "group by vector_space", user_id)}
    per_kind = {r["key"]: int(r["n"]) for r in await conn.fetch(
        f"select kind as key, count(distinct {CONTENT_KEY}) as n from orion_memory where user_id = $1 group by kind",
        user_id)}
    summary = {"total": int(total or 0), "per_vector_space": per_space, "per_kind": per_kind,
               "current_vector_space": space, "not_searchable": int(total or 0), "movable": {}, "copied": {}}
    if space:
        summary["not_searchable"] = int(await conn.fetchval(
            f"select count(*) from (select {GROUP_KEY} from orion_memory where user_id = $1 group by 1 "
            "having bool_and(vector_space <> $2)) t", user_id, space) or 0)
        has_copy = ("exists (select 1 from orion_memory c where c.user_id = r.user_id and c.vector_space = $2 "
                    "and coalesce(c.copied_from, c.id) = coalesce(r.copied_from, r.id))")
        for name, negate in (("movable", "not "), ("copied", "")):
            summary[name] = {row["key"]: int(row["n"]) for row in await conn.fetch(
                f"select vector_space as key, count(distinct coalesce(r.copied_from, r.id)) as n from orion_memory r "
                f"where r.user_id = $1 and r.vector_space <> $2 and {negate}{has_copy} group by vector_space",
                user_id, space)}
    from orion.kernel.memory_move import job_status
    summary["job"] = await job_status(conn, user_id)
    return summary


async def group_detail(user_id: str, group: str, settings) -> dict | None:
    """The whole memory for the detail panel: every part (full text, the spaces it is in) and its source chat."""
    space = await memory.current_space(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        rows = (await _group_rows(conn, user_id, [group])).get(group)
        if not rows:
            return None
        item = card(group, rows, space, space, full=True)
        chat_id = item.pop("source_chat_id")
        item["source_chat"] = None
        if chat_id:
            await registry._ensure_tables(conn)
            chat = await conn.fetchrow("select title from orion_chats where id = $1 and user_id = $2", chat_id, user_id)
            item["source_chat"] = {"id": chat_id, "exists": chat is not None, "title": chat["title"] if chat else None}
    finally:
        await conn.close()
    return item


async def delete_groups(user_id: str, groups: list[str]) -> list[dict]:
    """Delete whole memories (every part, in every space); returns the deleted rows."""
    groups = [str(g)[:64] for g in groups][:200]
    if not groups:
        return []
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            return await memory.delete_rows(conn, user_id, f"{GROUP_KEY} = any($2::text[])", groups)
    finally:
        await conn.close()


async def delete_copied(user_id: str, vector_space: str, settings) -> int:
    """Remove another model's vectors from the memories that have the active model's too (after a move); a memory
    with only that model's vectors keeps them. Returns how many vectors (rows) went."""
    space = await memory.current_space(settings)
    if not space:
        raise memory.MemoryUnavailable(memory.SETUP_MESSAGE)
    if vector_space == space:
        raise ValueError("Etkin modelin vektörleri kaldırılamaz.")
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        async with conn.transaction():
            rows = await memory.delete_rows(
                conn, user_id, "vector_space = $2 and exists (select 1 from orion_memory c where c.user_id = $1 "
                               "and c.vector_space = $3 and coalesce(c.copied_from, c.id) = "
                               "coalesce(orion_memory.copied_from, orion_memory.id))", vector_space, space)
    finally:
        await conn.close()
    return len(rows)


# --- Map ------------------------------------------------------------------------------------------------------------

MAP_MIN_SIM = 0.05  # A pair less similar than this is never linked, whatever the space's spread.


def map_floor(mean: float | None, pairs: int) -> float:
    """The similarity from which two memories of a space are linked: the space's typical pair (the mean of all its
    pairs), so only pairs less alike than usual (unrelated) are dropped. Each model spreads its similarities
    differently, so a fixed number would not do. Too few pairs to tell: MAP_MIN_SIM."""
    if mean is None or pairs < 3:
        return MAP_MIN_SIM
    return max(MAP_MIN_SIM, float(mean))


def _label(item: dict) -> str:
    label = item.get("title") if item["source_kind"] == "file" else None
    label = label or item.get("text") or next((p["text"] for p in item["parts"]), "") or ""
    return " ".join(label.split())


async def map_graph(user_id: str, settings, k: int = 5, limit: int = MAP_NODES, vector_space: str = "") -> dict:
    """The newest `limit` memories of a vector space (the active model's unless `vector_space` names another) as
    nodes and, per node, edges to its `k` nearest others (cosine of the memories' mean vectors) at or above the
    space's floor (map_floor), all computed by pgvector. Each edge's `w` places it between the floor (0) and the
    space's most similar pair (1)."""
    active = await memory.current_space(settings)
    space = vector_space or active
    if not space:
        raise memory.MemoryUnavailable(memory.SETUP_MESSAGE)
    limit, k = max(1, min(int(limit), MAP_NODES)), max(1, min(int(k), 10))
    picked = (f"select {GROUP_KEY} as g, avg(embedding) as v, max(created_at) as at from orion_memory "
              f"where user_id = $1 and vector_space = $2 group by 1 order by at desc, g desc limit $3")
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        total = await conn.fetchval(f"select count(distinct {GROUP_KEY}) from orion_memory where user_id = $1 "
                                    "and vector_space = $2", user_id, space)
        groups = [r["g"] for r in await conn.fetch(f"select g from ({picked}) p", user_id, space, limit)]
        # Every pair once (<= 45k at 300 nodes): the space's own spread.
        stats = await conn.fetchrow(
            f"with p as materialized ({picked}) select avg(s) as mean, stddev_pop(s) as sd, max(s) as top, "
            f"count(*) as n from (select 1 - (a.v <=> b.v) as s from p a join p b on a.g < b.g) t",
            user_id, space, limit)
        floor = map_floor(stats["mean"], int(stats["n"] or 0))
        edges = await conn.fetch(
            f"with p as materialized ({picked}) select a.g as a, n.g as b, n.sim from p a cross join lateral "
            f"(select b.g, 1 - (a.v <=> b.v) as sim from p b where b.g <> a.g order by a.v <=> b.v limit $4) n "
            f"where n.sim >= $5", user_id, space, limit, k, floor)
        members = await _group_rows(conn, user_id, groups)
    finally:
        await conn.close()
    nodes = []
    for g in groups:
        rows = members.get(g)
        if not rows:
            continue
        item = card(g, rows, space, active)
        thumb = next((p["id"] for p in item["parts"] if p["kind"] == "image" and p.get("media")), None)
        nodes.append({"group_id": g, "kind": item["kind"], "counts": item["counts"], "label": _label(item)[:80],
                      "thumb": thumb, "created_at": item["created_at"], "part_count": item["part_count"]})
    pairs: dict[tuple[str, str], float] = {}
    for e in edges:
        key = (min(e["a"], e["b"]), max(e["a"], e["b"]))
        pairs[key] = max(pairs.get(key, -1.0), round(float(e["sim"]), 4))
    top = float(stats["top"]) if stats["top"] is not None else 1.0
    span = max(top - floor, 1e-6)

    def rounded(value):
        return None if value is None else round(float(value), 4)
    return {"space": space, "active": space == active, "nodes": nodes, "total": int(total or 0),
            "capped": int(total or 0) > len(nodes), "floor": round(floor, 4),
            "spread": {"pairs": int(stats["n"] or 0), "mean": rounded(stats["mean"]), "sd": rounded(stats["sd"]),
                       "max": rounded(stats["top"])},
            "edges": [{"a": a, "b": b, "sim": sim, "w": round(min(1.0, max(0.0, (sim - floor) / span)), 3)}
                      for (a, b), sim in sorted(pairs.items())]}


async def space_vectors(user_id: str, vector_space: str, settings, sample: int = 50) -> dict:
    """What removing `vector_space`'s vectors would do: the memories that also have the active model's vectors
    lose `vector_space` (they stay, searchable with the active model); those with only its vectors keep them."""
    space = await memory.current_space(settings)
    if not space:
        raise memory.MemoryUnavailable(memory.SETUP_MESSAGE)
    if vector_space == space:
        raise ValueError("Etkin modelin vektörleri kaldırılamaz.")
    has_active = ("exists (select 1 from orion_memory c where c.user_id = r.user_id and c.vector_space = $3 "
                  "and coalesce(c.copied_from, c.id) = coalesce(r.copied_from, r.id))")
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        counts = await conn.fetchrow(
            f"select count(distinct {GROUP_KEY}) filter (where {has_active}) as memories, "
            f"count(*) filter (where {has_active}) as vectors, "
            f"count(distinct {GROUP_KEY}) filter (where not {has_active}) as kept "
            f"from orion_memory r where user_id = $1 and vector_space = $2", user_id, vector_space, space)
        groups = [r["g"] for r in await conn.fetch(
            f"select {GROUP_KEY} as g, max(created_at) as at from orion_memory r where user_id = $1 "
            f"and vector_space = $2 and {has_active} group by 1 order by at desc limit $4",
            user_id, vector_space, space, sample)]
        members = await _group_rows(conn, user_id, groups)
    finally:
        await conn.close()
    items = []
    for g in groups:
        rows = members.get(g)
        if rows:
            item = card(g, rows, space, space)
            items.append({"group_id": g, "kind": item["kind"], "label": _label(item)[:120],
                          "created_at": item["created_at"]})
    return {"space": vector_space, "active": space, "memories": int(counts["memories"] or 0),
            "vectors": int(counts["vectors"] or 0), "kept": int(counts["kept"] or 0), "items": items}


# --- Context for the tools ------------------------------------------------------------------------------------------

GROUP_TEXT_CAP = 8000
CHAT_TEXT_CAP = 1500


async def _resolve_legacy_source(conn, user_id: str, rows: list[dict]) -> tuple[str | None, int | None]:
    """Where a chat save from before the source columns came from: the last message before the save, in this user's
    chats (the assistant chat first), that carries one of its files, else that holds the start of one of its texts.
    The found source is stored on the rows of the group, so this runs once."""
    def like(text: str) -> str:
        return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    order = sorted(rows, key=lambda r: (not r.get("media_id"), r["id"]))  # A file id is the surest probe.
    found = None
    for row in order:
        if row.get("media_id"):
            # The file and the words sent with it (the label's tail): the same photo sent in another chat is not
            # where this memory came from.
            where, values = "m.content_json like $2", [f"%{row['media_id']}%"]
            tail = row["text"].split(" — ", 1)[1].strip()[:60] if " — " in row["text"] else ""
            if len(tail) >= 4:
                where += " and m.plain_text like $4"
                values.append(like(tail))
        else:
            head = row["text"].split("\n\n", 1)[0]
            for prefix in ("Kullanıcı: ", "Asistan: "):
                head = head.removeprefix(prefix)
            probe = head.split("\n", 1)[0].strip()[:60]
            if len(probe) < 4:
                continue
            where, values = "m.plain_text like $2", [like(probe)]
        # The last such message before the save (the same photo may have been sent more than once).
        found = await conn.fetchrow(
            f"select m.chat_id, m.id from orion_chat_messages m join orion_chats c on c.id = m.chat_id "
            f"where c.user_id = $1 and {where} and (m.created_at is null or m.created_at <= $3) "
            f"order by c.assistant desc, m.id desc limit 1", user_id, values[0], row["created_at"], *values[1:])
        if found is not None:
            break
    if found is None:
        return None, None
    await conn.execute("update orion_memory set source_chat_id = $2, source_message_id = coalesce(source_message_id, $3) "
                       "where id = any($1::bigint[]) and source_chat_id is null",
                       [r["id"] for r in rows], found["chat_id"], found["id"])
    return found["chat_id"], found["id"]


def _readable(rows) -> list[dict]:
    from orion.kernel.chat_search import message_text
    found = []
    for r in rows:
        try:
            message = json.loads(r["content_json"])
        except ValueError:
            continue
        text = message_text(message)
        if text:
            found.append({"id": r["id"], "role": message.get("role"), "text": text[:CHAT_TEXT_CAP],
                          "date": memory.local_time(r["created_at"])})
    return found


async def source_chat(conn, user_id: str, row: dict, count: int = 6, where: str = "around",
                      group: list[dict] | None = None) -> dict:
    """The conversation a memory row was saved from: `count` readable messages around the saved one ("around") or
    the chat's first ones ("start"). {"state": ok | unknown | deleted | cleared, "title", "messages"}."""
    chat_id, message_id = row.get("source_chat_id"), row.get("source_message_id")
    if not chat_id and row.get("source_kind") == "chat_save":
        chat_id, message_id = await _resolve_legacy_source(conn, user_id, group or [row])
    if not chat_id:
        return {"state": "unknown"}
    chat = await conn.fetchrow("select title from orion_chats where id = $1 and user_id = $2", chat_id, user_id)
    if chat is None:
        return {"state": "deleted"}
    window = count * 4  # Tool calls and system messages between them are not shown.
    columns = "id, content_json, created_at"
    if where == "start" or message_id is None:
        messages = _readable(await conn.fetch(
            f"select {columns} from orion_chat_messages where chat_id = $1 order by id limit $2", chat_id, window))[:count]
        if not messages:
            return {"state": "cleared", "title": chat["title"]}
        return {"state": "ok", "title": chat["title"], "messages": messages}
    after = _readable(await conn.fetch(
        f"select {columns} from orion_chat_messages where chat_id = $1 and id >= $2 order by id limit $3",
        chat_id, message_id, window))
    if not after or after[0]["id"] != message_id:
        if not await conn.fetchval("select 1 from orion_chat_messages where chat_id = $1 and id = $2",
                                   chat_id, message_id):
            return {"state": "cleared", "title": chat["title"]}
    before = _readable(await conn.fetch(
        f"select {columns} from orion_chat_messages where chat_id = $1 and id < $2 order by id desc limit $3",
        chat_id, message_id, window))[::-1]
    lead = min(len(before), count // 2)
    messages = before[len(before) - lead:] + after[:count - lead]
    for message in messages:
        message["saved"] = message["id"] == message_id
    return {"state": "ok", "title": chat["title"], "messages": messages}


async def memory_context(user_id: str, row_id: int, settings, with_chat: bool = False, count: int = 6,
                         where: str = "around", start: int = 0) -> dict | None:
    """A memory row's whole group (each part's full text from part `start`, within GROUP_TEXT_CAP) and, with
    `with_chat`, the conversation it was saved from. None for a row this user does not have."""
    space = await memory.current_space(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        row = await conn.fetchrow(f"select {_ROW} from orion_memory where user_id = $1 and id = $2", user_id, int(row_id))
        if row is None:
            return None
        row = dict(row)
        group = group_key(row)
        rows = (await _group_rows(conn, user_id, [group])).get(group) or [row]
        parts = prefer_space(rows, space)
        shown, used, rest = [], 0, 0
        for index, part in enumerate(parts):
            if index < start:
                continue
            if shown and used + len(part["text"]) > GROUP_TEXT_CAP:
                rest = len(parts) - index
                break
            used += len(part["text"])
            shown.append({**_part(part, full=True), "index": index + 1})
        result = {"group": group, "row": row, "parts": shown, "part_count": len(parts), "rest": rest,
                  "source_kind": rows[0]["source_kind"], "title": rows[0]["source_chat_title"],
                  "date": memory.local_time(min(r["created_at"] for r in rows))}
        if with_chat:
            await registry._ensure_tables(conn)
            # A part saved with this row knows the chat when this one does not (an old row that was copied).
            anchor = row if row.get("source_chat_id") else next((r for r in rows if r.get("source_chat_id")), row)
            result["chat"] = await source_chat(conn, user_id, anchor, count, where, rows)
        return result
    finally:
        await conn.close()
