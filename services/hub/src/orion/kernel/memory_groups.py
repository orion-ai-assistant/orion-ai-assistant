"""Memory cards: the rows saved together (`group_id`: one chat save, one note, one file) shown and handled as one
memory, on the page, on the map and in the tools. A row's copies in other vector spaces (memory_move) share its
group and its content key, so a memory is listed once and each of its parts once, the copy in the preferred space
(the active model's, or the filtered one's) standing for the others."""
from __future__ import annotations

import json
import logging

from orion.kernel import memory, registry
from orion.kernel.memory import (CONTENT_KEY, KINDS, content_key, group_key, item_positions, order_key, pieces_only,
                                 prefer_space)

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
        "media_seconds, group_id, copied_from, source_chat_id, source_message_id, item_id, whole, span_start, span_end, "
        "track, caption, item_order")
ITEM_KEY = f"coalesce(item_id, {CONTENT_KEY}::text)"


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

def _part(r: dict, spaces: list[str] | None = None, full: bool = False,
          position: tuple[int, int] | None = None) -> dict:
    text = r["text"] if full or r["kind"] == "text" else r["text"][:200]
    part = {"id": r["id"], "kind": r["kind"], "text": text, "vector_space": r["vector_space"],
            "source_kind": r.get("source_kind"), "item_id": r.get("item_id") or f"r{content_key(r)}"}
    if r.get("media_id"):
        seconds = r.get("media_seconds")
        part.update(media=True, media_mime=r.get("media_mime"),
                    media_seconds=None if seconds is None else round(float(seconds), 1))
    if r.get("span_start") is not None:
        part.update(span_start=round(float(r["span_start"]), 2), span_end=round(float(r["span_end"]), 2))
    if r.get("track"):
        part["track"] = r["track"]
    if r.get("source_kind") == "file" and r["kind"] == "text" and (r.get("source_chat_title") or "").strip():
        part["file_name"] = r["source_chat_title"].strip()  # A file in a chat memory keeps its own name.
    if r.get("caption"):
        part["caption"] = r["caption"]
    if r.get("created_at") is not None:  # Each part its own time: parts can be added to a memory later.
        part["created_at"] = r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"])
    if position and position[1] > 1:
        part.update(piece=position[0], pieces=position[1])
    if spaces is not None:
        part["spaces"] = spaces
    return part


def item_counts(parts: list[dict]) -> dict:
    """Items per kind (a text file, a note, a video... each once, however many pieces it has)."""
    items = {}
    for p in parts:
        items.setdefault(p.get("item_id") or f"r{content_key(p)}", p["kind"])
    counts = {kind: sum(1 for k in items.values() if k == kind) for kind in KINDS}
    return {kind: n for kind, n in counts.items() if n}


def card(group: str, rows: list[dict], prefer, active, full: bool = False, matched: int | None = None) -> dict:
    """One memory as the page shows it. `full`: every part with its whole text and the spaces it is in (the detail
    panel); else the text of one text part (the matched one, else the first) and the first piece of each media
    item. Items' whole vectors are never parts."""
    parts = pieces_only(prefer_space(rows, prefer))
    if not parts:  # Only whole vectors left (should not happen): show them rather than nothing.
        parts = prefer_space(rows, prefer)
    first = min(rows, key=lambda r: r["id"])
    counts = item_counts(parts)
    spaces = sorted({r["vector_space"] for r in rows})
    active_keys = {content_key(r) for r in rows if r["vector_space"] == active}
    positions = item_positions(parts)
    item = {"group_id": group, "kind": next(iter(counts)) if len(counts) == 1 else "mixed", "counts": counts,
            "part_count": len(parts), "source_kind": first["source_kind"], "title": first["source_chat_title"],
            "created_at": min(r["created_at"] for r in rows).isoformat(), "vector_spaces": spaces,
            "searchable": bool(active_keys), "missing_in_active": sum(1 for p in parts if content_key(p) not in active_keys)}
    texts = [p for p in parts if p["kind"] == "text"]
    if full:
        in_spaces: dict[int, list[str]] = {}
        for r in rows:
            in_spaces.setdefault(content_key(r), []).append(r["vector_space"])
        item["parts"] = [_part(p, sorted(set(in_spaces[content_key(p)])), True, positions.get(p["id"])) for p in parts]
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
    media, seen = [], set()
    for p in parts:
        key = p.get("item_id") or f"r{content_key(p)}"
        if p["kind"] == "text" or key in seen or (p["kind"] == "video" and p.get("track") == "audio"):
            continue
        seen.add(key)
        media.append(_part(p, position=positions.get(p["id"])))
    item["parts"] = media[:PREVIEW_MEDIA]
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


