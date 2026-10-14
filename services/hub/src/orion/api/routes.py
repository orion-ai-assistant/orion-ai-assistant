import asyncio
import aiohttp
from contextlib import suppress
from typing import Any
from services.shared.environment import get_env
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
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
from orion.api.services.job_service import create_job, get_job, stop_job, utc_now, get_key, get_user_chats, get_chat_history, get_turn_audio, rename_chat, delete_chat, get_all_chats_admin, get_chat_history_admin
from orion.api.auth_routes import get_current_user, require_active_user
from orion.api.security import JWT_SECRET_KEY, ALGORITHM, valid_admin_key, require_admin
import jwt
from services.shared.environment import get_router_base_urls

router = APIRouter()


@router.get("/api/v1/audio/languages")
async def stt_language_catalog(request: Request, current_user: str = Depends(get_current_user)):
    from orion.api.services.stt_catalog import fetch_stt_catalog
    settings = await get_runtime_settings(request.app.state.redis, current_user)
    try:
        return await fetch_stt_catalog(settings.router_api_key or "orion")
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
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc



from orion.kernel.registry import check_db_health

@router.get("/api/v1/assistant/capabilities")
async def assistant_capabilities(current_user: str = Depends(get_current_user)):
    return {"speech_modes": ["whole", "adaptive"], "speech_protocol": 1}


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
async def get_chat_history_endpoint(chat_id: str, request: Request, include_audio: bool = True, current_user: str = Depends(get_current_user)) -> list[dict]:
    return await get_chat_history(request.app.state.redis, current_user, chat_id, include_audio=include_audio)

@router.get("/api/v1/chats/{chat_id}/turns/{turn_id}/audio", response_model=dict)
async def get_turn_audio_endpoint(chat_id: str, turn_id: str, request: Request, current_user: str = Depends(get_current_user)) -> dict:
    return await get_turn_audio(request.app.state.redis, current_user, chat_id, turn_id)


@router.api_route('/api/v1/chats/{chat_id}/media/{media_id}', methods=['GET', 'HEAD'])
async def chat_media_endpoint(chat_id: str, media_id: str, request: Request,
                              current_user: str = Depends(get_current_user)):
    import re
    from orion.kernel.media_store import read_media
    await ensure_chat_access(request.app.state.redis, current_user, chat_id)
    if not re.fullmatch(r'[0-9a-f]{64}', media_id):
        raise HTTPException(404, 'Media not found')
    try:
        mime, raw = await read_media(chat_id, media_id)
    except LookupError as exc:
        raise HTTPException(404, 'Media not found') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Media unavailable') from exc
    total = len(raw)
    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "sandbox; default-src 'none'", 'Accept-Ranges': 'bytes',
               'Content-Length': str(total)}
    status_code = 200
    if request.method == 'GET':
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', request.headers.get('range', ''))
        if match and any(match.groups()):
            start, end = match.groups()
            if start:
                start = int(start)
                end = min(int(end), total - 1) if end else total - 1
            else:
                length = int(end)
                start, end = max(0, total - length), total - 1
            if start > end or start >= total:
                return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{total}', 'Content-Length': '0'})
            raw = raw[start:end + 1]
            headers.update({'Content-Range': f'bytes {start}-{end}/{total}', 'Content-Length': str(len(raw))})
            status_code = 206
    return Response(content=raw if request.method != 'HEAD' else b'', media_type=mime,
                    headers=headers, status_code=status_code)

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
    if request.query_params.get("user_id") not in (None, user_id):
        raise HTTPException(403, "Access denied")
    if request.query_params.get("chat_id"):
        await ensure_chat_access(redis, user_id, request.query_params["chat_id"])
    channel = get_key(ROOM_USER_PREFIX, user_id) # Artık genel helper kullanıyoruz
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    async def event_generator():
        settings = await get_runtime_settings(redis, user_id)
        try:
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
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        if getattr(websocket.app.state, 'mobile', False) and payload.get('device_id') != getattr(websocket.state, 'device_id', None):
            raise ValueError('Account token is not bound to this device')
        user_id = payload.get("sub")
        await require_active_user(user_id)
        if not user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    
    redis = websocket.app.state.redis
    settings = await get_runtime_settings(redis, user_id)
    router_api_key = settings.router_api_key or "orion"

    router_url = get_router_base_urls()[0]
    ws_base = router_url.replace("http://", "ws://").replace("https://", "wss://")
    
    params = urllib.parse.urlencode({
        "language": language,
        "model": model,
        "provider": provider,
        **({"prompt": prompt.strip()} if prompt and prompt.strip() else {}),
    })
    target_ws_url = f"{ws_base}/v1/audio/transcriptions/stream?{params}"

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



def _mask_settings_for_client(settings: RuntimeSettings) -> RuntimeSettings:
    if settings.router_api_key:
        return settings.model_copy(update={"router_api_key": mask_api_key(settings.router_api_key)})
    return settings


@router.post("/api/v1/admin/settings", response_model=RuntimeSettings)
async def update_settings(payload: SettingsUpdateRequest, request: Request, current_user: str = Depends(get_current_user)) -> RuntimeSettings:
    if payload.user_id != current_user and current_user != "global":
        raise HTTPException(status_code=403, detail="Forbidden")

    await _validate_settings_update(payload.values)

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
            catalog = await fetch_stt_catalog(settings.router_api_key or "orion")
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
    await _validate_settings_update(payload.values)
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
