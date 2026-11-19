"""The cards (docs/hafiza-tasarimi.md §8, §12, §16 step 6): changes that wait for the user's answer, one table
(`orion_mem_proposal`), one list, one answer route, one card widget in the app (page and chat). Behind the per-user
`memory_v2` setting.

What a card can ask (`action`):
- `add`            a fact the model may not write itself (a locked kind) - carried out by memory_entities.carry_out
- `supersede`      a fact that changed (add_memory `replaces`): the old version closes only when the user accepts
- `set_relation`   a person's relation to the user changed (remember_fact `new_relation`: sevgilim -> eski sevgilim): the old
                   relation is closed with a date and the new one opens; `old` / `statement` hold the two relations
- `merge_entities` two entities that look the same (a batch card: each pair answered on its own)
- `trash`          the model asked to delete memories (delete_memory): they go to the trash (restorable) only when the
                   user accepts; the answer carries `trash_id` and the card says where they are now (in the trash,
                   restored, gone), so "Geri al" works from the card as it does for the old confirmation card
                   (memory_pending, which a user without memory_v2 still gets)
A card is decided once (`already`); a failed one goes back to `pending` with `error`; the sentence / labels a card held
are removed once it is decided (the memory may be deleted for good later, §11.2); what was done is kept as `result`.
The same open question is never asked twice (a model that asks again gets the same card).

Every table has user_id and every read here filters by it."""
from __future__ import annotations

import json
import logging

from orion.kernel import memory

logger = logging.getLogger(__name__)

ACTIONS = ("add", "supersede", "merge_entities", "trash", "set_relation")
# Keys of a payload that hold the user's words or a memory's text: removed from a card once it is decided.
CONTENT_KEYS = ("statement", "old", "preview")


def _entities():
    from orion.kernel import memory_entities
    return memory_entities


def _text(p: dict) -> str:
    return memory.fold(p.get("statement", ""))


def _same_question(action: str, a: dict, b: dict) -> bool:
    if action == "add":
        return a.get("about") == b.get("about") and _text(a) == _text(b)
    if action == "supersede":
        return a.get("fact_id") == b.get("fact_id") and _text(a) == _text(b)
    if action == "set_relation":
        return a.get("entity_id") == b.get("entity_id") and _text(a) == _text(b)
    if action == "merge_entities":
        return (a.get("from_id"), a.get("into_id")) == (b.get("from_id"), b.get("into_id"))
    if action == "trash":
        return sorted(a.get("ids") or []) == sorted(b.get("ids") or []) and bool(a.get("whole")) == bool(b.get("whole"))
    return False