EACH_POOL = 200  # Memories taken per input before fusing (mode "each").
SPREAD_FLOOR = 0.02  # The least spread an input's similarities are taken to have (a tiny memory pool).
EACH_STRONG, EACH_MEDIUM = 3.0, 2.0  # Standing out from an input's own pool by this many spreads.


def fuse_inputs(lists: list[list[dict]], labels: list[str] | None = None) -> list[dict]:
    """Several inputs searched one by one ("each"), fused as OR: a memory ranks by the input it stands out for
    most. Each input's similarities are calibrated against that input's own pool (z = (score - mean) / spread),
    since an image's and a text's similarities live on different scales and an input that matches nothing
    (an unrelated picture) has a flat pool where nothing stands out. Rank fusion summed every input's ranks, so
    such an input acted like AND and pushed a strong single-input match down. Each memory keeps the row and raw
    score of its winning input (the card's number), `via` (that input's label) and `fused` (the z, the order)."""
    import statistics
    pool: dict[str, dict] = {}
    for index, rows in enumerate(lists):
        if not rows:
            continue
        scores = [float(r["score"]) for r in rows]
        mean = statistics.fmean(scores)
        spread = max(statistics.pstdev(scores) if len(scores) > 1 else 0.0, SPREAD_FLOOR)
        label = labels[index] if labels and index < len(labels) else f"girdi {index + 1}"
        for r in rows:
            z = (float(r["score"]) - mean) / spread
            g = group_key(r)
            entry = pool.get(g)
            if entry is None or z > entry["fused"]:
                pool[g] = {**r, "fused": z, "via": label, "above": float(r["score"]) - mean}
    return sorted(pool.values(), key=lambda e: (-e["fused"], -e["id"]))


def merge_matches(meaning: list[dict], texts: list[dict], keep_order: bool = False) -> list[dict]:
    """Meaning and text matches per memory, best first: {"group", "row" (the part that matched), "score", "tmatch",
    "tscore", "match" (text, similar or meaning), "strength"}. A memory ranks by its best row (memory._ranked's
    measure): a piece, or an item's whole vector."""
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
    if keep_order:  # Already fused (fuse_inputs): the order stands, strength by how far it stands out.
        ranked = list(pool.values())
        for e in ranked:
            z = float(e["row"].get("fused") or 0.0)
            e["match"] = e["tmatch"] or "meaning"
            e["via"] = e["row"].get("via")
            e["strength"] = "strong" if z >= EACH_STRONG else ("medium" if z >= EACH_MEDIUM else "weak")
        return ranked
    ranked = sorted(pool.values(), key=lambda e: (-_rank(e), -e["row"]["id"]))
    top = _rank(ranked[0]) if ranked else 0.0
    for e in ranked:
        e["match"] = e["tmatch"] or "meaning"  # The score says how close the meaning is; this, what the words did.
        e["strength"] = _strength(e, top)
    return ranked


def text_hit(q: str, text: str) -> tuple[str | None, float]:
    """What the words of `q` do in `text`: ("text", 1) for a substring (Turkish folding), ("similar", s) for a near
    spelling (memory.word_similarity >= FUZZY_MIN_PY), else (None, 0)."""
    folded, target = memory.fold(q), memory.fold(text or "")
    if not folded:
        return None, 0.0
    if folded in target:
        return "text", 1.0
    similarity = memory.word_similarity(folded, target)
    return ("similar", similarity) if similarity >= memory.FUZZY_MIN_PY else (None, 0.0)


