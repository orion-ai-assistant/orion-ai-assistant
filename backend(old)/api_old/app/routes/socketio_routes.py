from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Awaitable
from urllib import parse

from log import Logger
import socketio

from api.app.socketio_server import get_socketio_server
from api.app.routes.socketio_agent_tasks import cancel_agent_task_for_session, start_agent_background_task
from config.config_manager import get_config_manager
from config.settings.api_server import WS_ENABLE_REAL_AGENT

logger = Logger(__file__)

_background_tasks: set[asyncio.Task[Any]] = set()
_incoming_worker_tasks: list[asyncio.Task[Any]] = []
_persist_ack_worker_tasks: list[asyncio.Task[Any]] = []

INCOMING_WORKER_COUNT = max(1, int(os.getenv("SOCKETIO_INCOMING_WORKERS", "50")))
INCOMING_QUEUE_SIZE = max(100, int(os.getenv("SOCKETIO_INCOMING_QUEUE_SIZE", "5000")))
_incoming_queues: list[asyncio.Queue[tuple[str, str | dict]]] = [
    asyncio.Queue(maxsize=INCOMING_QUEUE_SIZE) for _ in range(INCOMING_WORKER_COUNT)
]

PERSIST_ACK_WORKER_COUNT = max(1, int(os.getenv("SOCKETIO_PERSIST_ACK_WORKERS", "50")))
PERSIST_ACK_QUEUE_SIZE = max(100, int(os.getenv("SOCKETIO_PERSIST_ACK_QUEUE_SIZE", "5000")))
_persist_ack_queues: list[asyncio.Queue[tuple[str, str, str, str]]] = [
    asyncio.Queue(maxsize=PERSIST_ACK_QUEUE_SIZE) for _ in range(PERSIST_ACK_WORKER_COUNT)
]

from api.app.shared_state import manager, session_chat_map, session_verified_chat_map, increment_message_count
from api.app.store.redis_store import add_message, chat_exists, create_chat, user_can_access_chat, chat_has_activity


from api.app.core.stream_constants import MessageStatus, SocketEventName, PayloadEventName
from api.app.core.message_protocol import IncomingEventType, ParseErrorCode
from api.app.services.message_extractor import extract_chat_id, parse_incoming
from api.app.services.message_factory import MessageFactory
from api.app.services.messenger_service import Messenger


def _build_new_chat_id(user_id: str) -> str:
    """Generate a server-owned opaque chat id."""
    return f"chat-{uuid.uuid4().hex}"


async def _send_system_message(
    sid: str,
    chat_id: str,
    user_id: str,
    content: str,
    *,
    status: MessageStatus = MessageStatus.OK,
    emit_event: SocketEventName | str,
    broadcast: bool = False,
    skip_sid: str | None = None,
    **extra_fields,
) -> None:
    payload_event = extra_fields.pop("event", None)
    base_message = MessageFactory.system_message(content=content, payload_event=payload_event)
    message_data = base_message.model_copy(update={"status": status, **extra_fields})

    if broadcast:
        await Messenger.broadcast_message(
            chat_id=chat_id,
            socket_event=emit_event,
            message=message_data,
            session_id=sid,
            user_id=user_id,
            skip_sid=skip_sid,
        )
        return

    await Messenger.send_direct_message(
        sid=sid,
        socket_event=emit_event,
        message=message_data,
        session_id=sid,
        chat_id=chat_id,
        user_id=user_id,
    )


async def _send_error_to_sid(
    sid: str,
    chat_id: str,
    user_id: str,
    content: str,
) -> None:
    await Messenger.send_direct_message(
        sid=sid,
        socket_event=SocketEventName.CHAT_ERROR,
        message=MessageFactory.agent_error(content),
        chat_id=chat_id,
        user_id=user_id,
        session_id=sid,
    )


async def _handle_format_error(sid: str, chat_id: str, user_id: str, parse_error: str) -> None:
    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "Invalid message format. Expected: {'payload': {'chatId': '...', 'message': {'text': '...'}}}",
        status=MessageStatus.ERROR,
        emit_event=SocketEventName.CHAT_ERROR,
        event=PayloadEventName.INVALID_FORMAT.value,
        reason=parse_error,
    )


