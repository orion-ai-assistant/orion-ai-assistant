import asyncio
import aiohttp
from contextlib import suppress
from typing import Any
from services.shared.environment import get_env
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from starlette.background import BackgroundTask
from redis.asyncio import Redis
import websockets
import urllib.parse

from orion.contracts.http import JobCreateRequest, JobCreateResponse, JobStatusResponse, JobStopResponse
from orion.contracts.constants import ROOM_USER_PREFIX # Prefix'i direkt buradan alıyoruz
from pydantic import BaseModel, Field, ValidationError

from orion.kernel.config import RuntimeSettings, settings as factory_settings, get_runtime_settings, update_runtime_settings, _allowed_keys, get_all_users_settings, delete_runtime_setting, is_protected_global_key, mask_api_key
from orion.kernel.router_models import validate_model_updates, ModelNotFoundError
from orion.kernel.config import serialize_setting
from orion.contracts.constants import SETTINGS_DEFAULT_USER
from orion.api.services.job_service import create_job, get_job, stop_job, utc_now, get_key, get_user_chats, get_chat_history, get_turn_audio, rename_chat, delete_chat, get_all_chats_admin, get_chat_history_admin, search_chats
from orion.api.auth_routes import authenticate_token, get_current_user
from orion.api.security import valid_admin_key, require_admin
from services.shared.environment import get_router_base_urls

router = APIRouter()