async def pending_like(conn, user_id: str, action: str, payload: dict) -> int | None:
    """A pending proposal that asks the same thing (a model that asks twice opens one card)."""
    for r in await conn.fetch("select id, payload from orion_mem_proposal where user_id = $1 and action = $2 and "
                              "state = 'pending'", user_id, action):
        if _same_question(action, json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"], payload):
            return r["id"]
    return None


async def propose(user_id: str, settings, action: str, payload: dict, reason: str | None = None,
                  chat_id: str | None = None, batch_id: int | None = None, conn=None) -> int:
    """Open a card: a change that waits for the user (id). The same open question is not asked twice."""
    me = _entities()
    me.require(settings)
    if action not in ACTIONS:
        raise ValueError(f"Unknown proposal action {action!r}")
    async with me._db() as own:
        db = conn or own
        existing = await pending_like(db, user_id, action, payload)
        if existing:
            return existing
        pid = await db.fetchval(
            "insert into orion_mem_proposal (user_id, batch_id, action, payload, reason, chat_id) values ($1, $2, $3, $4, $5, "
            "$6) returning id", user_id, batch_id, action, json.dumps(payload, ensure_ascii=False), reason, chat_id)
    if getattr(settings, "memory_auto_approve", False) and action != "merge_entities" and batch_id is None:
        await decide(user_id, settings, accept=[pid], auto=True)  # Applied at once; the answer reaches the model like a press.
    return pid


async def propose_trash(user_id: str, settings, ids: list[int], whole: bool = False, chat_id: str | None = None,
                        reason: str | None = None) -> dict | None:
    """The model asks to delete memories (delete_memory): a card listing what would go, nothing moves yet. None when
    none of `ids` is this user's live memory. {"proposal", "will_delete": [{id, kind, label}], "parts", "whole_memory"}."""
    from orion.kernel import memory_pending
    me = _entities()
    me.require(settings)
    async with me._db() as conn:
        found = await memory_pending.describe(conn, user_id, ids, whole)
        if found is None:
            return None
        pid = await propose(user_id, settings, "trash", {
            "ids": found["ids"], "whole": bool(whole), "parts": found["parts"], "preview": found["preview"],
            "source": {"chat_id": chat_id}}, reason, chat_id, conn=conn)
    return {"proposal": pid, "will_delete": found["preview"], "parts": found["parts"], "whole_memory": bool(whole)}


async def _trash_where(conn, user_id: str, deleted: list[int]) -> str | None:
    """Where memories a card moved to the trash are now: "in" (the trash), "restored" or "gone" (deleted for good)."""
    if not deleted:
        return None
    now = await conn.fetchval(
        "select bool_or(t.memory_id is null) from orion_memory m left join orion_mem_trash t on t.memory_id = m.id "
        "where m.user_id = $1 and m.id = any($2::bigint[])", user_id, deleted)
    return "gone" if now is None else ("restored" if now else "in")


def _out(r, trash: str | None = None) -> dict:
    payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
    out = {"id": r["id"], "batch_id": r["batch_id"], "action": r["action"], "payload": payload, "reason": r["reason"],
           "chat_id": r["chat_id"], "state": r["state"], "created_at": r["created_at"].isoformat(),
           "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None}
    if trash:
        out["trash"] = trash
    return out


async def proposals(user_id: str, settings, state: str | None = "pending", ids: list[int] | None = None) -> list[dict]:
    me = _entities()
    me.require(settings)
    async with me._db() as conn:
        rows = await conn.fetch(
            "select * from orion_mem_proposal where user_id = $1 and ($2::text is null or state = $2) and "
            "($3::bigint[] is null or id = any($3::bigint[])) order by id desc limit 200", user_id, state, ids)
        out = []
        for r in rows:
            where = None
            if r["action"] == "trash" and r["state"] == "accepted":
                done = (json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]).get("result") or {}
                where = await _trash_where(conn, user_id, [int(i) for i in done.get("deleted") or []])
            out.append(_out(r, where))
    return out


_auto_tried: set[int] = set()  # Cards the automatic approval of old pending ones already tried (a failed one waits for the user).


async def approve_pending(user_id: str, settings) -> int:
    """`memory_auto_approve` is on: the cards that were already waiting are applied too (merge cards need the user's eyes and stay).
    Called when the pending list is read; a card is tried once per process. The chat is not told (the cards are old news).
    Returns how many were applied. Never raises."""
    if not getattr(settings, "memory_auto_approve", False):
        return 0
    try:
        me = _entities()
        me.require(settings)
        async with me._db() as conn:
            ids = [r["id"] for r in await conn.fetch(
                "select id from orion_mem_proposal where user_id = $1 and state = 'pending' and action <> 'merge_entities' "
                "and batch_id is null order by id limit 100", user_id) if r["id"] not in _auto_tried]
        if not ids:
            return 0
        _auto_tried.update(ids)
        results = await decide(user_id, settings, accept=ids, auto=True, tell=False)
        return sum(1 for r in results if r["state"] in ("accepted", "edited"))
    except Exception:
        logger.warning("Memory proposals: the waiting cards could not be approved automatically", exc_info=True)
        return 0


async def count_pending(user_id: str, settings) -> int:
    me = _entities()
    me.require(settings)
    async with me._db() as conn:
        return int(await conn.fetchval("select count(*) from orion_mem_proposal where user_id = $1 and state = 'pending'",
                                       user_id))


def _answer_text(card: dict, state: str, auto: bool) -> str | None:
    """The line the model reads about a card's outcome: "[Hafıza kartı: 'old' → 'new' — onaylandı]"; None for a card that does not
    concern the conversation (a merge)."""
    p = card["payload"]
    what = {"accepted": "onaylandı", "edited": "düzenlenerek onaylandı", "rejected": "reddedildi"}.get(state)
    if what is None or card["action"] == "merge_entities":
        return None
    if auto and state != "rejected":
        what = "otomatik onaylandı"
    if card["action"] == "supersede":
        body = f"'{p.get('old')}' → '{p.get('statement')}'"
    elif card["action"] == "set_relation":
        body = f"{p.get('about')}: ilişki '{p.get('old') or '?'}' → '{p.get('statement')}'"
    elif card["action"] == "trash":
        body = f"silme ({len(p.get('ids') or [])} kayıt)"
    else:
        body = f"'{p.get('statement')}' eklensin"
    return f"[Hafıza kartı: {body} — {what}]"


async def _tell_the_model(user_id: str, card: dict, state: str, auto: bool) -> None:
    """The outcome of a card goes into the "Asistanla konuş" conversation as a user-role note (`memory_card_answer`; the app may
    hide it) so the next model turn knows. Never raises."""
    text = _answer_text(card, state, auto)
    chat_id = card.get("chat_id")
    if not text or not chat_id:
        return
    try:
        from orion.kernel.registry import insert_messages
        me = _entities()
        async with me._db() as conn:
            if not await conn.fetchval("select assistant from orion_chats where id = $1 and user_id = $2", chat_id, user_id):
                return
        await insert_messages(chat_id=chat_id, messages=[{"role": "user", "content": text, "memory_card_answer": True,
                                                          "turn_id": f"card-{card['id']}"}])
    except Exception:
        logger.warning("Memory card %s: its answer could not be put into the chat", card.get("id"), exc_info=True)


async def decide(user_id: str, settings, accept: list[int] = (), reject: list[int] = (),
                 edits: dict[int, str] | None = None, auto: bool = False, tell: bool = True) -> list[dict]:
    """The user's answer to cards: each accepted one is carried out (in its own transaction), rejected ones closed;
    `edits` {id: sentence}: Düzenle (the user's own sentence is written instead, state 'edited'). Each result:
    {"id", "state": accepted | edited | rejected | pending (failed: "error") | already | unknown, ...what was done}. A
    decided card is never decided twice. What a decided card held (the sentence, the labels) is removed from it."""
    me = _entities()
    me.require(settings)
    edits = edits or {}
    out = []
    for pid in list(dict.fromkeys([*accept, *reject])):
        async with me._db() as conn:
            row = await conn.fetchrow("update orion_mem_proposal set state = 'claimed' where user_id = $1 and id = $2 "
                                      "and state = 'pending' returning *", user_id, pid)
            if row is None:
                known = await conn.fetchval("select state from orion_mem_proposal where user_id = $1 and id = $2", user_id,
                                            pid)
                out.append({"id": pid, "state": "already" if known else "unknown", "was": known})
                continue
        card = _out(row)
        if pid in reject and pid not in accept:
            state, error, extra = "rejected", None, {}
        else:
            try:
                extra = await carry_out(user_id, settings, card, edits.get(pid))
                state, error = ("edited" if edits.get(pid) else "accepted"), None
            except Exception as exc:  # noqa: BLE001 - one card failing does not stop the others
                logger.info("Memory proposal %s not carried out: %s", pid, exc)
                state, error, extra = "pending", str(exc), {}
        async with me._db() as conn:
            payload = card["payload"]
            if state != "pending":
                payload = {k: v for k, v in payload.items() if k not in CONTENT_KEYS}
                if extra:
                    payload["result"] = extra
            await conn.execute("update orion_mem_proposal set state = $3, decided_at = case when $3 = 'pending' then null "
                               "else now() end, payload = $4 where user_id = $1 and id = $2", user_id, pid, state,
                               json.dumps(payload, ensure_ascii=False))
        result = {"id": pid, "state": state, **extra}
        if error:
            result["error"] = error
        if state != "pending" and tell:
            await _tell_the_model(user_id, card, state, auto)
        out.append(result)
    return out


async def carry_out(user_id: str, settings, card: dict, edit: str | None) -> dict:
    """What a card does when accepted. A trash card moves the memories to the trash as ONE removal ({"trash_id",
    "deleted": [ids]}); the others belong to memory_entities."""
    if card["action"] != "trash":
        return await _entities().carry_out(user_id, settings, card, edit)
    from orion.kernel import memory_groups, memory_trash
    p = card["payload"]
    ids = [int(i) for i in p.get("ids") or []]
    if p.get("whole"):
        async with _entities()._db() as conn:
            groups = [r["g"] for r in await conn.fetch(
                f"select distinct {memory_groups.GROUP_KEY} as g from orion_memory where user_id = $1 and "
                "id = any($2::bigint[])", user_id, ids)]
        removed = await memory_trash.trash_groups(user_id, groups)
    else:
        removed = await memory_trash.trash_items(user_id, ids)
    if not removed["removed"]:
        raise ValueError("Bunlar artık hafızada değil (zaten kaldırılmış).")
    return {"trash_id": removed["trash_id"], "deleted": [r["id"] for r in removed["removed"]]}