async def _handle_empty_message(sid: str, chat_id: str, user_id: str) -> None:
    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "Empty message ignored",
        emit_event=SocketEventName.CHAT_ERROR,
        event=PayloadEventName.EMPTY_MESSAGE.value,
    )


async def _handle_ping(sid: str, chat_id: str, user_id: str) -> None:
    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "pong",
        emit_event=SocketEventName.CHAT_MESSAGE_RECEIVED,
    )


async def _handle_cancel(sid: str, chat_id: str, user_id: str) -> None:
    from api.app.services.agent_queue_service import bump_cancel_version

    await bump_cancel_version(chat_id)
    cancel_agent_task_for_session(sid, chat_id)
    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "Agent akışı iptal edildi",
        emit_event=SocketEventName.CHAT_AGENT_CANCELED,
        event=PayloadEventName.MESSAGE_RECEIVED.value,
    )


async def _ack_user_message(
    sid: str,
    chat_id: str,
    user_id: str,
    user_msg: str,
) -> tuple[str, dict]:
    client_message_id = str(uuid.uuid4())
    user_entry = await add_message(
        chat_id=chat_id,
        sender="user",
        content=str(user_msg),
        metadata={
            "session_id": sid,
            "user_id": user_id,
            "client_message_id": client_message_id,
        },
    )
    logger.debug(lambda: f"[{sid}] Message received (user={user_id}, chat={chat_id}, id={user_entry['id']}): {user_msg}"
    )
    increment_message_count(user_id)

    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "OK: mesajınız alındı",
        emit_event=SocketEventName.CHAT_MESSAGE_RECEIVED,
        broadcast=True,
        event=PayloadEventName.MESSAGE_RECEIVED.value,
        message_id=user_entry["id"],
    )
    return client_message_id, user_entry


async def _echo_to_other_devices(
    sid: str,
    chat_id: str,
    user_id: str,
    user_msg: str,
    message_id: str,
    client_message_id: str,
) -> None:
    echo_message = MessageFactory.user_echo(str(user_msg), message_id=message_id, client_message_id=client_message_id)
    await Messenger.broadcast_message(
        chat_id=chat_id,
        socket_event=SocketEventName.CHAT_MESSAGE_RECEIVED,
        message=echo_message,
        session_id=sid,
        user_id=user_id,
        skip_sid=sid,
    )


def _fire_and_forget(coro: Awaitable[Any], *, name: str) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done_callback(done_task: asyncio.Task[Any]) -> None:
        _background_tasks.discard(done_task)
        try:
            done_task.result()
        except Exception as exc:
            logger.error(lambda: f"Background task failed ({name}): {exc}")

    task.add_done_callback(_done_callback)


async def _persist_and_ack_user_message(
    sid: str,
    chat_id: str,
    user_id: str,
    user_msg: str,
) -> None:
    client_message_id, user_entry = await _ack_user_message(
        sid,
        chat_id,
        user_id,
        user_msg,
    )
    await _echo_to_other_devices(
        sid,
        chat_id,
        user_id,
        user_msg,
        user_entry["id"],
        client_message_id,
    )


async def _switch_chat_room(
    sid: str,
    user_id: str,
    previous_chat_id: str,
    requested_chat_id: str,
) -> tuple[str, str | None]:
    if requested_chat_id == previous_chat_id:
        return previous_chat_id, None

    if not await chat_exists(requested_chat_id):
        return previous_chat_id, f"Böyle bir sohbet yok: {requested_chat_id}"

    if not await user_can_access_chat(requested_chat_id, user_id):
        return previous_chat_id, f"Sohbet erişimi yok: {requested_chat_id}"

    sio = get_socketio_server()
    if sio is None:
        raise RuntimeError("Socket.IO server is not registered")

    await sio.enter_room(sid, room=f"chat_{requested_chat_id}")
    session_chat_map[sid] = requested_chat_id
    session_verified_chat_map[sid] = requested_chat_id
    return requested_chat_id, None