@router.get("/api/v1/audio/languages")
async def stt_language_catalog(request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.services.stt_catalog import fetch_stt_catalog
    settings = await get_runtime_settings(request.app.state.redis, current_user)
    try:
        return await fetch_stt_catalog(settings.router_api_key)
    except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(503, "STT dil listesine şu anda ulaşılamıyor.") from exc

from orion.contracts.tools import ToolSelection, default_tool_selection
from orion.api.services.tool_service import read_selection, save_selection
from orion.api.services.job_service import ensure_chat_access
from orion.worker.tools._registry import get_registry


@router.get("/api/v1/tools")
async def tool_catalog(request: Request, lang: str | None = None, current_user: str = Depends(get_current_user)):
    return get_registry().catalog(lang or request.headers.get("Accept-Language"))


@router.get("/api/v1/tools/default-selection", response_model=ToolSelection)
async def tool_default_selection(current_user: str = Depends(get_current_user)):
    return default_tool_selection()


@router.get("/api/v1/chats/{chat_id}/tools")
async def get_chat_tools(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    try:
        return await read_selection(request.app.state.redis, current_user, chat_id)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.put("/api/v1/chats/{chat_id}/tools")
async def put_chat_tools(chat_id: str, payload: ToolSelection, request: Request, current_user: str = Depends(get_current_user)):
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    return await save_selection(request.app.state.redis, current_user, chat_id, payload)


@router.delete("/api/v1/chats/{chat_id}/tools")
async def reset_chat_tools(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    return await save_selection(request.app.state.redis, current_user, chat_id, None)


@router.post("/api/v1/chats/{chat_id}/memory/save")
async def save_chat_to_memory(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    """Only the "Asistanla konuş" chat is saved to memory, on the user's button press."""
    from orion.api.services.assistant_chat import current_assistant_chat
    from orion.kernel import memory
    meta = await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    if await current_assistant_chat(request.app.state.redis, current_user) != chat_id:
        raise HTTPException(400, "Yalnızca Asistanla konuş sohbeti hafızaya kaydedilir.")
    try:
        saved = await memory.save_chat(current_user, chat_id, meta.get("name") or "",
                                       await get_runtime_settings(request.app.state.redis, current_user))
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return saved  # {"saved", "skipped", "warnings"}: images/audio the model can't take are skipped with a warning.


class MemoryNote(BaseModel):
    text: str = Field(default="", max_length=8 * 1024 * 1024)  # A request-size guard; memory.MAX_NOTE is the real cap.
    title: str = Field(default="", max_length=200)  # The memory's name (optional).
    group_id: str | None = Field(default=None, max_length=64)  # Add to this memory instead of a new one.


class MemoryDelete(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)  # Parts (rows); their copies go with them.
    group_ids: list[str] = Field(default_factory=list, max_length=200)  # Whole memories.
    item_ids: list[str] = Field(default_factory=list, max_length=100)  # Whole parts (every piece of an item).


_searches: dict = {}


async def latest_search(key: str, work):
    """Run a memory search as the only one of `key` (a user): a newer search cancels the one still running (its
    embedding and queries stop; the older request answers 409, which the page ignores: the latest wins)."""
    import asyncio
    old = _searches.get(key)
    if old is not None and not old.done():
        old.cancel()
    task = asyncio.ensure_future(work)
    _searches[key] = task
    try:
        return await task
    except asyncio.CancelledError:
        if task.cancelled() and _searches.get(key) is not task:
            raise HTTPException(409, "Daha yeni bir arama başladı.")
        raise
    finally:
        if _searches.get(key) is task:
            _searches.pop(key, None)


@router.get("/api/v1/memory")
async def list_memory(request: Request, q: str = "", limit: int = 20, offset: int = 0, vector_space: str = "all",
                      kind: str = "all", exclude: list[str] = Query(default=[]),
                      collection: list[str] = Query(default=[]), when_from: str = "", when_to: str = "",
                      about: str = "", current_user: str = Depends(get_current_user)):
    """A page of memories: cards of the rows saved together (memory_groups.list_groups). `collection`: only these
    collections; `when_from` / `when_to`: the one date filter (YYYY[-MM[-DD]]): events within it, facts that were true within it,
    notes and files saved within it, the photos of such events. Both with the user's memory_v2 on. Also with memory_v2 (the chat
    tool's `about`, one code: memory_groups.list_groups): `about` (a name, relation, `#<id>` or `me`): only what is about that
    entity. The answer then also has `entity` (the card of the one entity the query or `about` names), `entity_candidates` (several
    fit: no card; `about` that fits several: no results either) and `note` when `about` names nobody. A fact's earlier versions
    are in its own history (GET /memory/facts/{id}/history), not in the list."""
    from orion.kernel import memory, memory_entities, memory_events, memory_groups
    try:
        settings = await get_runtime_settings(request.app.state.redis, current_user)
        span = memory_events.parse_range(when_from[:10], when_to[:10])
        extra: dict = {}
        about = about.strip()[:120]
        v2 = memory_entities.enabled(settings)
        entity_id = None
        if v2 and about:
            try:
                async with memory_entities._db() as conn:
                    entity, _ = await memory_entities.resolve_entity(conn, current_user, about, create=False)
            except memory_entities.Ambiguous as exc:
                return {"groups": [], "matching": 0, "more": False, "note": str(exc),
                        "entity_candidates": [{"id": e["id"], "name": e["name"], "relation": e["relation"]}
                                              for e in exc.candidates[:5]]}
            if entity is None:
                return {"groups": [], "matching": 0, "more": False, "note": f"Hafızada {about!r} adında biri yok."}
            entity_id = entity["id"]
            extra["about"] = [entity_id]
        if v2:
            async def side(conn):  # The entity card, on the search's own connection.
                try:
                    return await memory_entities.search_extras(current_user, settings, [q.strip()] if q.strip() else [],
                                                                entity_id, conn=conn)
                except Exception:  # Extras: the page's results go out without them.
                    import logging
                    logging.getLogger(__name__).warning("memory page: entity card failed", exc_info=True)
                    return None
            extra["side"] = side
        work = memory_groups.list_groups(
            current_user, settings, q[:memory.MAX_QUERY_CHARS],
            max(1, min(limit, 100)), max(0, offset), vector_space[:100], kind[:20],
            exclude=[t[:200] for t in exclude[:5]], collections=[c[:120] for c in collection[:10]] or None, when=span,
            **extra)
        found = await (latest_search(current_user, work) if q.strip() else work)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    extras = found.pop("side", None)
    if extras is not None:
        if extras["candidates"]:
            found["entity_candidates"] = extras["candidates"]
        if extras["card"] is not None:
            found["entity"] = extras["card"]
    windows = await query_note(q)
    if windows:
        found["query_windows"] = windows  # The page says how a long passage was searched.
    return found


async def query_note(q: str) -> dict | None:
    """{"used", "total"} windows of a long passage query (memory.query_windows), None for a short query."""
    import asyncio
    from orion.kernel import memory
    q = (q or "").strip()
    if len(q) <= memory.TEXT_MATCH_CHARS:
        return None
    windows, total = await asyncio.to_thread(memory.query_windows, q)
    return {"used": len(windows), "total": total} if total > 1 else None


@router.get("/api/v1/memory/groups/{group_id}")
async def memory_group(group_id: str, request: Request, offset: int = Query(0, ge=0),
                       limit: int = Query(60, ge=1, le=500), around: list[int] = Query(default=[]),
                       item: str | None = Query(None, max_length=64),
                       current_user: str = Depends(get_current_user)):
    """One memory for the detail panel (a long item as a preview with the `around` pieces), the models it is in,
    its source chat; with `item`: a page of that item's pieces (the viewer's lazily loaded text)."""
    from orion.kernel import memory, memory_groups
    try:
        found = await memory_groups.group_detail(current_user, group_id[:64],
                                                 await get_runtime_settings(request.app.state.redis, current_user),
                                                 offset, limit, around[:50], item)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.")
    return found


@router.get("/api/v1/memory/map")
async def memory_map(request: Request, k: int = 5, limit: int = 300, vector_space: str = "", kind: str = "all",
                     collection: list[str] = Query(default=[]), when_from: str = "", when_to: str = "",
                     about: str = "", current_user: str = Depends(get_current_user)):
    """A model's memories (the active one's by default) as a graph: each one's nearest neighbours above the
    space's own floor (pgvector). The page's filters (`collection`, `when_from`/`when_to`, `about`: the same
    values and code as GET /api/v1/memory) leave the other memories out of the map; with memory_v2 on."""
    from orion.kernel import memory, memory_entities, memory_events, memory_groups
    try:
        settings = await get_runtime_settings(request.app.state.redis, current_user)
        extra: dict = {}
        about = about.strip()[:120]
        if memory_entities.enabled(settings):
            if about:
                try:
                    async with memory_entities._db() as conn:
                        entity, _ = await memory_entities.resolve_entity(conn, current_user, about, create=False)
                except memory_entities.Ambiguous:
                    entity = None
                extra["about"] = [entity["id"]] if entity else []  # Nobody by that name: an empty map.
        return await memory_groups.map_graph(
            current_user, settings, k, limit, vector_space[:100], kind[:20],
            collections=[c[:120] for c in collection[:10]] or None,
            when=memory_events.parse_range(when_from[:10], when_to[:10]), **extra)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/v1/memory/vectors")
async def memory_space_vectors(request: Request, vector_space: str = "", current_user: str = Depends(get_current_user)):
    """What removing another model's vectors would change (memory_groups.space_vectors)."""
    from orion.kernel import memory, memory_groups
    try:
        return await memory_groups.space_vectors(current_user, vector_space[:100],
                                                 await get_runtime_settings(request.app.state.redis, current_user))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


class MemoryMove(BaseModel):
    vector_space: str = Field(default="", max_length=100)


@router.get("/api/v1/memory/move")
async def memory_move_status(current_user: str = Depends(get_current_user)):
    from orion.kernel import memory, memory_move
    try:
        return {"job": await memory_move.status(current_user)}
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/move")
async def memory_move_start(payload: MemoryMove, request: Request, current_user: str = Depends(get_current_user)):
    """Copy another model's memories into the active model's space, in the background (memory_move)."""
    from orion.kernel import memory, memory_move
    try:
        return {"job": await memory_move.start(current_user, payload.vector_space,
                                               await get_runtime_settings(request.app.state.redis, current_user))}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/v1/chats/{chat_id}/compact")
async def get_chat_compaction(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    """The compaction of the "Asistanla konuş" chat: {running, compacted_upto (last message id covered), compacted_at,
    summary_tokens, messages_since, tokens_since, summary, compacted_turn_id, last_usage {prompt_tokens, completion_tokens, at},
    context_tokens (the chat model's context length, 0 = unknown), threshold_percent} for the client's token meter."""
    from orion.kernel import memory, memory_compact
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    settings = await get_runtime_settings(request.app.state.redis, current_user)
    try:
        got = await memory_compact.state(current_user, chat_id)
        got["context_tokens"] = await memory_compact.context_length(settings)
        got["threshold_percent"] = settings.chat_compact_percent
        return got
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/chats/{chat_id}/compact", status_code=202)
async def compact_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    """"Sohbeti sıkıştır": one background-model call turns the conversation into a summary (its new first message for the
    model; older messages stay stored and shown) and memory items. Answers 202 {"state": "started"} at once; poll the GET.
    400: not the assistant chat / memory off / nothing to compact; 409: already running."""
    from orion.kernel import memory, memory_compact
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    settings = await get_runtime_settings(request.app.state.redis, current_user)
    got = await memory_compact.state(current_user, chat_id)
    if got["running"]:
        raise HTTPException(409, "Bu sohbet zaten sıkıştırılıyor.")
    if not got["messages_since"]:
        raise HTTPException(400, "Sıkıştırılacak mesaj yok.")
    if not memory_compact.mx.enabled(settings):
        raise HTTPException(400, "Hafıza kapalı (memory_v2 ve sohbetten çıkarım).")
    memory_compact.start(current_user, chat_id, settings)
    return {"state": "started"}


# --- Ben: entities, facts, proposals (memory_entities; behind the user's memory_v2) -------------------------------------

class EntityBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    kind: str = Field(default="person", max_length=20)
    relation: str | None = Field(default=None, max_length=120)
    aliases: list[str] | None = Field(default=None, max_length=20)


class FactBody(BaseModel):
    entity_id: int | None = None  # An existing entity, else `about` (a name; a new entity when unknown).
    about: str = Field(default="", max_length=120)
    kind: str = Field(default="person", max_length=20)
    relation: str | None = Field(default=None, max_length=120)
    category: str = Field(default="diğer", max_length=40)
    statement: str = Field(min_length=1, max_length=2000)
    since: str | None = Field(default=None, max_length=10)
    recurring: str | None = Field(default=None, max_length=12)  # The day it repeats every year ("11-17"); "" stops it.


class MergeBody(BaseModel):
    from_id: int
    into_id: int


class ProposalAnswer(BaseModel):
    accept: list[int] = Field(default_factory=list, max_length=100)
    reject: list[int] = Field(default_factory=list, max_length=100)
    edits: dict[str, str] = Field(default_factory=dict, max_length=100)  # proposal id -> the user's own sentence


async def _ben(request: Request, user: str, call):
    """Run `call(settings)` of memory_entities as this user: its errors as the page's answers (404 for what is not the
    user's, 400 for a refused value, 409 for a name that fits several entities)."""
    from orion.kernel import memory, memory_entities
    try:
        return await call(await get_runtime_settings(request.app.state.redis, user))
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except memory_entities.Ambiguous as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/v1/memory/entities")
async def memory_entities_list(request: Request, q: str = "", current_user: str = Depends(get_current_user)):
    """The Ben tab's list: {"entities": [{id, kind, name, relation, aliases, facts, locked}], "pending": proposals open}."""
    from orion.kernel import memory_entities as me

    async def call(settings):
        return {"entities": await me.list_entities(current_user, settings, q[:120]),
                "pending": len(await me.proposals(current_user, settings, "pending"))}
    return await _ben(request, current_user, call)


@router.post("/api/v1/memory/entities")
async def memory_entity_create(payload: EntityBody, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.create_entity(
        current_user, s, payload.name or "", payload.kind, payload.relation, payload.aliases))


@router.post("/api/v1/memory/entities/merge")
async def memory_entities_merge(payload: MergeBody, request: Request, current_user: str = Depends(get_current_user)):
    """The user merges two entities (their own change, no card); {"merge_id"} undoes it."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.merge_entities(current_user, s, payload.from_id, payload.into_id))


@router.post("/api/v1/memory/entities/merges/{merge_id}/undo")
async def memory_entities_undo_merge(merge_id: int, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.undo_merge(current_user, s, merge_id))


@router.post("/api/v1/memory/entities/duplicates")
async def memory_entities_duplicates(request: Request, current_user: str = Depends(get_current_user)):
    """Entities with the same name or alias as ONE batch card of merge proposals: {"batch_id", "proposals": [ids]}."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.propose_merges(current_user, s))


@router.get("/api/v1/memory/entities/{entity_id}")
async def memory_entity_card(entity_id: int, request: Request, reveal: bool = False,
                             current_user: str = Depends(get_current_user)):
    """One entity with its current facts (past version counts, source counts; locked ones without text unless `reveal`)."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.entity_card(current_user, s, entity_id, reveal))


@router.put("/api/v1/memory/entities/{entity_id}")  # PUT too: the app's client has no PATCH.
@router.patch("/api/v1/memory/entities/{entity_id}")
async def memory_entity_update(entity_id: int, payload: EntityBody, request: Request,
                               current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.update_entity(
        current_user, s, entity_id, payload.name, payload.relation, payload.aliases))


@router.post("/api/v1/memory/facts")
async def memory_fact_create(payload: FactBody, request: Request, current_user: str = Depends(get_current_user)):
    """The user writes a fact (no card; source: the user): {"fact"} or {"same_as"}."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.write_by_user(
        current_user, s, payload.about, payload.category, payload.statement, kind=payload.kind,
        relation=payload.relation, since=payload.since, entity_id=payload.entity_id, recurring=payload.recurring))


@router.get("/api/v1/memory/facts/{fact_id}")
async def memory_fact_history(fact_id: int, request: Request, reveal: bool = False,
                              current_user: str = Depends(get_current_user)):
    """Every version of a fact with its sources (locked text only with `reveal`)."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.history(current_user, s, fact_id, reveal))


@router.post("/api/v1/memory/facts/{fact_id}/replace")
async def memory_fact_replace(fact_id: int, payload: FactBody, request: Request,
                              current_user: str = Depends(get_current_user)):
    """The user corrects or updates a fact: a new version, the old one kept as history (no card: their own change)."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.supersede(
        current_user, s, fact_id, payload.statement, origin="user", source={"by_user": True}, since=payload.since,
        category=payload.category if payload.category != "diğer" else None, recurring=payload.recurring))


@router.post("/api/v1/memory/facts/{fact_id}/remove")  # The same as DELETE, with the answer for the app's client.
@router.delete("/api/v1/memory/facts/{fact_id}")
async def memory_fact_remove(fact_id: int, request: Request, current_user: str = Depends(get_current_user)):
    """Remove a fact with its history: {"trash_id"} (undo: POST /memory/trash/restore) or, for a locked fact,
    {"restore": id} (undo: POST /memory/facts/{id}/restore)."""
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.remove_fact(current_user, s, fact_id))


