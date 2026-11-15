"""Search measured against the query's own distribution (no fixed similarity number: an image's similarities against
text sit at 0.2-0.4, a text's at 0.1-0.7, and every model has its own scale).

- Above average: how far a result's score stands above what this query scores against every memory of the user.
  A neutral cue beside the score, never a verdict: results are neither hidden nor called "no match".
- Exclusions ("benzemesin" inputs: texts or files): memories whose similarity to such an input stands clearly out
  of that input's own distribution (z >= EXCLUDE_Z) are left out, and so are those whose text contains an
  excluded text.
"""
from __future__ import annotations

import statistics

from orion.kernel import memory

EXCLUDE_Z = 2.0  # Standing this many spreads above a "benzemesin" input's mean against all memories: out.
MIN_POOL = 8  # Fewer memories than this: too few to measure (no cue, nothing left out by meaning).
SPREAD_FLOOR = 0.02
CAPTION_FOLD = memory.FOLD_SQL.replace("(text,", "(coalesce(caption, ''),")


def calibrate(scores: list[float]) -> tuple[float, float] | None:
    """(mean, spread) of a query's best similarity per memory; None when too few memories to tell."""
    if len(scores) < MIN_POOL:
        return None
    return statistics.fmean(scores), max(statistics.pstdev(scores), SPREAD_FLOOR)


def standing(score, stats) -> float | None:
    """How many spreads `score` stands above the query's mean (None: cannot tell)."""
    if stats is None or score is None:
        return None
    return (float(score) - stats[0]) / stats[1]


async def group_scores(conn, user_id: str, space: str, vector: str, kind: str = "all") -> dict[str, float]:
    """Each memory's best similarity to `vector` in `space` (every memory of the user, pieces and whole vectors)."""
    from orion.kernel.memory_groups import GROUP_KEY, _filters
    params = [user_id, space, vector]
    extra = _filters("all", kind, params)
    rows = await conn.fetch(
        f"select {GROUP_KEY} as g, max(1 - (embedding <=> $3::vector)) as s from orion_memory "
        f"where user_id = $1 and vector_space = $2{extra} group by g", *params)
    return {r["g"]: float(r["s"]) for r in rows}


async def keyword_groups(conn, user_id: str, keyword: str) -> set[str]:
    """The memories whose text or caption contains `keyword` (Turkish folding, a plain substring: no meaning)."""
    from orion.kernel.memory_groups import GROUP_KEY
    folded = memory.fold(keyword)
    if not folded:
        return set()
    like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return {r["g"] for r in await conn.fetch(
        f"select distinct {GROUP_KEY} as g from orion_memory where user_id = $1 and "
        f"({memory.FOLD_SQL} like $2 or {CAPTION_FOLD} like $2)", user_id, like)}


async def excluded_groups(conn, user_id: str, space, terms: list[str], settings,
                          vectors: list[str] | None = None) -> set[str]:
    """The memories a search leaves out for its "benzemesin" inputs: close to one by meaning (z >= EXCLUDE_Z against
    that input's own distribution: a text's query vector or a file's vector) or, for a text, containing it in their
    text or caption."""
    from orion.kernel.memory_groups import GROUP_KEY
    out: set[str] = set()
    found = list(vectors or [])
    for term in terms:
        folded = memory.fold(term)
        like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        for r in await conn.fetch(
                f"select distinct {GROUP_KEY} as g from orion_memory where user_id = $1 and "
                f"({memory.FOLD_SQL} like $2 or {CAPTION_FOLD} like $2)", user_id, like):
            out.add(r["g"])
        if space:
            _, vector, _ = await memory._query_vector(term, settings)
            if vector is not None:
                found.append(vector)
    if not space:
        return out
    for vector in found:
        scores = await group_scores(conn, user_id, space, vector)
        stats = calibrate(list(scores.values()))
        for g, s in scores.items():
            z = standing(s, stats)
            if z is not None and z >= EXCLUDE_Z:
                out.add(g)
    return out