async def _create_new_chat_and_join(sid: str, user_id: str) -> str:
    new_chat_id = _build_new_chat_id(user_id)
    await create_chat(chat_id=new_chat_id, created_by=user_id)

    sio = get_socketio_server()
    if sio is None:
        raise RuntimeError("Socket.IO server is not registered")

    await sio.enter_room(sid, room=f"chat_{new_chat_id}")
    session_chat_map[sid] = new_chat_id
    session_verified_chat_map[sid] = new_chat_id
    return new_chat_id


async def connect(sid: str, environ: dict, auth: dict | None = None):
    query_string = environ.get("QUERY_STRING", "")
    query_params = parse.parse_qs(query_string)

    auth_data = auth if isinstance(auth, dict) else {}
    user_id = (
        auth_data.get("user_id")
        or auth_data.get("userId")
        or query_params.get("user_id", [None])[0]
        or query_params.get("userId", [None])[0]
    )
    token = auth_data.get("token") or query_params.get("token", [None])[0]

    if not user_id or not token:
        raise socketio.exceptions.ConnectionRefusedError("missing_auth")

    user_id = str(user_id)
    requested_chat_id = (
        query_params.get("chat_id", [None])[0]
        or query_params.get("chatId", [None])[0]
    )

    chat_id: str
    created_new_chat = False
    if requested_chat_id:
        chat_id = str(requested_chat_id)
        if not await chat_exists(chat_id):
            logger.warning(lambda: f"Connection rejected: sid={sid} user={user_id} unknown_chat={chat_id}")
            raise socketio.exceptions.ConnectionRefusedError("chat_not_found")
        if not await user_can_access_chat(chat_id, user_id):
            logger.warning(lambda: f"Connection rejected: sid={sid} user={user_id} forbidden_chat={chat_id}")
            raise socketio.exceptions.ConnectionRefusedError("chat_access_denied")
    else:
        chat_id = _build_new_chat_id(user_id)
        await create_chat(chat_id=chat_id, created_by=user_id)
        created_new_chat = True

    manager.connect(sid, user_id)
    session_chat_map[sid] = chat_id
    session_verified_chat_map[sid] = chat_id

    sio = get_socketio_server()
    if sio is None:
        raise RuntimeError("Socket.IO server is not registered")
    await sio.enter_room(sid, room=f"user_{user_id}")
    await sio.enter_room(sid, room=f"chat_{chat_id}")

    logger.info(lambda: f"Client connected: sid={sid} user={user_id} chat={chat_id} qs='{query_string}'"
    )
    await _send_system_message(
        sid,
        chat_id,
        user_id,
        "Connected",
        emit_event=SocketEventName.CONNECTED,
        event=PayloadEventName.CONNECTED.value,
        chat_created=created_new_chat,
    )
    return True


async def _resolve_chat_for_message(sid: str, user_id: str, requested_chat_id: str | None) -> str:
    previous_chat_id = session_chat_map.get(sid)
    verified_chat_id = session_verified_chat_map.get(sid)

    if requested_chat_id:
        if previous_chat_id and requested_chat_id == previous_chat_id and verified_chat_id == previous_chat_id:
            return previous_chat_id

        if not await chat_exists(requested_chat_id):
            return previous_chat_id or await _create_new_chat_and_join(sid, user_id)
        if await user_can_access_chat(requested_chat_id, user_id):
            if previous_chat_id and previous_chat_id != requested_chat_id:
                await _switch_chat_room(sid, user_id, previous_chat_id, requested_chat_id)
            return requested_chat_id
        return previous_chat_id or await _create_new_chat_and_join(sid, user_id)

    if not previous_chat_id:
        return await _create_new_chat_and_join(sid, user_id)

    if verified_chat_id == previous_chat_id:
        return previous_chat_id

    if await chat_has_activity(previous_chat_id):
        return await _create_new_chat_and_join(sid, user_id)

    return previous_chat_id


