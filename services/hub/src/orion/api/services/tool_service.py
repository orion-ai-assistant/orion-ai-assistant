import json

from fastapi import HTTPException

from orion.contracts.tools import ToolSelection
from orion.kernel.config import get_runtime_settings
from orion.kernel import registry as db
from orion.worker.tools._registry import get_registry


def validate_selection(selection):
    try:
        return get_registry().validate_selection(selection)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def read_selection(redis, user_id, chat_id=None):
    override = None
    if chat_id:
        key = f"orion:tool-selection:{chat_id}"
        raw = await redis.get(key)
        if raw is None:
            raw = json.dumps(await db.get_chat_tool_selection(chat_id, user_id))
            await redis.set(key, raw, ex=3600)
        value = json.loads(raw)
        if value is not None:
            override = ToolSelection.model_validate(value)
    settings = await get_runtime_settings(redis, user_id)
    effective = override if override is not None else settings.tool_selection
    return {"selection": effective.model_dump(), "inherited": override is None,
            "enabled_tools": get_registry().enabled(effective)}


async def save_selection(redis, user_id, chat_id, selection):
    if selection is not None:
        validate_selection(selection)
    try:
        await db.set_chat_tool_selection(chat_id, user_id, selection.model_dump() if selection is not None else None)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    await redis.delete(f"orion:tool-selection:{chat_id}")
    return await read_selection(redis, user_id, chat_id)