def score_parts(parts: list[dict], scores: dict[int, float], q: str = "") -> dict:
    """Each part's own match for a search: {"parts": {id: {"score", "match", "matched", "best"}}, "best": id,
    "matched": how many parts matched, "whole_score"}. A part matched when its words hit the query or its meaning
    is close to its memory's best (STRONG share of the best part's score, and not weak for its kind: WEAK_SCORE).
    `parts` are the memory's rows in one space each (prefer_space), whole vectors included; the best part is a
    piece (an item's whole vector ranks the memory but is not a part)."""
    if len(q or "") > memory.TEXT_MATCH_CHARS:  # A pasted passage: meaning only (list_groups does the same).
        q = ""
    found, best, best_rank, whole = {}, None, None, None
    pieces = [p for p in parts if not p.get("whole")]
    for p in parts:
        if p.get("whole") and p["id"] in scores:
            whole = max(whole or -1.0, scores[p["id"]])
    top = max((scores[p["id"]] for p in pieces if p["id"] in scores), default=None)
    for p in pieces:
        score = scores.get(p["id"])
        hit, tscore = text_hit(q, p["text"]) if q else (None, 0.0)
        if q and p.get("caption") and hit is None:
            hit, tscore = text_hit(q, p["caption"])
        rank = (score or 0.0) + {"text": memory.TEXT_BONUS, "similar": 0.8 * memory.TEXT_BONUS}.get(hit, 0.0) \
            + 0.01 * tscore
        close = (score is not None and top is not None and top > 0 and score >= STRONG * top
                 and score >= WEAK_SCORE.get(p["kind"], 0.25 if p["kind"] == "text" else 0.12))
        found[p["id"]] = {"score": None if score is None else round(float(score), 4), "match": hit or "meaning",
                          "matched": bool(hit) or close}
        if best_rank is None or rank > best_rank:
            best, best_rank = p["id"], rank
    if best is not None:
        found[best]["best"] = True
        found[best]["matched"] = True
    return {"parts": found, "best": best, "matched": sum(1 for v in found.values() if v["matched"]),
            "whole_score": None if whole is None else round(float(whole), 4)}


async def all_score(conn, user_id: str, group: str, space, vector) -> float | None:
    """The memory's score over all its content: the query's cosine with the mean of every piece vector it has in
    `space` (all items and tracks; whole vectors left out, they are means already). The mean's length does not change
    a cosine, so this is the same as against the normalized mean, as stored whole vectors are made; pgvector
    averages in the database, nothing is read out."""
    if vector is None or not space:
        return None
    where = "group_id = $2" if not group.startswith("r") else "group_id is null and id = $2::text::bigint"
    key = group if not group.startswith("r") else group[1:]
    value = await conn.fetchval(
        f"select 1 - (avg(embedding) <=> $3::vector) from orion_memory where user_id = $1 and vector_space = $4 "
        f"and not whole and {where}", user_id, key, vector, space)
    return None if value is None else float(value)


async def _scores(conn, user_id: str, groups: list[str], space, vector) -> dict[int, float]:
    """The meaning score of every row of these memories in the space `space`."""
    if not groups or vector is None or not space:
        return {}
    real = [g for g in groups if not g.startswith("r")]
    loose = [int(g[1:]) for g in groups if g.startswith("r") and g[1:].isdigit()]
    return {r["id"]: float(r["s"]) for r in await conn.fetch(
        "select id, 1 - (embedding <=> $4::vector) as s from orion_memory where user_id = $1 and vector_space = $5 "
        "and (group_id = any($2::text[]) or (group_id is null and id = any($3::bigint[])))",
        user_id, real, loose, vector, space)}


def _in_space(rows: list[dict], space) -> list[dict]:
    """A memory's rows one per content, in `space` when they have a copy there, whole vectors included."""
    return prefer_space(rows, space)