async def _process_incoming_message(sid: str, data: str | dict) -> None:
    user_id = manager.get_user_id(sid)
    if not user_id:
        return

    parsed = parse_incoming(data)
    if parsed.error == ParseErrorCode.INVALID_JSON:
        current_chat = session_chat_map.get(sid, "unknown-chat")
        await _send_error_to_sid(sid, current_chat, user_id, "Geçersiz JSON formatı")
        return

    if parsed.event_type == IncomingEventType.JOIN:
        await handle_chat_join(sid, parsed.payload)
        return

    if parsed.event_type == IncomingEventType.CANCEL:
        effective_chat_id = await _resolve_chat_for_message(sid, user_id, parsed.chat_id)
        if not effective_chat_id:
            effective_chat_id = session_chat_map.get(sid, "unknown-chat")

        await _handle_cancel(sid, effective_chat_id, user_id)
        return

    if parsed.event_type == IncomingEventType.MESSAGE:
        wrapped_payload = {"payload": parsed.payload} if isinstance(parsed.payload, dict) else parsed.payload
        await _process_incoming_message(sid, wrapped_payload)
        return

    if parsed.error in {ParseErrorCode.INVALID_PAYLOAD, ParseErrorCode.MISSING_PAYLOAD}:
        await _handle_format_error(sid, session_chat_map.get(sid, "unknown-chat"), user_id, parsed.error.value)
        return

    user_msg = parsed.message
    requested_chat_id = parsed.chat_id

    effective_chat_id = await _resolve_chat_for_message(sid, user_id, requested_chat_id)
    if not effective_chat_id:
        logger.warning(lambda: f"[message] sid={sid} user={user_id} no active chat after resolve")
        await _send_error_to_sid(sid, "unknown-chat", user_id, "Aktif sohbet bulunamadı.")
        return

    previous_chat_id = session_chat_map.get(sid)
    if previous_chat_id and previous_chat_id != effective_chat_id:
        await _switch_chat_room(sid, user_id, previous_chat_id, effective_chat_id)

    if not str(user_msg).strip():
        await _handle_empty_message(sid, effective_chat_id, user_id)
        return

    if str(user_msg).lower() == "ping":
        await _handle_ping(sid, effective_chat_id, user_id)
        return

    try:
        if not _enqueue_persist_ack(sid, effective_chat_id, user_id, str(user_msg)):
            await _send_error_to_sid(
                sid,
                effective_chat_id,
                user_id,
                "Sunucu şu anda yoğun. Lütfen kısa süre sonra tekrar deneyin.",
            )
            return

        config = get_config_manager().get_config()
        ws_real_agent_enabled = getattr(config, "ws_enable_real_agent", False)

        if ws_real_agent_enabled:
            # Queue real agent processing for high concurrency handling
            from api.app.services.agent_queue_service import enqueue_agent_task
            from api.app.services.session_service import get_messages_for_agent

            messages = await get_messages_for_agent(effective_chat_id, str(user_msg))

            request_id = await enqueue_agent_task(
                session_id=sid,
                user_id=user_id,
                chat_id=effective_chat_id,
                messages=messages,
            )
            logger.debug(lambda: f"Queued agent task sid={sid} chat={effective_chat_id} request_id={request_id}")
        else:
            sio = get_socketio_server()
            if sio is not None:
                await start_agent_background_task(
                    sio,
                    False,
                    sid,
                    user_id,
                    effective_chat_id,
                    user_msg,
                )
            else:
                logger.warning(lambda: "Socket.IO server not available for mock agent task")

    except Exception as exc:
        await _send_error_to_sid(sid, effective_chat_id, user_id, f"Mesaj işleme hatası: {exc}")


def _queue_for_sid(sid: str) -> asyncio.Queue[tuple[str, str | dict]]:
    return _incoming_queues[hash(sid) % INCOMING_WORKER_COUNT]


def _persist_ack_queue_for_sid(sid: str) -> asyncio.Queue[tuple[str, str, str, str]]:
    return _persist_ack_queues[hash(sid) % PERSIST_ACK_WORKER_COUNT]


