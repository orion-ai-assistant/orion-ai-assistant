"""The profile block (docs/hafiza-tasarimi.md §9.2, §16 step 8): a short, stable summary of what the memory knows about the
user, put in the system message of every chat. Behind the per-user `memory_v2` setting (and `memory_profile`, on by default);
with either off nothing here runs and the chat is exactly what it was.

What is in it, in this order, inside TOKEN_LIMIT tokens (the whole block, header included; tokens are estimated:
memory.estimate_tokens):
1. The lines the user PINNED (`orion_mem_profile.pinned`: their own text, optionally tied to a fact). Always in. If they alone
   pass the limit the block still carries them all, `overflow` says so (the page warns the user, who shortens them) and
   the generated part is left empty.
2. Then the current facts, by kind: kimlik, ilişki (family, partner, children), iş, yer, tercih, everything else; inside a
   kind the fact with more sources / the more recently confirmed one first. A fact is never cut in the middle: one that does not
   fit is left out whole and the next one that does fit is tried.
Never in it: a past version, a removed fact (the trash), a locked (sensitive) fact, a fact of a removed or merged entity.

Rebuilt from the facts at every chat turn (one small query), so a fact that changed, was removed or went to the trash is gone
or new in the very next turn; when nothing changed the text is byte for byte the same (the model's prompt cache stays
valid). The last built block is stored (`generated`: fact ids and lines, `tokens`, `built_at`) only when it changed, for the
page. A pinned line tied to a fact that was since replaced or removed is not injected (it says `replaced` / `gone` on the
page: the user pins the new one); a pinned line tied to a locked fact cannot be made at all.

The Router's request log: the chat request carries `x-orion-memory-profile: tokens=N; facts=1,2,3` (see `log_header`) so
the Router can label the log entry with them (the block's text stays in the Router's log, nothing is hidden), and the Hub
logs the same line (never the content).
Every table has user_id; every read filters by it."""
from __future__ import annotations

import json
import logging

from orion.kernel import memory, memory_entities as me

logger = logging.getLogger(__name__)

TOKEN_LIMIT = 300
MAX_PINNED = 30
MAX_PINNED_CHARS = 300
CANDIDATES = 300  # Facts read per build, best first (the block holds a few dozen lines at most).
HEADER = ("What you know about the user (a profile built from their memory; use it quietly where it helps, "
          "never recite it):")
RANK = ("kimlik", "ilişki", "iş", "yer", "tercih")  # Then the rest, in one group.
HEADER_NAME = "x-orion-memory-profile"


def enabled(settings) -> bool:
    return bool(getattr(settings, "memory_v2", False)) and bool(getattr(settings, "memory_profile", True))


def require(settings) -> None:
    if not bool(getattr(settings, "memory_v2", False)):
        raise ValueError("Hafıza koleksiyonları kapalı: Ayarlar'da açın (memory_v2).")


# A fact "lives" when the current version of its lineage does (a replaced version keeps no vector row of its own).
_HEAD_LIVE = ("exists (select 1 from orion_mem_fact h where h.user_id = f.user_id and h.lineage = f.lineage and "
              f"h.superseded_by is null and {me._live('h')})")


def _line(text: str) -> str:
    return "- " + " ".join((text or "").split())


def log_header(block: dict | None) -> dict[str, str]:
    """The request header that tells the Router what the block was without its text."""
    if not block:
        return {}
    return {HEADER_NAME: f"tokens={block['tokens']}; facts={','.join(str(i) for i in block['fact_ids'])}"}


async def _pinned(conn, user_id: str) -> list[dict]:
    raw = await conn.fetchval("select pinned from orion_mem_profile where user_id = $1", user_id)
    items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return [{"text": str(i.get("text") or ""), "fact_id": i.get("fact_id")} for i in items if i.get("text")]


async def _pinned_state(conn, user_id: str, pinned: list[dict]) -> list[dict]:
    """Each pinned line with its `state`: ok, or replaced / gone / locked for a line tied to a fact that no longer counts."""
    ids = [int(p["fact_id"]) for p in pinned if p.get("fact_id") is not None]
    facts = {r["id"]: r for r in await conn.fetch(
        f"select f.id, f.superseded_by, f.sensitive, {_HEAD_LIVE} as live from orion_mem_fact f "
        "where f.user_id = $1 and f.id = any($2::bigint[])", user_id, ids)} if ids else {}
    out = []
    for p in pinned:
        state = "ok"
        if p.get("fact_id") is not None:
            f = facts.get(int(p["fact_id"]))
            if f is None or not f["live"]:
                state = "gone"
            elif f["sensitive"]:
                state = "locked"
            elif f["superseded_by"] is not None:
                state = "replaced"
        out.append({**p, "state": state})
    return out