async def list_groups(user_id: str, settings, q: str = "", limit: int = 20, offset: int = 0,
                      vector_space: str = "all", kind: str = "all", vector: str | None = None,
                      vector_space_of_query: str | None = None, vectors: list[str] | None = None,
                      mode: str = "together", input_labels: list[str] | None = None,
                      exclude: list[str] | None = None, exclude_vectors: list[str] | None = None,
                      leave_out: set[str] | None = None, only_groups: set[str] | None = None,
                      meaning: bool = True) -> dict:
    """A page of this user's memories (cards) and a summary; `vector_space` filters by a model ("active": the
    active one's, "all": every model's). Without `q` or `vector`: newest first. With `q`: by their best row's
    meaning score (a piece or an item's whole vector) plus a text-match bonus (as the tool ranks), each with `match`
    (meaning, text, similar: what the words did; `score` says the meaning), `score`, `strength` (strong, medium,
    weak: next to the best result), `best_part` (the piece that matched best, its place and time) and
    `matched_parts`. `vector` (with its space): a query vector already made (a search by file). `vectors` with
    mode "each": several inputs searched one by one and fused as OR (fuse_inputs; `input_labels` name them); the cards' scores are each part's best
    score against any input. ("together" is the caller's one combined vector in `vector`.) `leave_out`: memories
    never listed (the memory a search by one of its own parts started from). `only_groups`: only these memories
    (a keyword's). `meaning` False: words only (a keyword search)."""
    q = (q or "").strip()
    kind = kind if kind in KINDS else "all"
    vector_space = vector_space or "all"
    reason = None
    from orion.kernel import memory_filters as mf
    negatives = [t.strip() for t in exclude or [] if t.strip()][:5]
    negative_vectors = list(exclude_vectors or [])[:5]
    excluding = bool(negatives or negative_vectors)
    leave_out = set(leave_out or ())
    searching = bool(q) or vector is not None
    if vector is not None:
        space = vector_space_of_query or await memory.current_space(settings)
    elif q and meaning:
        space, vector, reason = await memory._query_vector(q, settings)
    else:
        space = await memory.current_space(settings)
    if q and not meaning and vector is None:
        reason = None
    if vector_space == "active":  # The page's default: whichever model is active now.
        vector_space = space or "all"
    prefer = vector_space if vector_space != "all" else space
    conn = await memory._connect()
    more, search, scores = False, {}, {}
    try:
        await memory.ensure_memory_table(conn)
        if searching:
            searchable = vector is not None and vector_space in ("all", space)
            meaning = []
            if searchable and vectors and len(vectors) > 1 and mode == "each":
                lists = []
                for one in vectors:
                    params = [user_id, space, one]
                    extra = _filters("all", kind, params)
                    lists.append([dict(r) for r in await conn.fetch(
                        f"select * from (select distinct on ({GROUP_KEY}) {_ROW}, 1 - (embedding <=> $3::vector) "
                        f"as score from orion_memory where user_id = $1 and vector_space = $2{extra} "
                        f"order by {GROUP_KEY}, embedding <=> $3::vector, id) best order by score desc, id desc "
                        f"limit {EACH_POOL}", *params)])
                meaning = fuse_inputs(lists, input_labels)
            elif searchable:
                params = [user_id, space, vector]
                extra = _filters("all", kind, params)
                meaning = [dict(r) for r in await conn.fetch(
                    f"select * from (select distinct on ({GROUP_KEY}) {_ROW}, 1 - (embedding <=> $3::vector) as score "
                    f"from orion_memory where user_id = $1 and vector_space = $2{extra} "
                    f"order by {GROUP_KEY}, embedding <=> $3::vector, id) best order by score desc, id desc "
                    f"limit {offset + limit + 1 + len(leave_out) + (EACH_POOL if excluding else 0)}", *params)]
            texts = await memory._text_rows(conn, user_id, q, vector_space, kind, space if searchable else None,
                                            vector if searchable else None) if q and len(q) <= memory.TEXT_MATCH_CHARS else []
            each = bool(vectors and len(vectors) > 1 and mode == "each")
            ranked = merge_matches(meaning, texts, keep_order=each)
            excluded, dropped = 0, []
            if leave_out:
                ranked = [e for e in ranked if e["group"] not in leave_out]
            if only_groups is not None:
                ranked = [e for e in ranked if e["group"] in only_groups]
            if excluding:
                out = await mf.excluded_groups(conn, user_id, space if searchable else None, negatives, settings,
                                               negative_vectors)
                kept = [e for e in ranked if e["group"] not in out]
                dropped = [e for e in ranked if e["group"] in out][:10]
                excluded, ranked = len(ranked) - len(kept), kept
            # A neutral cue: how far each score is above this query's typical score against every memory.
            stats = None
            if searchable and not each:
                stats = mf.calibrate(list((await mf.group_scores(conn, user_id, space, vector, kind)).values()))
            for e in ranked:
                if each:
                    e["above"] = e["row"].get("above")
                elif stats is not None and e["score"] is not None:
                    e["above"] = float(e["score"]) - stats[0]
            more = len(ranked) > offset + limit
            picked = ranked[offset:offset + limit]
            search = {e["group"]: e for e in picked}
            groups = [e["group"] for e in picked]
            matching = len(ranked)
            if searchable and vectors and len(vectors) > 1 and mode == "each":
                for one in vectors:
                    for rid, value in (await _scores(conn, user_id, groups, space, one)).items():
                        scores[rid] = max(scores.get(rid, -1.0), value)
            elif searchable:
                scores = await _scores(conn, user_id, groups, space, vector)
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
        parts_match = None
        if entry:
            parts_match = score_parts(_in_space(rows, space), scores, q)
            row = entry["row"]
            if row.get("whole") or not parts_match["parts"].get(row["id"]):
                matched = parts_match["best"]
            else:
                key = content_key(row)
                shown = prefer_space([r for r in rows if content_key(r) == key], prefer)
                matched = shown[0]["id"] if shown else row["id"]
            if matched is None:
                matched = row["id"]
        item = card(g, rows, prefer, space, matched=matched)
        if entry:
            item.update(match=entry["match"], strength=entry["strength"], matched_part=matched,
                        score=None if entry["score"] is None else round(float(entry["score"]), 4),
                        matched_parts=max(1, parts_match["matched"]), via_whole=bool(entry["row"].get("whole")))
            if entry.get("above") is not None:
                item["above"] = round(float(entry["above"]), 4)  # Score minus this query's typical score.
            if entry.get("via"):
                item["via"] = entry["via"]  # Which input it matched ("metin", "görsel"): its score is that one's.
            best = _best_part(rows, matched, prefer)
            if best:
                item["best_part"] = best
        cards.append(item)
    result = {"groups": cards, "matching": int(matching or 0), "more": more, "summary": summary}
    if searching and excluding:
        result["excluded"] = excluded
        result["exclude_terms"] = negatives
        # What a "benzemesin" input took out, briefly (the best of them).
        result["excluded_list"] = [{"id": e["row"]["id"], "label": " ".join(e["row"]["text"].split())[:80]}
                                   for e in dropped]
    if searching:
        result["semantic"] = vector is not None
        if reason:
            result["semantic_reason"] = reason
    return result