def _enqueue_persist_ack(
    sid: str,
    chat_id: str,
    user_id: str,
    user_msg: str,
) -> bool:
    queue = _persist_ack_queue_for_sid(sid)
    try:
        queue.put_nowait((sid, chat_id, user_id, user_msg))
        return True
    except asyncio.QueueFull:
        logger.warning(lambda: f"Persist+ack queue overflow sid={sid} chat={chat_id}")
        return False


async def _incoming_message_worker(worker_idx: int) -> None:
    queue = _incoming_queues[worker_idx]
    while True:
        sid, data = await queue.get()
        try:
            await _process_incoming_message(sid, data)
        except Exception as exc:
            logger.error(lambda: f"incoming_message_worker[{worker_idx}] failed sid={sid}: {exc}")
        finally:
            queue.task_done()


async def _persist_ack_worker(worker_idx: int) -> None:
    queue = _persist_ack_queues[worker_idx]
    while True:
        sid, chat_id, user_id, user_msg = await queue.get()
        try:
            await _persist_and_ack_user_message(sid, chat_id, user_id, user_msg)
        except Exception as exc:
            logger.error(lambda: f"persist_ack_worker[{worker_idx}] failed sid={sid}: {exc}")
        finally:
            queue.task_done()


async def start_incoming_message_workers() -> None:
    incoming_running = _incoming_worker_tasks and any(not task.done() for task in _incoming_worker_tasks)
    persist_running = _persist_ack_worker_tasks and any(not task.done() for task in _persist_ack_worker_tasks)
    if incoming_running and persist_running:
        return

    if not incoming_running:
        _incoming_worker_tasks.clear()
        for worker_idx in range(INCOMING_WORKER_COUNT):
            task = asyncio.create_task(_incoming_message_worker(worker_idx), name=f"incoming_message_worker_{worker_idx}")
            _incoming_worker_tasks.append(task)

    if not persist_running:
        _persist_ack_worker_tasks.clear()
        for worker_idx in range(PERSIST_ACK_WORKER_COUNT):
            task = asyncio.create_task(_persist_ack_worker(worker_idx), name=f"persist_ack_worker_{worker_idx}")
            _persist_ack_worker_tasks.append(task)

    logger.info(
        lambda: (
            f"Started incoming workers={INCOMING_WORKER_COUNT} incoming_queue_size={INCOMING_QUEUE_SIZE} "
            f"persist_workers={PERSIST_ACK_WORKER_COUNT} persist_queue_size={PERSIST_ACK_QUEUE_SIZE}"
        )
    )


async def stop_incoming_message_workers() -> None:
    for task in _incoming_worker_tasks:
        task.cancel()
    if _incoming_worker_tasks:
        await asyncio.gather(*_incoming_worker_tasks, return_exceptions=True)
    _incoming_worker_tasks.clear()

    for task in _persist_ack_worker_tasks:
        task.cancel()
    if _persist_ack_worker_tasks:
        await asyncio.gather(*_persist_ack_worker_tasks, return_exceptions=True)
    _persist_ack_worker_tasks.clear()

    logger.info(lambda: "Stopped incoming and persist+ack workers")


async def handle_message(sid: str, data: str | dict):
    user_id = manager.get_user_id(sid)
    if not user_id:
        return

    queue = _queue_for_sid(sid)
    try:
        queue.put_nowait((sid, data))
    except asyncio.QueueFull:
        chat_id = session_chat_map.get(sid, "unknown-chat")
        logger.warning(lambda: f"Incoming queue overflow sid={sid} chat={chat_id}")
        _fire_and_forget(
            _send_error_to_sid(
                sid,
                chat_id,
                user_id,
                "Sunucu şu anda yoğun. Lütfen kısa süre sonra tekrar deneyin.",
            ),
            name="incoming_queue_overflow_error",
        )


async def handle_chat_message(sid: str, data: str | dict):
    logger.info(lambda: f"[chat:message] sid={sid} payload={data}")
    wrapped = {"payload": data} if isinstance(data, dict) else data
    await handle_message(sid, wrapped)