@router.post("/api/v1/memory/facts/{fact_id}/restore")
async def memory_fact_restore(fact_id: int, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_entities as me
    return await _ben(request, current_user, lambda s: me.restore_fact(current_user, s, fact_id))


@router.get("/api/v1/memory/proposals")
async def memory_proposals(request: Request, state: str = "pending", ids: str = "",
                           current_user: str = Depends(get_current_user)):
    """The cards: proposals in `state` ("" = every state), newest first; `ids` (comma separated) picks some."""
    from orion.kernel import memory_proposals as me
    wanted = [int(i) for i in ids.split(",") if i.strip().isdigit()][:100] or None
    return await _ben(request, current_user, lambda s: _proposal_list(me, current_user, s, state, wanted))


async def _proposal_list(me, user: str, settings, state: str, ids):
    return {"proposals": await me.proposals(user, settings, state or None, ids)}


@router.post("/api/v1/memory/proposals/answer")
async def memory_proposals_answer(payload: ProposalAnswer, request: Request,
                                  current_user: str = Depends(get_current_user)):
    """The user's answer to one card or a batch card: accept / reject ids, `edits` {id: own sentence} (Düzenle)."""
    from orion.kernel import memory_proposals as me
    edits = {int(k): v[:2000] for k, v in payload.edits.items() if k.isdigit()}
    return await _ben(request, current_user, lambda s: _proposal_answer(me, current_user, s, payload, edits))


async def _proposal_answer(me, user: str, settings, payload: ProposalAnswer, edits: dict):
    return {"results": await me.decide(user, settings, payload.accept, payload.reject, edits)}


class RecentUndo(BaseModel):
    type: str = Field(max_length=10)  # fact | event | note
    id: str = Field(max_length=64)


@router.get("/api/v1/memory/review")
async def memory_review(request: Request, current_user: str = Depends(get_current_user)):
    """The page's two badges: {"pending": cards waiting for an answer, "recent": additions of the last 7 days}."""
    from orion.kernel import memory_recent
    return await _ben(request, current_user, lambda s: memory_recent.review(current_user, s))


@router.get("/api/v1/memory/recent")
async def memory_recent_list(request: Request, days: int = 30, limit: int = 50, offset: int = 0,
                             current_user: str = Depends(get_current_user)):
    """Son eklenenler: what the assistant wrote into the memory by itself, newest first, with its source chat:
    {"items": [{type, id, key, title, text, locked, origin, added_at, chat}], "more"}."""
    from orion.kernel import memory_recent
    return await _ben(request, current_user, lambda s: memory_recent.recent(current_user, s, days, limit, offset))


@router.post("/api/v1/memory/recent/undo")
async def memory_recent_undo(payload: RecentUndo, request: Request, current_user: str = Depends(get_current_user)):
    """Geri al: take one addition back: {"trash_id"} (undo of the undo: POST /memory/trash/restore) or {"restore":
    {"fact": id}} for a locked fact (POST /memory/facts/{id}/restore). 404 when it is not an addition of this user's."""
    from orion.kernel import memory_recent
    return await _ben(request, current_user, lambda s: memory_recent.undo(current_user, s, payload.type, payload.id))


# --- Profile block: what every chat is told about the user (memory_profile; memory_v2) ---------------------------------------

class ProfileLine(BaseModel):
    text: str = Field(max_length=2000)
    fact_id: int | None = None


class ProfilePinned(BaseModel):
    items: list[ProfileLine] = Field(default_factory=list, max_length=100)


class ProfilePin(BaseModel):
    fact_id: int
    text: str | None = Field(default=None, max_length=2000)  # The user's own wording of it (else the fact's sentence).


@router.get("/api/v1/memory/profile")
async def memory_profile_get(request: Request, current_user: str = Depends(get_current_user)):
    """The profile block as the next "Asistanla konuş" request would carry it in its system prompt ({text, tokens, limit (the
    `memory_profile_tokens` budget, default 800), overflow, left_out}), the pinned lines with their state (ok / replaced / gone /
    locked) and the generated lines (the user's own facts only) with their fact id, `why` (pinned | own_fact) and category."""
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.get(current_user, settings))


@router.put("/api/v1/memory/profile/pinned")
async def memory_profile_set_pinned(payload: ProfilePinned, request: Request, current_user: str = Depends(get_current_user)):
    """Replace the pinned lines (they always go into the block; the generated part fills what is left)."""
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.set_pinned(
        current_user, settings, [i.model_dump() for i in payload.items]))


@router.post("/api/v1/memory/profile/pin")
async def memory_profile_pin(payload: ProfilePin, request: Request, current_user: str = Depends(get_current_user)):
    """Pin one current fact (its sentence, or the user's own wording)."""
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.pin_fact(
        current_user, settings, payload.fact_id, payload.text))


@router.post("/api/v1/memory/profile/compact")
async def memory_profile_compact(request: Request, current_user: str = Depends(get_current_user)):
    """"Profili düzenle": the background model compacts the non-pinned part of the profile now (the same the Hub starts by
    itself past the hard limit). Answers like GET /memory/profile. 400 when there is nothing to shorten."""
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.compact_profile(current_user, settings))


@router.get("/api/v1/memory/profile/why/{fact_id}")
async def memory_profile_why(fact_id: int, request: Request, current_user: str = Depends(get_current_user)):
    """Why a line is in the profile and where the fact comes from: {fact_id, reason (pinned | own_fact | not_in_block),
    statement, category, about, sources}. 404 for a fact that is not the user's."""
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.why(current_user, settings, fact_id))