async def _candidates(conn, user_id: str) -> list:
    """The current, live, unlocked facts best first: kind rank, then more sources, then the most recently confirmed (the
    newest source, else when it was learned), then the newest id (a fixed last tie-break)."""
    rank = " ".join(f"when f.category = '{c}' then {i}" for i, c in enumerate(RANK))
    return await conn.fetch(
        f"select f.id, f.category, f.statement, count(s.id) as sources, "
        f"coalesce(max(s.created_at), f.recorded_at) as confirmed from orion_mem_fact f "
        f"join orion_mem_entity e on e.id = f.entity_id and e.user_id = f.user_id "
        f"left join orion_mem_source s on s.user_id = f.user_id and s.target_type = 'fact' and s.target_id = f.id "
        f"where f.user_id = $1 and f.superseded_by is null and f.valid_to is null and f.expired_at is null "
        f"and not f.sensitive and e.merged_into is null and e.deleted_at is null and {me._live('f')} "
        f"group by f.id order by case {rank} else {len(RANK)} end, count(s.id) desc, "
        f"coalesce(max(s.created_at), f.recorded_at) desc, f.id desc limit {CANDIDATES}", user_id)


def compose(pinned: list[dict], facts: list) -> dict:
    """The block from the pinned lines (state ok) and the candidate facts (best first): see the module docstring.
    {"text", "tokens", "fact_ids", "lines": [{"fact_id", "text", "pinned"}], "overflow", "left_out"}."""
    used = memory.estimate_tokens(HEADER)
    lines = [{"fact_id": p.get("fact_id"), "text": p["text"], "pinned": True} for p in pinned if p["state"] == "ok"]
    for item in lines:
        used += memory.estimate_tokens(_line(item["text"]))
    overflow = bool(lines) and used > TOKEN_LIMIT
    left_out = 0
    if not overflow:
        pinned_ids = {int(p["fact_id"]) for p in pinned if p.get("fact_id") is not None}
        seen = {memory.fold(p["text"]) for p in pinned}
        for f in facts:
            if f["id"] in pinned_ids or memory.fold(f["statement"]) in seen:
                continue
            cost = memory.estimate_tokens(_line(f["statement"]))
            if used + cost > TOKEN_LIMIT:
                left_out += 1
                continue
            used += cost
            lines.append({"fact_id": f["id"], "text": " ".join(f["statement"].split()), "pinned": False})
    text = "\n".join([HEADER, *(_line(i["text"]) for i in lines)]) if lines else ""
    return {"text": text, "tokens": used if lines else 0,
            "fact_ids": [i["fact_id"] for i in lines if i["fact_id"] is not None], "lines": lines,
            "overflow": overflow, "left_out": left_out}


async def build(user_id: str, settings, store: bool = True) -> dict:
    """The user's block now ({"text", "tokens", "fact_ids", "lines", "overflow", "left_out", "pinned": the pinned lines with
    their state}); stored as the last built one when it differs from the stored one (`store`)."""
    async with me._db() as conn:
        pinned = await _pinned_state(conn, user_id, await _pinned(conn, user_id))
        block = compose(pinned, await _candidates(conn, user_id))
        block["pinned"] = pinned
        if store:
            generated = [{"fact_id": i["fact_id"], "text": i["text"]} for i in block["lines"] if not i["pinned"]]
            was = await conn.fetchval("select generated from orion_mem_profile where user_id = $1", user_id)
            was = json.loads(was) if isinstance(was, str) else was
            if was is None or was != generated:
                await conn.execute(
                    "insert into orion_mem_profile (user_id, generated, tokens, built_at) values ($1, $2::jsonb, $3, now()) "
                    "on conflict (user_id) do update set generated = excluded.generated, tokens = excluded.tokens, "
                    "built_at = excluded.built_at", user_id, json.dumps(generated, ensure_ascii=False), block["tokens"])
    return block


async def for_chat(user_id: str, settings) -> dict | None:
    """The block of a chat turn ({"text", "tokens", "fact_ids"}) or None (off, nothing to say, or any failure: a chat never
    waits on or breaks for its profile). The line the Router log shows is made by log_header."""
    if not enabled(settings):
        return None
    try:
        block = await build(user_id, settings)
    except Exception:
        logger.warning("Memory profile: could not be built; the chat goes without it.", exc_info=True)
        return None
    if not block["text"]:
        return None
    logger.info("Memory profile: user=%s tokens=%d facts=%s pinned=%d", user_id, block["tokens"],
                ",".join(str(i) for i in block["fact_ids"]) or "-", sum(1 for i in block["lines"] if i["pinned"]))
    return {"text": block["text"], "tokens": block["tokens"], "fact_ids": block["fact_ids"]}


