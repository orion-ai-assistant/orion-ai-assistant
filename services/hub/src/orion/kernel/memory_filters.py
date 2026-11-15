"""Search honesty and exclusions, both measured against the query's own distribution (no fixed similarity number:
an image's similarities against text sit at 0.2-0.4, a text's at 0.1-0.7, and every model has its own scale).

- Noise: without a threshold the nearest memory always shows, even for a query nothing in memory resembles. A
  memory is a match when its best similarity stands clearly above what this query scores against every memory of
  the user (z = (score - mean) / spread >= NOISE_Z); below that it is "zayıf" and, when even the first one is,
  the answer says nothing similar was found.
- Negative terms: "-köpek", -"iki kelime" in the query (or `haric` in the tool) drop memories whose similarity to
  that term stands clearly out of the term's own distribution (z >= EXCLUDE_Z) or whose text contains it.
"""
from __future__ import annotations

import re
import statistics

from orion.kernel import memory

NOISE_Z = 2.0  # Standing this many spreads above the query's mean against all memories: a real match.
EXCLUDE_Z = 2.0  # The same for a negative term: close to it, out.
MIN_POOL = 8  # Fewer memories than this: too few to tell a match from noise (nothing is marked).
SPREAD_FLOOR = 0.02
CAPTION_FOLD = memory.FOLD_SQL.replace("(text,", "(coalesce(caption, ''),")
_NEGATIVE = re.compile(r'(?:(?<=\s)|^)-(?:"([^"]+)"|(\S+))')


def parse_query(q: str) -> tuple[str, list[str]]:
    """("kedi", ["köpek", "iki kelime"]) from 'kedi -köpek -"iki kelime"'. A dash inside a word stays."""
    negatives = [(a or b).strip() for a, b in _NEGATIVE.findall(q or "")]
    rest = " ".join(_NEGATIVE.sub(" ", q or "").split())
    return rest, [n for n in negatives if n][:5]


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


async def excluded_groups(conn, user_id: str, space, terms: list[str], settings) -> set[str]:
    """The memories a search leaves out for its negative terms: close to a term by meaning (z >= EXCLUDE_Z against
    the term's own distribution) or containing it in their text or caption."""
    from orion.kernel.memory_groups import GROUP_KEY
    out: set[str] = set()
    for term in terms:
        folded = memory.fold(term)
        like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        for r in await conn.fetch(
                f"select distinct {GROUP_KEY} as g from orion_memory where user_id = $1 and "
                f"({memory.FOLD_SQL} like $2 or {CAPTION_FOLD} like $2)", user_id, like):
            out.add(r["g"])
        if not space:
            continue
        _, vector, _ = await memory._query_vector(term, settings)
        if vector is None:
            continue
        scores = await group_scores(conn, user_id, space, vector)
        stats = calibrate(list(scores.values()))
        for g, s in scores.items():
            z = standing(s, stats)
            if z is not None and z >= EXCLUDE_Z:
                out.add(g)
    return out