@router.delete("/api/v1/memory/profile/pin/{fact_id}")
async def memory_profile_unpin(fact_id: int, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_profile
    return await _ben(request, current_user, lambda settings: memory_profile.unpin_fact(current_user, settings, fact_id))


# --- Extraction: reading chats for memories, shadow mode and the quality-gate report (memory_extract; memory_v2) -------------

class ExtractionLive(BaseModel):
    enabled: bool
    confirm: bool = False  # Needed to turn automatic writing on: the user has read the report.


class ExtractionBacklog(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)


@router.get("/api/v1/memory/extraction")
async def memory_extraction_status(request: Request, limit: int = 20, current_user: str = Depends(get_current_user)):
    """The extraction: whether it is on, whether automatic writing is on (off by default), the queue and the newest runs."""
    from orion.kernel import memory_extract as mx

    async def call(settings):
        await mx.resume(current_user, settings)
        return await mx.status(current_user, settings, limit)
    return await _ben(request, current_user, call)


@router.get("/api/v1/memory/extraction/report")
async def memory_extraction_report(request: Request, run_id: int | None = None, chat_id: str | None = Query(default=None, max_length=120),
                                   limit: int = 100, verdict: str | None = Query(default=None, max_length=12),
                                   reveal: bool = False, format: str = Query(default="json", pattern="^(json|text)$"),
                                   current_user: str = Depends(get_current_user)):
    """The quality-gate report: what the extraction found and what it would have written (nothing is written in shadow
    mode). `format=text` is the same as readable lines. Locked (sensitive) sentences only with `reveal=true`."""
    from orion.kernel import memory_extract as mx

    async def call(settings):
        rep = await mx.report(current_user, settings, run_id, chat_id, limit, reveal, verdict)
        return Response(mx.report_text(rep), media_type="text/plain; charset=utf-8") if format == "text" else rep
    return await _ben(request, current_user, call)


@router.post("/api/v1/memory/extraction/backlog")
async def memory_extraction_backlog(payload: ExtractionBacklog, request: Request, current_user: str = Depends(get_current_user)):
    """Queue the chats that have messages not read yet (newest first, at most `limit`): how the old chats go through the
    quality gate. Runs one at a time in the background; cancel any time."""
    from orion.kernel import memory_extract as mx
    return await _ben(request, current_user, lambda s: mx.enqueue_backlog(current_user, s, payload.limit))


@router.post("/api/v1/memory/extraction/cancel")
async def memory_extraction_cancel_all(request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_extract as mx
    return await _ben(request, current_user, lambda s: mx.cancel_all(current_user))


@router.post("/api/v1/memory/extraction/runs/{run_id}/cancel")
async def memory_extraction_cancel(run_id: int, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_extract as mx
    return await _ben(request, current_user, lambda s: mx.cancel_run(current_user, run_id))


@router.put("/api/v1/memory/extraction/live")
async def memory_extraction_live(payload: ExtractionLive, request: Request, current_user: str = Depends(get_current_user)):
    """Automatic writing (add-only; conflicts become cards) on or off. Off by default; turning it on needs confirm=true."""
    from orion.kernel import memory_extract as mx
    return await _ben(request, current_user, lambda s: mx.set_live(current_user, s, payload.enabled, payload.confirm))


@router.get("/api/v1/memory/extraction/daily")
async def memory_extraction_daily(request: Request, current_user: str = Depends(get_current_user)):
    """The first week after automatic writing was turned on: one summary per day ("Dün hafızaya 4 bilgi, 2 olay eklendi")."""
    from orion.kernel import memory_extract as mx
    return await _ben(request, current_user, lambda s: mx.daily_summary(current_user, s))


@router.get("/api/v1/chats/{chat_id}/memory/summary")
async def memory_chat_summary(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    """The short summary the extraction keeps of a chat ({"summary": null} until it was read)."""
    from orion.kernel import memory_extract as mx
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    return await _ben(request, current_user, lambda s: mx.chat_summary(current_user, chat_id))


# --- Yaşadıklarım: events (memory_events; behind the user's memory_v2) -------------------------------

class EventBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=4000)
    date: str | None = Field(default=None, max_length=10)  # YYYY-MM-DD, YYYY-MM or YYYY
    end_date: str | None = Field(default=None, max_length=10)
    precision: str | None = Field(default=None, max_length=10)  # day | month | year | approx
    said_as: str | None = Field(default=None, max_length=120)
    mood: str | None = Field(default=None, max_length=40)
    participants: list[str] | None = Field(default=None, max_length=20)  # names, relations or "#<entity id>"
    place: str | None = Field(default=None, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=100)  # memory group keys (photos...)


class MembersBody(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)


@router.get("/api/v1/memory/events")
async def memory_events_list(request: Request, start: str = "", end: str = "", entity_id: int | None = None, limit: int = 50,
                             offset: int = 0, current_user: str = Depends(get_current_user)):
    """The Yaşadıklarım timeline: {"events", "more", "years"}, newest day first; `start` / `end` a range, `entity_id` one
    person's or place's events."""
    from orion.kernel import memory_events as ev
    return await _ben(request, current_user, lambda s: ev.list_events(current_user, s, start[:10], end[:10], entity_id,
                                                                    limit, offset))


@router.get("/api/v1/memory/events/upcoming")
async def memory_events_upcoming(request: Request, days: int = 30, current_user: str = Depends(get_current_user)):
    """The yearly recurrences (birthdays, anniversaries) in the next `days` days: {"upcoming": [...]}."""
    from orion.kernel import memory_events as ev

    async def call(settings):
        return {"upcoming": await ev.upcoming(current_user, settings, days)}
    return await _ben(request, current_user, call)


@router.post("/api/v1/memory/events")
async def memory_event_create(payload: EventBody, request: Request, current_user: str = Depends(get_current_user)):
    """The user writes an event (no card; source: the user): {"event"} or {"same_as"}."""
    from orion.kernel import memory_events as ev, memory_entities as me

    async def call(settings):
        when = ev.resolve_date(payload.date, payload.end_date, payload.precision, payload.said_as)
        async with me._db() as conn:
            groups = await ev.groups_exist(conn, current_user, payload.items) if payload.items else []
        return await ev.add_event(current_user, settings, payload.title or "", when, body=payload.body, mood=payload.mood,
                                  participants=payload.participants, place=payload.place, items=groups, origin="user",
                                  source={"by_user": True})
    return await _ben(request, current_user, call)


@router.get("/api/v1/memory/events/{event_id}")
async def memory_event_card(event_id: int, request: Request, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory_events as ev
    return await _ben(request, current_user, lambda s: ev.get_event(current_user, s, event_id))


@router.put("/api/v1/memory/events/{event_id}")  # PUT too: the app's client has no PATCH.
@router.patch("/api/v1/memory/events/{event_id}")
async def memory_event_update(event_id: int, payload: EventBody, request: Request,
                              current_user: str = Depends(get_current_user)):
    """The user corrects an event: a new version, the old one kept as history. What is left out stays as it was."""
    from orion.kernel import memory_events as ev

    async def call(settings):
        when = ev.resolve_date(payload.date, payload.end_date, payload.precision, payload.said_as) \
            if payload.date is not None else None
        return await ev.replace_event(current_user, settings, event_id, title=payload.title, body=payload.body, when=when,
                                      mood=payload.mood, participants=payload.participants, place=payload.place)
    return await _ben(request, current_user, call)


@router.post("/api/v1/memory/events/{event_id}/remove")  # The same as DELETE, with the answer for the app's client.
@router.delete("/api/v1/memory/events/{event_id}")
async def memory_event_remove(event_id: int, request: Request, current_user: str = Depends(get_current_user)):
    """Remove an event: {"trash_id"} (undo: POST /memory/trash/restore)."""
    from orion.kernel import memory_events as ev
    return await _ben(request, current_user, lambda s: ev.remove_event(current_user, s, event_id))


@router.post("/api/v1/memory/events/{event_id}/items")
async def memory_event_items(event_id: int, payload: MembersBody, request: Request,
                             current_user: str = Depends(get_current_user)):
    """Link photos / videos (memory group keys) to an event, or unlink them: {"linked", "unlinked"}."""
    from orion.kernel import memory_events as ev
    return await _ben(request, current_user, lambda s: ev.set_items(current_user, s, event_id, payload.add, payload.remove))


@router.post("/api/v1/memory/collections/revert")
async def memory_collections_revert(request: Request, current_user: str = Depends(get_current_user)):
    """Undo the collections (docs/hafiza-tasarimi.md §13): memory_v2 off for this user, the rows' collection emptied,
    the collections gone. The memories themselves are untouched."""
    from orion.kernel import memory, memory_collections
    try:
        await update_runtime_settings(request.app.state.redis, {"memory_v2": "false"}, current_user)
        return await memory_collections.revert(current_user)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/move/cancel")
async def memory_move_cancel(current_user: str = Depends(get_current_user)):
    from orion.kernel import memory, memory_move
    try:
        return {"job": await memory_move.cancel(current_user)}
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/vectors/remove")
@router.post("/api/v1/memory/move/cleanup")
async def memory_move_cleanup(payload: MemoryMove, request: Request, current_user: str = Depends(get_current_user)):
    """Remove another model's vectors from the memories that have the active model's too."""
    from orion.kernel import memory, memory_groups
    try:
        return {"deleted": await memory_groups.delete_copied(
            current_user, payload.vector_space, await get_runtime_settings(request.app.state.redis, current_user))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory")
async def add_memory_note(payload: MemoryNote, request: Request, current_user: str = Depends(get_current_user)):
    """A note as a new memory, or added to the memory `group_id`. {"added", "group_id"}: a staged note with files
    passes the group on to the files' calls, so they are one memory."""
    from orion.kernel import memory
    try:
        return await memory.add_note_to(current_user, payload.text,
                                        await get_runtime_settings(request.app.state.redis, current_user),
                                        group=payload.group_id or None, title=payload.title)
    except LookupError as exc:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


class MemoryFile(BaseModel):
    name: str = Field(default="", max_length=300)
    data: str = Field(default="", max_length=90 * 1024 * 1024)  # A data URL: base64 of at most 64 MB.
    group_id: str | None = Field(default=None, max_length=64)  # Add to this memory instead of a new one.
    caption: str = Field(default="", max_length=4000)  # What the user wrote about the file.


@router.post("/api/v1/memory/file")
async def add_memory_file(payload: MemoryFile, request: Request, current_user: str = Depends(get_current_user)):
    """An image, audio, video or text (.txt/.md) file the user adds on the memory page, sent as a data URL: a new
    memory, or a part of the memory `group_id`. A video is embedded in the background: the answer has its "job"."""
    from orion.kernel import memory
    from orion.kernel.media_store import decode_data_url
    try:
        mime, raw = decode_data_url(payload.data)
    except Exception as exc:
        raise HTTPException(400, "Dosya okunamadı.") from exc
    try:
        return await memory.add_file(current_user, payload.name, mime, raw,
                                     await get_runtime_settings(request.app.state.redis, current_user),
                                     group=payload.group_id or None, caption=payload.caption)
    except LookupError as exc:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


class MemorySearchInput(BaseModel):
    name: str = Field(default="", max_length=300)
    data: str = Field(default="", max_length=90 * 1024 * 1024)
    text: str = Field(default="", max_length=1_000_000)  # A long passage: searched by its windows' mean.
    negative: bool = False  # "benzemesin": memories like this are left out.


class MemorySearch(BaseModel):
    """A search with a file (a data URL: image, audio, video or .txt/.md) or with the query vector an earlier
    search by file answered with (the next page, without sending the file again)."""
    name: str = Field(default="", max_length=300)
    data: str = Field(default="", max_length=90 * 1024 * 1024)
    vector: list[float] | None = Field(default=None, max_length=4096)
    vector_space: str = Field(default="", max_length=100)  # The query vector's space.
    filter_space: str = Field(default="active", max_length=100)  # The page's model filter.
    kind: str = Field(default="all", max_length=20)
    limit: int = 20
    offset: int = 0
    # Several inputs at once: files (name + data URL) and texts; `vectors` are the query vectors an earlier answer
    # gave (the next page). mode "together": one query, the normalized mean of the inputs; "each": every input
    # ranked on its own and ranked by its best input (memory_groups.rank_matches).
    inputs: list[MemorySearchInput] = Field(default_factory=list, max_length=8)
    vectors: list[list[float]] = Field(default_factory=list, max_length=8)
    q: str = Field(default="", max_length=1_000_000)  # The texts, for the words' own matches.
    mode: str = Field(default="together", max_length=20)
    kinds: list[str] = Field(default_factory=list, max_length=8)
    exclude: list[str] = Field(default_factory=list, max_length=5)  # "benzemesin" texts (also as inputs).
    exclude_vectors: list[list[float]] = Field(default_factory=list, max_length=5)  # "benzemesin" files, made.
    labels: list[str] = Field(default_factory=list, max_length=8)  # The inputs' names an earlier answer gave.
    collections: list[str] = Field(default_factory=list, max_length=10)  # Only these collections (memory_v2).


@router.post("/api/v1/memory/search")
async def search_memory_by_file(payload: MemorySearch, request: Request, current_user: str = Depends(get_current_user)):
    """Memories like a file (memory.file_query_vector), as GET /api/v1/memory answers a text search, with the query
    vector and its space for the next page and for the detail's part scores."""
    from orion.kernel import memory, memory_groups
    from orion.kernel.media_store import decode_data_url
    settings = await get_runtime_settings(request.app.state.redis, current_user)
    if payload.inputs or payload.vectors:
        return await latest_search(current_user, _search_memory_multi(payload, settings, current_user))
    try:
        if payload.vector:
            if len(payload.vector) != 768:
                raise HTTPException(400, "Arama vektörü geçersiz.")
            space, vector, kind = payload.vector_space or await memory.current_space(settings), \
                memory._vector(payload.vector), None
        else:
            try:
                mime, raw = decode_data_url(payload.data)
            except Exception as exc:
                raise HTTPException(400, "Dosya okunamadı.") from exc
            space, vector, kind = await memory.file_query_vector(payload.name, mime, raw, settings)
        found = await memory_groups.list_groups(
            current_user, settings, "", max(1, min(payload.limit, 100)), max(0, payload.offset),
            payload.filter_space[:100], payload.kind[:20], vector=vector, vector_space_of_query=space,
            collections=[c[:120] for c in payload.collections] or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    import json
    found["query"] = {"vector": [round(v, 6) for v in json.loads(vector)], "vector_space": space, "kind": kind,
                      "name": payload.name[:200]}
    return found


INPUT_LABELS = {"query": "metin", "text": "metin dosyası", "image": "görsel", "audio": "ses", "video": "video"}


def input_labels(kinds: list[str], count: int, names: list[str] | None = None) -> list[str]:
    """What each search input is called on a card: its own name when known (a text in quotes: '"kuş"', a file by its
    name), else its kind ("metin", "görsel"; two of a kind are numbered)."""
    kinds_named = [INPUT_LABELS.get(kinds[i], "girdi") if i < len(kinds) else "girdi" for i in range(count)]
    labels = [f"{n} {kinds_named[:i + 1].count(n)}" if kinds_named.count(n) > 1 else n
              for i, n in enumerate(kinds_named)]
    return [(names[i] if names and i < len(names) and names[i] else labels[i])[:80] for i in range(count)]


def input_name(one) -> str:
    """A search input's own name: its text in quotes (cut), or its file's name."""
    import json
    text = " ".join(one.text.split())
    if text:
        return json.dumps(text if len(text) <= 40 else text[:39] + "…", ensure_ascii=False)
    return " ".join(one.name.split())[:80]


async def _search_memory_multi(payload: MemorySearch, settings, current_user: str) -> dict:
    """Several files and texts at once: each becomes a query vector (memory.file_query_vector, a text as a query);
    "together" searches with their normalized mean, "each" fuses each one's ranking. The answer's `query` has the
    vectors (the next page sends them back) and the combined vector (the detail's part scores)."""
    import json
    from orion.kernel import memory, memory_groups
    from orion.kernel.media_store import decode_data_url
    mode = payload.mode if payload.mode in ("together", "each") else "together"
    try:
        space, vectors, kinds, names = payload.vector_space or None, [], [], []
        exclude = [t[:200] for t in payload.exclude if t.strip()]
        if any(len(v) != 768 for v in payload.exclude_vectors):
            raise HTTPException(400, "Arama vektörü geçersiz.")
        exclude_vectors = [list(v) for v in payload.exclude_vectors]
        if payload.vectors:
            if any(len(v) != 768 for v in payload.vectors):
                raise HTTPException(400, "Arama vektörü geçersiz.")
            vectors = [list(v) for v in payload.vectors]
            kinds = [k[:20] for k in payload.kinds][:len(vectors)]
            names = [n[:80] for n in payload.labels][:len(vectors)]
            space = space or await memory.current_space(settings)
        else:
            for one in payload.inputs:
                text = one.text.strip()[:memory.MAX_QUERY_CHARS]
                if one.negative and text:
                    exclude.append(text[:200])
                    continue
                if text:
                    space, vector, reason = await memory._query_vector(text, settings)
                    if vector is None:
                        raise HTTPException(503, reason or "Anlamla arama kapalı.")
                    kind = "query"
                else:
                    try:
                        mime, raw = decode_data_url(one.data)
                    except Exception as exc:
                        raise HTTPException(400, f"Dosya okunamadı: {one.name[:60]}") from exc
                    space, vector, kind = await memory.file_query_vector(one.name, mime, raw, settings)
                    if one.negative:
                        exclude_vectors.append(json.loads(vector))
                        continue
                vectors.append(json.loads(vector))
                kinds.append(kind)
                names.append(input_name(one))
        if not vectors:
            raise HTTPException(400, "Aranacak bir şey yok: en az bir \"benzesin\" metni ya da dosyası ekle.")
        found, combined = await memory_groups.search_vectors(
            current_user, settings, vectors, input_labels(kinds, len(vectors), names), space, mode,
            payload.q[:memory.MAX_QUERY_CHARS], limit=max(1, min(payload.limit, 100)), offset=max(0, payload.offset),
            vector_space=payload.filter_space[:100], kind=payload.kind[:20], exclude=exclude,
            exclude_vectors=[memory._vector(v) for v in exclude_vectors],
            collections=[c[:120] for c in payload.collections] or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    windows = await query_note(payload.q)
    if windows:
        found["query_windows"] = windows
    found["query"] = {"exclude_vectors": [[round(v, 6) for v in one] for one in exclude_vectors],
                      "vector": [round(v, 6) for v in combined], "vectors": [[round(v, 6) for v in one] for one in vectors],
                      "vector_space": space, "kinds": kinds, "mode": mode,
                      "labels": input_labels(kinds, len(vectors), names)}
    return found


class MemoryMatch(BaseModel):
    kind: str = Field(default="all", max_length=20)  # The page's kind filter: only that kind's parts count.
    q: str = Field(default="", max_length=1_000_000)
    vector: list[float] | None = Field(default=None, max_length=4096)
    vector_space: str = Field(default="", max_length=100)


@router.post("/api/v1/memory/groups/{group_id}/match")
async def memory_group_match(group_id: str, payload: MemoryMatch, request: Request,
                             current_user: str = Depends(get_current_user)):
    """Each part's own score and match in one memory for the page's search (memory_groups.part_scores):
    {"parts": {id: {"score", "match", "matched", "best"}}, "best", "best_part", "matched", "whole_score"}."""
    from orion.kernel import memory, memory_groups
    vector = None
    if payload.vector:
        if len(payload.vector) != 768:
            raise HTTPException(400, "Arama vektörü geçersiz.")
        vector = memory._vector(payload.vector)
    try:
        found = await memory_groups.part_scores(
            current_user, group_id[:64], await get_runtime_settings(request.app.state.redis, current_user),
            payload.q[:memory.MAX_QUERY_CHARS], vector, payload.vector_space or None,
            payload.kind[:20])
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.")
    return found


@router.get("/api/v1/memory/pending-delete/{token}")
async def memory_pending_status(token: str, current_user: str = Depends(get_current_user)):
    """A delete the chat model asked for: {"state": pending | claimed | deleted | declined | expired, "will_delete",
    "whole_memory", "deleted"} (the confirmation card's content)."""
    from orion.kernel import memory, memory_pending
    try:
        found = await memory_pending.status(current_user, token[:64])
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Onay bulunamadı.")
    return found


@router.post("/api/v1/memory/pending-delete/{token}/confirm")
async def memory_pending_confirm(token: str, current_user: str = Depends(get_current_user)):
    """The user pressed Sil: delete what the model asked to (once). Answers the confirmation's state."""
    from orion.kernel import memory, memory_pending
    try:
        found = await memory_pending.resolve(current_user, token[:64], True)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Onay bulunamadı.")
    return found


@router.post("/api/v1/memory/pending-delete/{token}/decline")
async def memory_pending_decline(token: str, current_user: str = Depends(get_current_user)):
    """The user declined: nothing is deleted; the model can see it with its confirmation id."""
    from orion.kernel import memory, memory_pending
    try:
        found = await memory_pending.resolve(current_user, token[:64], False)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if found is None:
        raise HTTPException(404, "Onay bulunamadı.")
    return found


@router.get("/api/v1/memory/media-jobs")
async def memory_media_jobs(current_user: str = Depends(get_current_user)):
    """Videos being embedded in the background and those that ended in the last minutes (memory_media_jobs)."""
    from orion.kernel import memory, memory_media_jobs as jobs
    try:
        return {"jobs": await jobs.jobs(current_user)}
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/media-jobs/{job_id}/cancel")
async def memory_media_job_cancel(job_id: str, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory, memory_media_jobs as jobs
    try:
        if not await jobs.cancel(current_user, job_id[:64]):
            raise HTTPException(404, "İş bulunamadı.")
        return {"jobs": await jobs.jobs(current_user)}
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/v1/memory/{item_id}/frame")
async def memory_item_frame(item_id: int, request: Request, at: float | None = None,
                            current_user: str = Depends(get_current_user)):
    """A JPEG frame of a memory video at `at` seconds (default: the row's segment start, a whole video's first
    second): the page's thumbnails. Same 404s as the media."""
    import asyncio
    from orion.kernel import memory, memory_pieces
    try:
        mime, raw, media_id = await memory.read_item_media(current_user, item_id)
        span = await memory.item_span(current_user, item_id)
    except LookupError as exc:
        raise HTTPException(404, 'Media not found') from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not mime.lower().startswith("video/"):
        raise HTTPException(404, 'Media not found')
    second = max(0.0, float(at if at is not None else (span[0] + min(1.0, (span[1] - span[0]) / 2) if span else 1.0)))
    etag = f'"{media_id}-{second:.2f}"'
    headers = {'Cache-Control': 'private, max-age=2592000, immutable', 'ETag': etag,
               'X-Content-Type-Options': 'nosniff'}
    if etag in [tag.strip() for tag in request.headers.get('if-none-match', '').split(',')]:
        return Response(status_code=304, headers=headers)
    try:
        frame = await asyncio.to_thread(memory_pieces.frame_at, raw, second)
    except Exception as exc:
        raise HTTPException(404, 'Media not found') from exc
    return Response(content=frame, media_type="image/jpeg", headers=headers)


@router.delete("/api/v1/memory/{item_id}")
async def delete_memory_item(item_id: int, current_user: str = Depends(get_current_user)):
    """Move one part (and its copies) to the trash."""
    from orion.kernel import memory, memory_trash
    try:
        removed = await memory_trash.trash_items(current_user, [item_id])
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not removed["removed"]:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.")
    return {"deleted": [row["id"] for row in removed["removed"]], "trash_id": removed["trash_id"]}


@router.get("/api/v1/memory/{item_id}/text")
async def memory_item_text(item_id: int, request: Request, current_user: str = Depends(get_current_user)):
    """The whole text of the item a text row belongs to (a note, a chat's text, a text file: its sections joined),
    as a plain text file: the viewer opens it like any text attachment, whatever its length. Another user's row,
    a missing row and a media row are the same 404."""
    from orion.kernel import memory, memory_groups
    try:
        doc = await memory_groups.item_document(current_user, item_id,
                                                await get_runtime_settings(request.app.state.redis, current_user))
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if doc is None:
        raise HTTPException(404, 'Text not found')
    from urllib.parse import quote
    return Response(content=doc["text"].encode("utf-8"), media_type="text/plain; charset=utf-8",
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'X-File-Name': quote(doc["name"])})


@router.get("/api/v1/memory/{item_id}/media")
async def memory_item_media(item_id: int, request: Request, current_user: str = Depends(get_current_user)):
    """The image or audio a memory row keeps. Another user's row, a missing row and a row without media are the
    same 404."""
    from orion.kernel import memory
    try:
        mime, raw, media_id = await memory.read_item_media(current_user, item_id)
    except LookupError as exc:
        raise HTTPException(404, 'Media not found') from exc
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    etag = f'"{media_id}"'
    headers = {'Cache-Control': 'private, max-age=2592000, immutable', 'ETag': etag,
               'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'"}
    if etag in [tag.strip() for tag in request.headers.get('if-none-match', '').split(',')]:
        return Response(status_code=304, headers=headers)
    return Response(content=raw, media_type=mime, headers=headers)


@router.post("/api/v1/memory/delete")
async def delete_memory_items(payload: MemoryDelete, current_user: str = Depends(get_current_user)):
    """Move parts, whole parts (items) and whole memories to the trash as one removal: {"deleted": [part ids],
    "trash_id"} (POST /api/v1/memory/trash/restore with it is the undo)."""
    from orion.kernel import memory, memory_trash
    try:
        removed = await memory_trash.trash(current_user, payload.ids, payload.item_ids, payload.group_ids)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"deleted": [row["id"] for row in removed["removed"]], "trash_id": removed["trash_id"]}


class MemoryTrashIds(BaseModel):
    trash_ids: list[str] = Field(default_factory=list, max_length=200)
    all: bool = False  # Purge: the whole trash.
    confirm: bool = False  # Purge: the user confirmed "geri alınamaz" (a purge without it is refused).


@router.get("/api/v1/memory/trash")
async def memory_trash_list(request: Request, limit: int = 50, offset: int = 0,
                            current_user: str = Depends(get_current_user)):
    """The removals in the trash, newest first, with their days left (memory_trash.list_trash)."""
    from orion.kernel import memory, memory_trash
    try:
        return await memory_trash.list_trash(current_user, await get_runtime_settings(request.app.state.redis,
                                                                                      current_user), limit, offset)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/trash/restore")
async def memory_trash_restore(payload: MemoryTrashIds, current_user: str = Depends(get_current_user)):
    from orion.kernel import memory, memory_trash
    try:
        return await memory_trash.restore(current_user, payload.trash_ids)
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/memory/trash/purge")
async def memory_trash_purge(payload: MemoryTrashIds, request: Request, current_user: str = Depends(get_current_user)):
    """Delete removals for good (every model's vectors, the file when nothing else links it, the text index): only
    with `confirm` (the page's own "geri alınamaz" question), and only what is in the trash."""
    from orion.kernel import memory, memory_trash
    if not payload.confirm:
        raise HTTPException(400, "Kalıcı silme onaylanmadı.")
    if not payload.all and not payload.trash_ids:
        raise HTTPException(400, "Silinecek bir şey seçilmedi.")
    try:
        return await memory_trash.purge(current_user, None if payload.all else payload.trash_ids,
                                        await get_runtime_settings(request.app.state.redis, current_user))
    except memory.MemoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


class SettingsUpdateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    values: dict[str, str] = Field(default_factory=dict)


async def _validate_settings_update(values: dict[str, str], user_id: str) -> None:
    for key in values:
        if key.lower() not in _allowed_keys:
            raise HTTPException(status_code=400, detail=f"Geçersiz ayar anahtarı: {key}")
    try:
        await validate_model_updates(values, user_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _save_settings_update(redis: Redis, payload: SettingsUpdateRequest) -> RuntimeSettings:
    try:
        return await update_runtime_settings(redis, payload.values, payload.user_id)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise HTTPException(status_code=422, detail=f"{error['loc'][0]}: {error['msg']}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc



from orion.kernel.registry import check_db_health

@router.get("/api/v1/assistant/capabilities")
async def assistant_capabilities(current_user: str = Depends(get_current_user)):
    # Every spoken reply is adaptive now; an older app that still asks finds it here.
    return {"speech_modes": ["adaptive"], "speech_protocol": 1}


class AssistantChatClaim(BaseModel):
    chat_id: str = Field(min_length=1, max_length=120)


@router.get("/api/v1/assistant/chat")
async def get_assistant_chat(request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.services.assistant_chat import current_assistant_chat
    return {"chat_id": await current_assistant_chat(request.app.state.redis, current_user)}


@router.post("/api/v1/assistant/chat")
async def claim_assistant_chat_endpoint(payload: AssistantChatClaim, request: Request,
                                        current_user: str = Depends(get_current_user)):
    """A device's earlier assistant chat; the answer is the shared one."""
    from orion.api.services.assistant_chat import claim_assistant_chat
    return {"chat_id": await claim_assistant_chat(request.app.state.redis, current_user, payload.chat_id)}


class WakeClaim(BaseModel):
    device: str = Field(min_length=1, max_length=120)


@router.post("/api/v1/assistant/wake")
async def claim_wake_endpoint(payload: WakeClaim, request: Request, current_user: str = Depends(get_current_user)):
    """A device heard "Orion": whether it takes the turn (another device beside it may have heard it first)."""
    from orion.api.services.assistant_chat import claim_wake
    granted, owner = await claim_wake(request.app.state.redis, current_user, payload.device)
    return {"granted": granted, "owner": owner}


@router.post("/api/v1/audio/normalize")
async def normalize_audio_endpoint(payload: dict, current_user: str = Depends(get_current_user)):
    from orion.contracts.http import Attachment
    from orion.api.services.input_audio import normalize_input_audio
    try:
        attachment = Attachment.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, "Geçersiz ses eki.") from exc
    if attachment.isText or not attachment.mime_type.startswith("audio/"):
        raise HTTPException(422, "Bir ses eki seç.")
    return (await normalize_input_audio(attachment)).model_dump(exclude_none=True)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "timestamp": utc_now()}

@router.get("/ready")
async def ready(request: Request) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    redis_ok = False
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    db_ok = await check_db_health(timeout=1.0)

    return {
        "ready": redis_ok,
        "redis": "connected" if redis_ok else "disconnected",
        "database": "connected" if db_ok else "disconnected",
    }

@router.post("/api/v1/chats/messages", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_chat_message_endpoint(payload: JobCreateRequest, request: Request, current_user: str = Depends(get_current_user)) -> JobCreateResponse:
    from orion.kernel import activity
    from orion.api.access_boundary import is_desktop_peer
    if payload.user_id != current_user:
        payload.user_id = current_user
    redis = request.app.state.redis
    await activity.require_not_draining(redis)  # An explicit "update now" is about to stop the Hub.
    if payload.assistant:
        from orion.api.services.assistant_chat import create_assistant_message
        created = await create_assistant_message(redis, payload)
    elif payload.chat_id:
        async with redis.lock(f'chat:lifecycle:{payload.chat_id}', timeout=120, blocking_timeout=10):
            created = await create_job(redis, payload)
    else:
        created = await create_job(redis, payload)
    if getattr(request.app.state, 'mobile', False) or not is_desktop_peer(request.scope):
        await activity.mark_remote_chat(redis, created.chat_id)
    return created

@router.post("/api/v1/chats/{chat_id}/stop", response_model=JobStopResponse)
async def stop_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)) -> JobStopResponse:
    return await stop_job(request.app.state.redis, current_user, chat_id)

@router.post("/api/v1/chats/{chat_id}/turns/{turn_id}/stop", response_model=JobStopResponse)
async def stop_chat_turn_endpoint(
    chat_id: str,
    turn_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
) -> JobStopResponse:
    return await stop_job(request.app.state.redis, current_user, chat_id, turn_id)

# Before "/api/v1/chats/{chat_id}", which would otherwise take "search" for a chat id.
@router.get("/api/v1/chats/search", response_model=list[dict])
async def search_chats_endpoint(q: str = Query(max_length=200), current_user: str = Depends(get_current_user)) -> list[dict]:
    return await search_chats(current_user, q)

@router.get("/api/v1/chats/{chat_id}", response_model=JobStatusResponse)
async def get_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)) -> JobStatusResponse:
    return await get_job(request.app.state.redis, current_user, chat_id)

@router.get("/api/v1/chats", response_model=list[dict])
async def list_chats_endpoint(request: Request, limit: int | None = Query(None, ge=1, le=500),
                              before: str | None = Query(None, max_length=300),
                              current_user: str = Depends(get_current_user)) -> list[dict]:
    """The newest `limit` chats, or those after `before` ("<updated_at>|<chat_id>" of the last one shown).
    Without `limit`, all of them (older clients)."""
    from orion.api.services.job_service import parse_chat_cursor
    return await get_user_chats(request.app.state.redis, current_user, limit=limit, before=parse_chat_cursor(before))

@router.get("/api/v1/chats/{chat_id}/history", response_model=list[dict])
async def get_chat_history_endpoint(chat_id: str, request: Request, include_audio: bool = True,
                                    limit: int | None = Query(None, ge=1, le=1000),
                                    before_turn: str | None = Query(None, max_length=200),
                                    max_chars: int | None = Query(None, ge=200, le=1_000_000),
                                    current_user: str = Depends(get_current_user)) -> list[dict]:
    return await get_chat_history(request.app.state.redis, current_user, chat_id, include_audio=include_audio,
                                  limit=limit, before_turn=before_turn, max_chars=max_chars)

@router.get("/api/v1/chats/{chat_id}/turns/{turn_id}/audio", response_model=dict)
async def get_turn_audio_endpoint(chat_id: str, turn_id: str, request: Request, current_user: str = Depends(get_current_user)) -> dict:
    return await get_turn_audio(request.app.state.redis, current_user, chat_id, turn_id)


@router.api_route('/api/v1/chats/{chat_id}/media/{media_id}', methods=['GET', 'HEAD'])
async def chat_media_endpoint(chat_id: str, media_id: str, request: Request,
                              current_user: str = Depends(get_current_user)):
    import re
    from orion.kernel.media_store import read_media, read_media_metadata
    # Allowed only through a chat of this user that links the file. Another user's chat, a chat that is not
    # there, a file the chat does not link and a hash nobody stored are the same 404: whether a file exists
    # somewhere is never revealed.
    if not re.fullmatch(r'[0-9a-f]{64}', media_id):
        raise HTTPException(404, 'Media not found')
    try:
        mime, total = await read_media_metadata(chat_id, media_id, user_id=current_user)
    except LookupError as exc:
        raise HTTPException(404, 'Media not found') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Media unavailable') from exc
    # The id is the SHA-256 of the bytes: a media path never changes what it holds. The device that fetched it
    # keeps it (private: never a shared cache) and asks again only after a month, or answers itself with 304.
    etag = f'"{media_id}"'
    headers = {'Cache-Control': 'private, max-age=2592000, immutable', 'ETag': etag,
               'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "sandbox; default-src 'none'", 'Accept-Ranges': 'bytes',
               'Content-Length': str(total)}
    if etag in [tag.strip() for tag in request.headers.get('if-none-match', '').split(',')]:
        return Response(status_code=304, headers={k: v for k, v in headers.items() if k != 'Content-Length'})
    status_code = 200
    start, length = 0, None
    if request.method == 'GET':
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', request.headers.get('range', ''))
        if match and any(match.groups()):
            first, last = match.groups()
            # Saturate huge numbers before int conversion or DB integer binding.
            def bounded(value):
                value = value.lstrip('0') or '0'
                return total if len(value) > len(str(total)) else min(int(value), total)
            if first:
                start = bounded(first)
                end = min(bounded(last), total - 1) if last else total - 1
            else:
                suffix = bounded(last)
                start, end = max(0, total - suffix), total - 1
            if start > end or start >= total:
                return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{total}', 'Content-Length': '0'})
            length = end - start + 1
            headers.update({'Content-Range': f'bytes {start}-{end}/{total}', 'Content-Length': str(length)})
            status_code = 206
    raw = b''
    if request.method != 'HEAD':
        from orion.kernel import activity
        from orion.api.access_boundary import is_desktop_peer
        try:
            async with activity.lease(request.app.state.redis, 'transfer', ttl=30,
                                      remote=getattr(request.app.state, 'mobile', False) or not is_desktop_peer(request.scope)):
                _, raw = await read_media(chat_id, media_id, offset=start, length=length, user_id=current_user)
        except LookupError as exc:
            raise HTTPException(404, 'Media not found') from exc
        except RuntimeError as exc:
            raise HTTPException(503, 'Media unavailable') from exc
    return Response(content=raw, media_type=mime,
                    headers=headers, status_code=status_code)

class ChatRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.delete('/api/v1/chats/{chat_id}/failed-turns/{key}')
async def delete_failed_turn_endpoint(chat_id: str, key: str, request: Request,
                                      current_user: str = Depends(get_current_user)):
    from orion.api.services.job_service import delete_failed_turn
    async with request.app.state.redis.lock(f'chat:lifecycle:{chat_id}', timeout=120, blocking_timeout=10):
        return await delete_failed_turn(request.app.state.redis, current_user, chat_id, key)


@router.post('/api/v1/chats/{chat_id}/discard-failed')
async def discard_failed_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.services.job_service import discard_failed_chat
    async with request.app.state.redis.lock(f'chat:lifecycle:{chat_id}', timeout=120, blocking_timeout=10):
        return await discard_failed_chat(request.app.state.redis, current_user, chat_id)

@router.patch("/api/v1/chats/{chat_id}/rename")
async def rename_chat_endpoint(chat_id: str, payload: ChatRenameRequest, request: Request, current_user: str = Depends(get_current_user)):
    return await rename_chat(request.app.state.redis, current_user, chat_id, payload.name)

@router.delete("/api/v1/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
    # A message sent meanwhile must not write the chat back after it is gone.
    async with request.app.state.redis.lock(f'chat:lifecycle:{chat_id}', timeout=120, blocking_timeout=10):
        return await delete_chat(request.app.state.redis, current_user, chat_id)

@router.get("/api/v1/admin/chats")
async def admin_list_all_chats(request: Request):
    _check_admin_key(request)
    redis: Redis = request.app.state.redis
    return await get_all_chats_admin(redis)

@router.delete("/api/v1/admin/chats/{chat_id}")
async def admin_delete_chat(chat_id: str, request: Request):
    _check_admin_key(request)
    redis: Redis = request.app.state.redis
    async with redis.lock(f'chat:lifecycle:{chat_id}', timeout=120, blocking_timeout=10):
        return await delete_chat(redis, user_id=None, chat_id=chat_id, admin=True)

@router.patch("/api/v1/admin/chats/{chat_id}/rename")
async def admin_rename_chat(chat_id: str, payload: ChatRenameRequest, request: Request):
    _check_admin_key(request)
    redis: Redis = request.app.state.redis
    return await rename_chat(redis, user_id=None, chat_id=chat_id, name=payload.name, admin=True)

@router.get("/api/v1/admin/chats/{chat_id}/history")
async def admin_chat_history(chat_id: str, request: Request):
    _check_admin_key(request)
    redis: Redis = request.app.state.redis
    return await get_chat_history_admin(redis, chat_id)

@router.get("/api/v1/chat/stream")
async def chat_stream(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    redis: Redis = request.app.state.redis
    user_id = current_user
    if request.query_params.get("user_id") not in (None, user_id):
        raise HTTPException(403, "Access denied")
    if request.query_params.get("chat_id"):
        await ensure_chat_access(redis, user_id, request.query_params["chat_id"])
    channel = get_key(ROOM_USER_PREFIX, user_id) # Artık genel helper kullanıyoruz
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    closed = False

    async def close_pubsub():
        # Also run as the response's background task: a client gone before the stream started never runs the
        # generator's finally, and its Redis connection stayed subscribed until garbage collection.
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass  # Redis gone: closing below releases the connection.
        await pubsub.aclose()

    async def event_generator():
        try:
            settings = await get_runtime_settings(redis, user_id)
            while not await request.is_disconnected():
                try:
                    # Redis must wait for data. An outer wait_for does not make
                    # get_message's default timeout=0 block: it floods idle
                    # clients with heartbeats and delays real events behind them.
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=settings.sse_heartbeat_seconds,
                    )
                    
                    if message and message.get("type") == "message":
                        yield f"event: message\ndata: {message['data']}\n\n"
                    else:
                        # Mesaj yoksa heartbeat gönder
                        yield ": heartbeat\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat timeout'u - normal durum
                    yield ": heartbeat\n\n"
        finally:
            await close_pubsub()

    return StreamingResponse(
        event_generator(),
        background=BackgroundTask(close_pubsub),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.websocket("/api/v1/audio/transcriptions/stream")
async def stt_proxy(
    websocket: WebSocket,
    language: str = "tr",
    model: str = "local-stt",
    provider: str = "local",
    prompt: str | None = None,
):
    token = websocket.query_params.get("token") or websocket.headers.get('authorization', '').removeprefix('Bearer ')
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        user_id = await authenticate_token(token, websocket)
    except HTTPException as error:
        # 1013 (try again later): the session could not be checked, it has not ended.
        await websocket.close(code=1013 if error.status_code == 503 else status.WS_1008_POLICY_VIOLATION)
        return
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    from orion.kernel import activity
    from orion.api.access_boundary import is_desktop_peer
    redis = websocket.app.state.redis
    if await activity.draining(redis):
        await websocket.close(code=1013)
        return
    await websocket.accept()
    settings = await get_runtime_settings(redis, user_id)
    router_api_key = settings.router_api_key

    router_url = get_router_base_urls()[0]
    ws_base = router_url.replace("http://", "ws://").replace("https://", "wss://")
    
    params = urllib.parse.urlencode({
        "language": language,
        "model": model,
        "provider": provider,
        **({"prompt": prompt.strip()} if prompt and prompt.strip() else {}),
    })
    target_ws_url = f"{ws_base}/v1/audio/transcriptions/stream?{params}"

    # Live dictation is active work for an explicit "update now" until the socket closes.
    from contextlib import AsyncExitStack
    work = AsyncExitStack()
    await work.enter_async_context(activity.lease(
        redis, 'voice', remote=getattr(websocket.app.state, 'mobile', False) or not is_desktop_peer(websocket.scope)))
    try:
        from orion.kernel.pinned_router import PinnedWebSocket
        async with PinnedWebSocket(target_ws_url, headers={'Authorization': 'Bearer ' + router_api_key}) as router_ws:
            async def client_to_router():
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        if message.get("bytes") is not None:
                            await router_ws.send(message["bytes"])
                        elif message.get("text") is not None:
                            # Finish is ordered after the last PCM packet. Keep
                            # the reverse stream alive until STT acknowledges.
                            await router_ws.send(message["text"])
                except WebSocketDisconnect:
                    pass
                except (websockets.ConnectionClosed, aiohttp.ClientConnectionError):
                    pass

            async def router_to_client():
                try:
                    while True:
                        message = await router_ws.recv()
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except WebSocketDisconnect:
                    pass
                except (websockets.ConnectionClosed, aiohttp.ClientConnectionError):
                    pass

            import asyncio
            tasks = {asyncio.create_task(client_to_router()), asyncio.create_task(router_to_client())}
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close()
    except Exception as e:
        try:
            await websocket.send_text('{"type": "error", "message": "Router bağlantısı kurulamadı."}')
            await websocket.close(code=1011)
        except:
            pass
    finally:
        await work.aclose()



def _mask_settings_for_client(settings: RuntimeSettings) -> RuntimeSettings:
    # Default-on tools a saved selection predates show as on, as the worker treats them.
    update = {"tool_selection": get_registry().complete(settings.tool_selection)}
    if settings.router_api_key:
        update["router_api_key"] = mask_api_key(settings.router_api_key)
    return settings.model_copy(update=update)


@router.post("/api/v1/admin/settings", response_model=RuntimeSettings)
async def update_settings(payload: SettingsUpdateRequest, request: Request, current_user: str = Depends(get_current_user)) -> RuntimeSettings:
    if payload.user_id != current_user and current_user != "global":
        raise HTTPException(status_code=403, detail="Forbidden")

    await _validate_settings_update(payload.values, payload.user_id)

    # Global ayarları değiştirmek için Admin API Key zorunlu
    if payload.user_id == SETTINGS_DEFAULT_USER:
        _check_admin_key(request)

    redis: Redis = request.app.state.redis
    settings = await _save_settings_update(redis, payload)
    return _mask_settings_for_client(settings)

@router.get("/api/v1/admin/settings", response_model=RuntimeSettings)
async def get_settings(request: Request, current_user: str = Depends(get_current_user)) -> RuntimeSettings:
            
    redis: Redis = request.app.state.redis
    settings = await get_runtime_settings(redis, current_user)
    device_language = request.headers.get('X-Orion-STT-Language')
    if settings.stt_language is None and device_language:
        from orion.api.services.stt_catalog import fetch_stt_catalog, initial_stt_language
        from orion.kernel.config import initialize_stt_language
        try:
            catalog = await fetch_stt_catalog(settings.router_api_key)
            settings = await initialize_stt_language(
                redis, current_user, initial_stt_language(device_language, catalog))
        except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            # Missing capabilities/storage must not persist a guessed default.
            # Other settings remain available; initialization retries next request.
            pass
    return _mask_settings_for_client(settings)

def _check_admin_key(request: Request):
    require_admin(request)

@router.get("/api/v1/admin/users/settings")
async def get_all_settings(request: Request) -> dict[str, dict[str, str]]:
    _check_admin_key(request)
    all_users = await get_all_users_settings()
    for user_dict in all_users.values():
        if "router_api_key" in user_dict:
            user_dict["router_api_key"] = mask_api_key(user_dict["router_api_key"])
    return all_users


@router.post("/api/v1/admin/users/settings", response_model=RuntimeSettings)
async def admin_update_settings(payload: SettingsUpdateRequest, request: Request) -> RuntimeSettings:
    _check_admin_key(request)
    await _validate_settings_update(payload.values, payload.user_id)
    settings = await _save_settings_update(request.app.state.redis, payload)
    return _mask_settings_for_client(settings)

@router.delete("/api/v1/admin/users/{user_id}/settings/{key}")
async def delete_user_setting_endpoint(user_id: str, key: str, request: Request):
    _check_admin_key(request)
    redis: Redis = request.app.state.redis
    try:
        await delete_runtime_setting(redis, user_id, key)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"status": "ok"}

@router.get("/api/v1/admin/settings/schema")
async def get_settings_schema(current_user: str = Depends(get_current_user)) -> list[str]:
    return sorted(_allowed_keys)


@router.get("/api/v1/admin/settings/defaults")
async def get_global_factory_defaults(request: Request) -> dict[str, str]:
    _check_admin_key(request)
    return {key: serialize_setting(value) for key, value in factory_settings.model_dump().items()}


@router.get("/api/v1/admin/settings/constraints")
async def get_settings_constraints(request: Request) -> dict[str, dict]:
    _check_admin_key(request)
    properties = RuntimeSettings.model_json_schema()["properties"]
    constraints = {}
    for key, field in properties.items():
        variants = field.get("anyOf", [field])
        value_field = next((item for item in variants if item.get("type") != "null"), field)
        constraints[key] = {name: value_field[name] for name in ("type", "minimum", "maximum") if name in value_field}
        if any(item.get("type") == "null" for item in variants):
            constraints[key]["nullable"] = True
    return constraints


@router.get("/api/v1/admin/models")
async def admin_models_endpoint(request: Request) -> dict:
    _check_admin_key(request)
    from orion.kernel.router_models import get_router_catalog
    try:
        return await get_router_catalog(force_refresh=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/v1/admin/settings/global/{key}/reset", response_model=RuntimeSettings)
async def reset_global_setting(key: str, request: Request) -> RuntimeSettings:
    _check_admin_key(request)
    normalized_key = key.lower()
    defaults = factory_settings.model_dump()
    if normalized_key not in defaults:
        raise HTTPException(status_code=400, detail=f"Geçersiz ayar anahtarı: {key}")
    redis: Redis = request.app.state.redis
    settings = await update_runtime_settings(
        redis, {normalized_key: serialize_setting(defaults[normalized_key])}, SETTINGS_DEFAULT_USER
    )
    return _mask_settings_for_client(settings)


@router.get("/api/v1/models")
async def chat_models_endpoint(current_user: str = Depends(get_current_user), include_voices: bool = True):
    from orion.kernel.router_models import get_router_catalog, get_account_catalog
    try:
        if current_user != 'global':
            return await get_account_catalog(current_user, include_voices)
        return await get_router_catalog(force_refresh=True, include_voices=include_voices)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