def with_system_prompt(system_prompt: str, block: dict | None) -> str:
    """The system message: the user's own system prompt, then the block (nothing added without one)."""
    if not block:
        return system_prompt
    return f"{system_prompt}\n\n{block['text']}" if system_prompt else block["text"]


# --- The user's pinned lines (the page: Ayarlar > profil) ---------------------------------------------------------------

def _clean_pinned(items: list[dict]) -> list[dict]:
    if len(items) > MAX_PINNED:
        raise ValueError(f"En fazla {MAX_PINNED} satır sabitlenebilir.")
    out, seen = [], set()
    for item in items:
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        if len(text) > MAX_PINNED_CHARS:
            raise ValueError(f"Bir satır en fazla {MAX_PINNED_CHARS} karakter olabilir.")
        key = memory.fold(text)
        if key in seen:
            continue
        seen.add(key)
        fact_id = item.get("fact_id")
        out.append({"text": text, "fact_id": int(fact_id) if fact_id is not None else None})
    return out


async def _save_pinned(conn, user_id: str, items: list[dict]) -> None:
    await conn.execute("insert into orion_mem_profile (user_id, pinned) values ($1, $2::jsonb) on conflict (user_id) do "
                       "update set pinned = excluded.pinned", user_id, json.dumps(items, ensure_ascii=False))


async def _check_facts(conn, user_id: str, items: list[dict]) -> None:
    """A pinned line tied to a fact: it must be this user's current, live, unlocked fact (LookupError / ValueError)."""
    for item in items:
        if item["fact_id"] is None:
            continue
        row = await conn.fetchrow(f"select f.sensitive, f.superseded_by, {_HEAD_LIVE} as live from orion_mem_fact f "
                                  "where f.user_id = $1 and f.id = $2", user_id, item["fact_id"])
        if row is None or not row["live"]:
            raise LookupError(item["fact_id"])
        if row["sensitive"]:
            raise ValueError("Kilitli bir bilgi profile konamaz.")
        if row["superseded_by"] is not None:
            raise ValueError("Bu bilgi yenisiyle değişti; güncel olanı sabitleyin.")


async def get(user_id: str, settings) -> dict:
    """What the page shows: the block as it would be sent ({"text", "tokens", "limit", "overflow", "left_out"}), the pinned
    lines with their state, and the generated lines with their fact ids."""
    require(settings)
    block = await build(user_id, settings)
    return {"enabled": enabled(settings), "limit": TOKEN_LIMIT, "text": block["text"], "tokens": block["tokens"],
            "overflow": block["overflow"], "left_out": block["left_out"], "pinned": block["pinned"],
            "generated": [{"fact_id": i["fact_id"], "text": i["text"]} for i in block["lines"] if not i["pinned"]]}


async def set_pinned(user_id: str, settings, items: list[dict]) -> dict:
    """Replace the pinned lines ([{"text", "fact_id"?}]). ValueError for too many / too long / a locked or replaced fact,
    LookupError for a fact that is not the user's."""
    require(settings)
    items = _clean_pinned(items)
    async with me._db() as conn:
        await _check_facts(conn, user_id, items)
        await _save_pinned(conn, user_id, items)
    return await get(user_id, settings)


async def pin_fact(user_id: str, settings, fact_id: int, text: str | None = None) -> dict:
    """Pin a current fact (its own sentence, or `text` the user rewrote); pinning it again changes the text."""
    require(settings)
    async with me._db() as conn:
        row = await conn.fetchrow(f"select f.statement from orion_mem_fact f where f.user_id = $1 and f.id = $2 and "
                                  f"{me._live('f')}", user_id, int(fact_id))
        if row is None:
            raise LookupError(fact_id)
        items = [p for p in await _pinned(conn, user_id) if p["fact_id"] != int(fact_id)]
        items.append({"text": text or row["statement"], "fact_id": int(fact_id)})
        items = _clean_pinned(items)
        await _check_facts(conn, user_id, items)
        await _save_pinned(conn, user_id, items)
    return await get(user_id, settings)


async def unpin_fact(user_id: str, settings, fact_id: int) -> dict:
    require(settings)
    async with me._db() as conn:
        await _save_pinned(conn, user_id, [p for p in await _pinned(conn, user_id) if p["fact_id"] != int(fact_id)])
    return await get(user_id, settings)


async def forget_user(conn, user_id: str) -> None:
    """memory_v2 turned off for good: the built block goes (derived); the user's pinned lines are their own writing and stay."""
    await conn.execute("update orion_mem_profile set generated = '[]'::jsonb, tokens = null, built_at = null "
                       "where user_id = $1", user_id)
