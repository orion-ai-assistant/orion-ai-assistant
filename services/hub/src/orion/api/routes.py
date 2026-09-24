import asyncio
from typing import Any
from services.shared.environment import get_env
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from orion.contracts.http import JobCreateRequest, JobCreateResponse, JobStatusResponse, JobStopResponse
from orion.contracts.constants import ROOM_USER_PREFIX # Prefix'i direkt buradan alıyoruz
from pydantic import BaseModel, Field, ValidationError

from orion.kernel.config import RuntimeSettings, settings as factory_settings, get_runtime_settings, update_runtime_settings, _allowed_keys, get_all_users_settings, delete_runtime_setting, is_protected_global_key
from orion.kernel.router_models import validate_model_updates, ModelNotFoundError
from orion.contracts.constants import SETTINGS_DEFAULT_USER
from orion.api.services.job_service import create_job, get_job, stop_job, utc_now, get_key, get_user_chats, get_chat_history, rename_chat, delete_chat, get_all_chats_admin, get_chat_history_admin
from orion.api.auth_routes import get_current_user

router = APIRouter()


class SettingsUpdateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    values: dict[str, str] = Field(default_factory=dict)


async def _validate_settings_update(values: dict[str, str]) -> None:
    for key in values:
        if key.lower() not in _allowed_keys:
            raise HTTPException(status_code=400, detail=f"Geçersiz ayar anahtarı: {key}")
    try:
        await validate_model_updates(values)
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



from orion.kernel.registry import check_db_health

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
    if payload.user_id != current_user:
        payload.user_id = current_user
    return await create_job(request.app.state.redis, payload)

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

@router.get("/api/v1/chats/{chat_id}", response_model=JobStatusResponse)
async def get_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)) -> JobStatusResponse:
    return await get_job(request.app.state.redis, current_user, chat_id)

@router.get("/api/v1/chats", response_model=list[dict])
async def list_chats_endpoint(request: Request, current_user: str = Depends(get_current_user)) -> list[dict]:
    return await get_user_chats(request.app.state.redis, current_user)

@router.get("/api/v1/chats/{chat_id}/history", response_model=list[dict])
async def get_chat_history_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)) -> list[dict]:
    return await get_chat_history(request.app.state.redis, current_user, chat_id)

class ChatRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

@router.patch("/api/v1/chats/{chat_id}/rename")
async def rename_chat_endpoint(chat_id: str, payload: ChatRenameRequest, request: Request, current_user: str = Depends(get_current_user)):
    return await rename_chat(request.app.state.redis, current_user, chat_id, payload.name)

@router.delete("/api/v1/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str, request: Request, current_user: str = Depends(get_current_user)):
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
    channel = get_key(ROOM_USER_PREFIX, user_id) # Artık genel helper kullanıyoruz
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    async def event_generator():
        settings = await get_runtime_settings(redis, user_id)
        try:
            while not await request.is_disconnected():
                try:
                    # Heartbeat ile mesajları ayır - timeout'u kaldırdık
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
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
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream", 
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/api/v1/admin/settings", response_model=RuntimeSettings)
async def update_settings(payload: SettingsUpdateRequest, request: Request, current_user: str = Depends(get_current_user)) -> RuntimeSettings:
    if payload.user_id != current_user and current_user != "global":
        raise HTTPException(status_code=403, detail="Forbidden")

    await _validate_settings_update(payload.values)

    # Global ayarları değiştirmek için Admin API Key zorunlu
    if payload.user_id == SETTINGS_DEFAULT_USER:
        _check_admin_key(request)

    redis: Redis = request.app.state.redis
    return await _save_settings_update(redis, payload)

@router.get("/api/v1/admin/settings", response_model=RuntimeSettings)
async def get_settings(request: Request, current_user: str = Depends(get_current_user)) -> RuntimeSettings:
            
    redis: Redis = request.app.state.redis
    settings = await get_runtime_settings(redis, current_user)
    return settings

def _check_admin_key(request: Request):
    admin_key = get_env("ADMIN_API_KEY")
    if admin_key:
        provided = request.headers.get("X-Admin-Key")
        if provided != admin_key:
            raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/api/v1/admin/users/settings")
async def get_all_settings(request: Request) -> dict[str, dict[str, str]]:
    _check_admin_key(request)
    return await get_all_users_settings()


@router.post("/api/v1/admin/users/settings", response_model=RuntimeSettings)
async def admin_update_settings(payload: SettingsUpdateRequest, request: Request) -> RuntimeSettings:
    _check_admin_key(request)
    await _validate_settings_update(payload.values)
    return await _save_settings_update(request.app.state.redis, payload)

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
async def get_settings_schema() -> list[str]:
    return sorted(_allowed_keys)


@router.get("/api/v1/admin/settings/defaults")
async def get_global_factory_defaults(request: Request) -> dict[str, str]:
    _check_admin_key(request)
    return {key: str(value) for key, value in factory_settings.model_dump().items()}


@router.get("/api/v1/admin/settings/constraints")
async def get_settings_constraints(request: Request) -> dict[str, dict]:
    _check_admin_key(request)
    properties = RuntimeSettings.model_json_schema()["properties"]
    return {
        key: {name: field[name] for name in ("type", "minimum", "maximum") if name in field}
        for key, field in properties.items()
    }


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
    return await update_runtime_settings(
        redis, {normalized_key: str(defaults[normalized_key])}, SETTINGS_DEFAULT_USER
    )


@router.get("/api/v1/models")
async def chat_models_endpoint(current_user: str = Depends(get_current_user)):
    from orion.kernel.router_models import get_router_catalog
    try:
        return await get_router_catalog(force_refresh=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