def _best_part(rows: list[dict], part_id, prefer) -> dict | None:
    """Where the best piece sits: {"id", "index" (its place among the memory's parts), "piece", "pieces" (within its
    item), "span_start", "span_end", "kind", "track", "text" (a text piece's text, a media part's label): what a
    label or tooltip of the memory shows while searching}."""
    parts = pieces_only(prefer_space(rows, prefer))
    chosen = next((p for p in parts if p["id"] == part_id), None)
    if chosen is None:
        key = next((content_key(r) for r in rows if r["id"] == part_id), None)
        chosen = next((p for p in parts if content_key(p) == key), None)
    if chosen is None:
        return None
    position = item_positions(parts).get(chosen["id"], (1, 1))
    best = {"id": chosen["id"], "index": parts.index(chosen) + 1, "piece": position[0], "pieces": position[1],
            "kind": chosen["kind"], "text": chosen["text"] if chosen["kind"] == "text" else chosen["text"][:300]}
    if chosen.get("span_start") is not None:
        best.update(span_start=round(float(chosen["span_start"]), 2), span_end=round(float(chosen["span_end"]), 2))
    if chosen.get("track"):
        best["track"] = chosen["track"]
    return best


async def part_scores(user_id: str, group: str, settings, q: str = "", vector: str | None = None,
                      space: str | None = None) -> dict | None:
    """Each part's own match in one memory for the page's search (score_parts), by the query text `q` or a query
    vector already made (a search by file, with its `space`). None for a memory this user does not have."""
    q = (q or "").strip()
    if vector is None and q:
        space, vector, _ = await memory._query_vector(q, settings)
    elif space is None:
        space = await memory.current_space(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        rows = (await _group_rows(conn, user_id, [group])).get(group)
        if not rows:
            return None
        scores = await _scores(conn, user_id, [group], space, vector) if vector is not None else {}
        whole_memory = await all_score(conn, user_id, group, space, vector) if vector is not None else None
    finally:
        await conn.close()
    in_space = _in_space(rows, space)
    found = score_parts(in_space, scores, q)
    found["all_score"] = None if whole_memory is None else round(whole_memory, 4)
    # The page shows each part once (the copy in the preferred space): its match is the match of its content.
    by_content = {content_key(p): found["parts"].get(p["id"]) for p in in_space if p["id"] in found["parts"]}
    found["parts"] = {str(r["id"]): by_content[content_key(r)] for r in rows
                      if content_key(r) in by_content and by_content[content_key(r)] is not None}
    if found["best"] is not None:
        found["best_part"] = _best_part(rows, found["best"], space)
    return found


async def _summary(conn, user_id: str, space) -> dict:
    total = await conn.fetchval(f"select count(distinct {GROUP_KEY}) from orion_memory where user_id = $1", user_id)
    per_space = {r["key"]: int(r["n"]) for r in await conn.fetch(
        f"select vector_space as key, count(distinct {GROUP_KEY}) as n from orion_memory where user_id = $1 "
        "group by vector_space", user_id)}
    per_kind = {r["key"]: int(r["n"]) for r in await conn.fetch(
        f"select kind as key, count(distinct {ITEM_KEY}) as n from orion_memory where user_id = $1 and not whole "
        "group by kind",
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


DETAIL_PARTS = 60  # Parts per page of one long item read in the viewer (a payload cap).
LONG_ITEM = 12  # An item of more pieces shows as a preview in the panel (a rendering choice, not a quality one).
PREVIEW_PIECES = 3  # A long item's first pieces in the preview.


def preview_parts(parts: list[dict], around: list[int]) -> tuple[list[dict], dict[str, int]]:
    """The panel's parts: every part of short items; of a long item (more than LONG_ITEM pieces) its first
    PREVIEW_PIECES and each `around` piece (a search's matches) with one neighbour each side. Returns (parts,
    {item_id: its piece count} of the long items)."""
    by_item: dict[str, list[int]] = {}
    for index, p in enumerate(parts):
        by_item.setdefault(p["item_id"], []).append(index)
    keep, long_items = set(), {}
    wanted = set(around)
    for item_id, indexes in by_item.items():
        if len(indexes) <= LONG_ITEM:
            keep.update(indexes)
            continue
        long_items[item_id] = len(indexes)
        keep.update(indexes[:PREVIEW_PIECES])
        for k, index in enumerate(indexes):
            if parts[index]["id"] in wanted:
                keep.update(indexes[max(0, k - 1):k + 2])
    return [p for i, p in enumerate(parts) if i in keep], long_items


async def group_detail(user_id: str, group: str, settings, offset: int = 0, limit: int = DETAIL_PARTS,
                       around: list[int] | None = None, item_id: str | None = None) -> dict | None:
    """The memory for the detail panel: its parts (full text, the spaces they are in), a long item only as a
    preview (preview_parts; `long_items` says how many pieces each has), and its source chat. With `item_id`: a page
    of that item's pieces from `offset` ({"item_id", "parts", "total", "offset"}) for the viewer's lazy text."""
    space = await memory.current_space(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        rows = (await _group_rows(conn, user_id, [group])).get(group)
        if not rows:
            return None
        item = card(group, rows, space, space, full=True)
        parts = item["parts"]
        if item_id is not None:
            pieces = [p for p in parts if p["item_id"] == item_id]
            limit = max(1, min(int(limit), 500))
            offset = max(0, int(offset))
            return {"item_id": item_id, "parts": pieces[offset:offset + limit], "total": len(pieces),
                    "offset": offset}
        shown, long_items = preview_parts(parts, list(around or []))
        item.update(parts=shown, parts_total=len(parts), long_items=long_items)
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
              f"where user_id = $1 and vector_space = $2 and not whole group by 1 order by at desc, g desc limit $3")
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
                      "thumb": thumb, "created_at": item["created_at"], "part_count": item["part_count"],
                      "source_kind": item["source_kind"]})
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
                         where: str = "around", start: int = 0, neighbours: int = 0) -> dict | None:
    """A memory row's whole group (each part's full text from part `start`, within GROUP_TEXT_CAP) and, with
    `with_chat`, the conversation it was saved from. `neighbours` > 0: only the row's part and that many parts
    before and after it in its memory (the previous/next pieces of a long text, audio or video come first: the same
    item and track). An item's whole vector stands for its first piece. None for a row this user does not have."""
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
        parts = pieces_only(prefer_space(rows, space)) or prefer_space(rows, space)
        positions = item_positions(parts)
        indexed = list(enumerate(parts))
        if neighbours > 0:
            key = content_key(row)
            same = [(i, p) for i, p in indexed if row.get("item_id") and p.get("item_id") == row["item_id"]
                    and p.get("track") == row.get("track")]
            pool = same if len(same) > 1 else indexed
            at = next((k for k, (_, p) in enumerate(pool) if content_key(p) == key), 0)
            indexed = pool[max(0, at - neighbours):at + neighbours + 1]
        shown, used, rest = [], 0, 0
        for step, (index, part) in enumerate(indexed):
            if index < start:
                continue
            if shown and used + len(part["text"]) > GROUP_TEXT_CAP:
                rest = len(indexed) - step
                break
            used += len(part["text"])
            shown.append({**_part(part, full=True, position=positions.get(part["id"])), "index": index + 1,
                          "asked": content_key(part) == content_key(row)})
        result = {"group": group, "row": row, "parts": shown, "part_count": len(parts), "rest": rest,
                  "source_kind": rows[0]["source_kind"], "title": rows[0]["source_chat_title"],
                  "date": memory.local_time(min(r["created_at"] for r in rows)), "neighbours": neighbours}
        if with_chat:
            await registry._ensure_tables(conn)
            # A part saved with this row knows the chat when this one does not (an old row that was copied).
            anchor = row if row.get("source_chat_id") else next((r for r in rows if r.get("source_chat_id")), row)
            result["chat"] = await source_chat(conn, user_id, anchor, count, where, rows)
        return result
    finally:
        await conn.close()