async def handle_chat_join(sid: str, data: str | dict | None = None):
    logger.info(lambda: f"[chat:join] sid={sid} payload={data}")
    user_id = manager.get_user_id(sid)
    if not user_id:
        logger.warning(lambda: f"[chat:join] unknown user for sid={sid}")
        return

    previous_chat_id = session_chat_map.get(sid)
    if not previous_chat_id:
        logger.warning(lambda: f"[chat:join] sid={sid} user={user_id} unknown active chat")
        await _send_error_to_sid(sid, "unknown-chat", user_id, "Aktif sohbet bulunamadı.")
        return

    if data is None:
        requested_chat_id, parse_error = None, ParseErrorCode.MISSING_CHAT_ID
    else:
        requested_chat_id, parse_error = extract_chat_id(data)

    if parse_error:
        # no chat_id means client asked for a new chat, but avoid creating a new one if current active chat is empty
        if previous_chat_id and not await chat_has_activity(previous_chat_id):
            await _send_system_message(
                sid,
                previous_chat_id,
                user_id,
                "Mevcut boş sohbet devam ediyor",
                emit_event=SocketEventName.CHAT_JOINED,
                event=PayloadEventName.CHAT_JOINED.value,
                room=f"chat_{previous_chat_id}",
            )
            return

        new_chat_id = await _create_new_chat_and_join(sid, user_id)
        logger.info(lambda: f"[chat:join] sid={sid} user={user_id} created new chat={new_chat_id}")
        await _send_system_message(
            sid,
            new_chat_id,
            user_id,
            "Yeni sohbet oluşturuldu ve katılım sağlandı",
            emit_event=SocketEventName.CHAT_CREATED,
            event=PayloadEventName.CHAT_CREATED.value,
            room=f"chat_{new_chat_id}",
        )
        return

    effective_chat_id, room_error = await _switch_chat_room(
        sid,
        user_id,
        previous_chat_id,
        requested_chat_id,
    )
    if room_error:
        logger.warning(lambda: f"[chat:join] sid={sid} user={user_id} error={room_error}")
        await _send_error_to_sid(sid, previous_chat_id, user_id, room_error)
        return

    await _send_system_message(
        sid,
        effective_chat_id,
        user_id,
        "Sohbete katılım başarılı",
        emit_event=SocketEventName.CHAT_JOINED,
        event=PayloadEventName.CHAT_JOINED.value,
        room=f"chat_{effective_chat_id}",
    )


async def handle_chat_cancel(sid: str, data: str | dict | None = None):
    logger.info(lambda: f"[chat:agent:cancel] sid={sid} payload={data}")
    payload = data if isinstance(data, dict) else {}
    await handle_message(sid, {"type": IncomingEventType.CANCEL.value, "payload": payload})


async def disconnect(sid: str):
    cancel_agent_task_for_session(sid)

    user_id = manager.disconnect(sid)
    chat_id_for_log = session_chat_map.pop(sid, "unknown-chat")
    session_verified_chat_map.pop(sid, None)

    if (
        user_id
        and not manager.has_active_user(user_id)
        and isinstance(chat_id_for_log, str)
        and chat_id_for_log != "unknown-chat"
    ):
        from api.app.services.session_service import clear_session
        await clear_session(chat_id_for_log)

    logger.info(lambda: f"Client disconnected: sid={sid} user={user_id or 'unknown-user'} chat={chat_id_for_log}")


def register_socket_events(sio_instance: socketio.AsyncServer) -> None:
    """Register socket.io event handlers explicitly (no module import side effects)."""
    sio_instance.on("connect")(connect)
    sio_instance.on("message")(handle_message)
    sio_instance.on(IncomingEventType.MESSAGE.value)(handle_chat_message)
    sio_instance.on(IncomingEventType.JOIN.value)(handle_chat_join)
    sio_instance.on(IncomingEventType.CANCEL.value)(handle_chat_cancel)
    sio_instance.on("disconnect")(disconnect)