# --- The chat model's search (the memory tools) ---------------------------------------------------------------------

async def row_vector(user_id: str, row_id: int, space: str | None) -> dict | None:
    """This user's memory row as a search query, the way a dropped file is one on the page: {"vector" (its embedding
    in `space`: the copy there when it was saved with another model; None when it has none), "group", "kind",
    "text"}. None for a row this user does not have."""
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        row = await conn.fetchrow("select id, kind, text, group_id, copied_from, vector_space, embedding::text as e "
                                  "from orion_memory where user_id = $1 and id = $2", user_id, int(row_id))
        if row is None:
            return None
        vector = row["e"] if row["vector_space"] == space else await conn.fetchval(
            "select embedding::text from orion_memory where user_id = $1 and vector_space = $2 "
            "and coalesce(copied_from, id) = $3 limit 1", user_id, space, row["copied_from"] or row["id"])
    finally:
        await conn.close()
    return {"vector": vector, "group": group_key(dict(row)), "kind": row["kind"], "text": row["text"]}


class QueryError(ValueError):
    """A query the search cannot run (an unknown id, an id without a vector of the active model): its message says
    which, in English, for the chat model."""


async def search_queries(user_id: str, settings, queries: list[dict], mode: str = "together", kind: str = "all",
                         vector_space: str = "all", limit: int = 10, offset: int = 0) -> dict:
    """The page's search for the chat model: several queries, each a text or a memory row id (that row's vector, as
    a dropped file), `like` true (results like it) or false (memories like it are left out); mode "together" (one
    query: the normalized mean of the like-queries) or "each" (each ranked on its own, fused as OR: fuse_inputs).
    The memory a like-id belongs to is not listed (it would come first, matching itself). Answers list_groups's
    page, plus "labels" (each like-query's label: '"text"' or 'id 12', the names `via` uses) and "left_out"
    (memories left out as the id-queries' own). No like-query: the newest memories (of `kind`)."""
    import json
    from orion.kernel.memory_pieces import mean_vector
    keywords = [q for q in queries if (q.get("keyword") or "").strip()]
    queries = [q for q in queries if not (q.get("keyword") or "").strip()]
    likes = [q for q in queries if q.get("like", True)]
    unlikes = [q for q in queries if not q.get("like", True)]
    only, out_words = None, set()
    if keywords:
        from orion.kernel import memory_filters as mf
        conn = await memory._connect()
        try:
            await memory.ensure_memory_table(conn)
            for k in keywords:
                found_groups = await mf.keyword_groups(conn, user_id, k["keyword"].strip()[:200])
                if k.get("like", True):
                    only = found_groups if only is None else only & found_groups
                else:
                    out_words |= found_groups
        finally:
            await conn.close()
    if keywords and not likes:
        # Keywords alone: their exact matches, by their words (no meaning).
        words = " ".join(k["keyword"].strip() for k in keywords if k.get("like", True))
        found = await list_groups(user_id, settings, words[:200], limit, offset, vector_space, kind,
                                  leave_out=out_words, only_groups=only if only is not None else None,
                                  meaning=False, exclude=[q["text"].strip()[:200] for q in unlikes
                                                          if (q.get("text") or "").strip()])
        found["labels"] = []
        return found
    if unlikes and not likes:
        raise QueryError("Add at least one query with like=true: like=false only leaves memories out of a search.")
    space = await memory.current_space(settings)
    leave_out: set[str] = set(out_words)
    exclude, exclude_vectors = [], []

    async def of_id(row_id: int) -> dict:
        found = await row_vector(user_id, row_id, space)
        if found is None:
            raise QueryError(f"There is no memory with id {row_id}.")
        if found["vector"] is None:
            raise QueryError(f"Memory {row_id} has no vector of the active embedding model; search for it by text.")
        leave_out.add(found["group"])
        return found

    for q in unlikes:
        if q.get("id") is not None:
            exclude_vectors.append((await of_id(int(q["id"])))["vector"])
        elif (q.get("text") or "").strip():
            exclude.append(q["text"].strip()[:200])
    texts = [q["text"].strip()[:memory.MAX_QUERY_CHARS] for q in likes
             if q.get("id") is None and (q.get("text") or "").strip()]
    words = ", ".join(texts)
    common = dict(vector_space=vector_space, kind=kind, exclude=exclude, exclude_vectors=exclude_vectors,
                  only_groups=only)
    if not likes:
        found = await list_groups(user_id, settings, "", limit, offset, vector_space, kind)
        found["labels"] = []
        return found
    if len(likes) == 1 and texts:  # One text: exactly the page's text search (meaning and its words).
        found = await list_groups(user_id, settings, texts[0], limit, offset, leave_out=leave_out, **common)
        found["labels"] = [json.dumps(texts[0][:60], ensure_ascii=False)]
        return found
    vectors, labels, reason = [], [], None
    for q in likes:
        if q.get("id") is not None:
            vectors.append(json.loads((await of_id(int(q["id"])))["vector"]))
            labels.append(f"id {int(q['id'])}")
            continue
        text = (q.get("text") or "").strip()[:memory.MAX_QUERY_CHARS]
        if not text:
            continue
        _, vector, why = await memory._query_vector(text, settings)
        if vector is None:
            reason = why
            continue
        vectors.append(json.loads(vector))
        labels.append(json.dumps(text[:60], ensure_ascii=False))
    if not vectors:  # Meaning search is off: the texts' words only.
        found = await list_groups(user_id, settings, words, limit, offset, leave_out=leave_out, **common)
        found["labels"] = []
        return found
    found = await list_groups(
        user_id, settings, words, limit, offset, vector=memory._vector(mean_vector(vectors)),
        vector_space_of_query=space, vectors=[memory._vector(v) for v in vectors],
        mode="each" if mode == "each" else "together", input_labels=labels, leave_out=leave_out, **common)
    found["labels"] = labels
    if reason:
        found["semantic_reason"] = reason
    return found


def readable_name(text: str, words: int = 6, limit: int = 48) -> str:
    """A file name from a text's first words ("Elif Kaya 34 yaşında İzmir Karşıyaka"): letters, digits and spaces
    only, cut at a word."""
    import re
    name = ""
    for word in re.sub(r"[^\w\s]", " ", text or "").split()[:words]:
        if len(name) + len(word) + 1 > limit:
            break
        name = f"{name} {word}".strip()
    return name


async def item_document(user_id: str, row_id: int, settings) -> dict | None:
    """The text item a memory row belongs to, as one document: {"name", "text" (its pieces joined in order: pieces
    are whole paragraphs, so this is the text that was saved), "pieces": [{"id", "text"}], "asked" (the row's place
    among them), "source_kind", "title"}. A text file added on the page is its pieces; a note or a chat save's text is
    its own item. None for a row this user does not have or one that is not text."""
    space = await memory.current_space(settings)
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        row = await conn.fetchrow(f"select {_ROW} from orion_memory where user_id = $1 and id = $2", user_id, int(row_id))
        if row is None or row["kind"] != "text":
            return None
        row = dict(row)
        group = group_key(row)
        rows = (await _group_rows(conn, user_id, [group])).get(group) or [row]
    finally:
        await conn.close()
    parts = pieces_only(prefer_space(rows, space)) or [row]
    item = row.get("item_id")
    pieces = [p for p in parts if p["kind"] == "text" and (p.get("item_id") == item if item else
                                                            content_key(p) == content_key(row))] or [row]
    key = content_key(row)
    asked = next((i for i, p in enumerate(pieces) if content_key(p) == key), 0)
    title = (row.get("source_chat_title") or "").strip()
    if row.get("source_kind") == "file" and title and "." in title[-6:]:
        name = title
    else:
        name = readable_name(pieces[0]["text"]) or f"Hafıza kaydı {row['id']}"
        name += ".txt"
    return {"name": name, "text": "\n\n".join(p["text"] for p in pieces),
            "pieces": [{"id": p["id"], "text": p["text"]} for p in pieces], "asked": asked,
            "source_kind": row.get("source_kind"), "title": title}
