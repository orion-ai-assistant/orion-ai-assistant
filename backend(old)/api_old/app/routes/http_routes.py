from pathlib import Path

from fastapi import APIRouter, File, Form, Query, HTTPException, UploadFile

import filetype as _filetype_mod

from log import Logger

from api.app.store.redis_store import add_file, add_message, get_all_messages, get_files, get_messages
from api.app.shared_state import session_chat_map


router = APIRouter()
logger = Logger(__file__)


def _ok(message: str, **extra) -> dict:
    return {"status": "ok", "message": message, **extra}

# ── Upload validation ────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

ALLOWED_EXTENSIONS: set[str] = {"txt", "pdf", "png", "jpg", "jpeg", "gif", "webp", "csv", "json", "md"}

# MIME types allowed for binary files (detected via magic bytes).
# Text-based formats (txt, csv, json, md) have no magic bytes; extension check is enough.
ALLOWED_BINARY_MIMES: set[str] = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}


def _validate_upload(filename: str | None, file_head: bytes, size_bytes: int) -> str:
    """Validate via magic bytes (filetype library). Returns detected MIME type."""
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Dosya çok büyük: {size_bytes} bayt. Maksimum: {MAX_FILE_SIZE_BYTES} bayt (5 MB).",
        )

    ext = Path(filename or "").suffix.lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Desteklenmeyen dosya uzantısı: '.{ext}'. "
                f"İzin verilenler: {sorted(ALLOWED_EXTENSIONS)}"
            ),
        )

    kind = _filetype_mod.guess(file_head)  # type: ignore[union-attr]
    if kind is not None:
        # Binary file — check real MIME against allow-list
        if kind.mime not in ALLOWED_BINARY_MIMES:
            raise HTTPException(
                status_code=415,
                detail=f"Gerçek dosya tipi izin verilmiyor: '{kind.mime}'.",
            )
        return kind.mime
    # kind is None → likely plain text; extension already verified above

    # Fallback: derive a sensible MIME from extension
    _EXT_MIME = {
        "txt": "text/plain",
        "csv": "text/csv",
        "md": "text/markdown",
        "json": "application/json",
    }
    return _EXT_MIME.get(ext, "application/octet-stream")


@router.get("/")
async def root():
    return {
        "message": "Orion AI Socket.IO Service is running",
        "socketio": "/socket.io",
        "upload_json": "/upload-json",
        "upload_file": "/upload-file",
        "mock_messages": "/mock/chats/{chat_id}/messages",
        "mock_files": "/mock/chats/{chat_id}/files",
    }


@router.get("/api/health")
async def api_health():
    return _ok("OK")


@router.post("/upload-json")
async def upload_json(payload: dict):
    return {"status": "received", "type": "json", "keys": list(payload.keys()), "data": payload}


@router.post("/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    description: str = Form(default=""),
    # Accept both snake_case (chat_id) and camelCase (chatId) from clients
    chat_id: str = Form(default=""),
    chatId: str = Form(default=""),
):
    effective_chat_id = chat_id or chatId or "default-chat"

    content = await file.read()
    size_bytes = len(content)

    detected_mime = _validate_upload(file.filename, content[:2048], size_bytes)

    file_item = await add_file(
        chat_id=effective_chat_id,
        filename=file.filename,
        content_type=detected_mime,
        size_bytes=size_bytes,
        description=description,
    )

    # Link the uploaded file to the chat context as a message entry
    await add_message(
        chat_id=effective_chat_id,
        sender="system",
        content=f"[Dosya yüklendi] {file_item['filename']} ({size_bytes} bayt)",
        message_type="file",
        metadata={
            "file_id": file_item["id"],
            "filename": file_item["filename"],
            "content_type": detected_mime,
            "size_bytes": size_bytes,
            "description": description,
        },
    )

    logger.info(lambda: f"File received chat={effective_chat_id} file_id={file_item['id']} "
        f"name={file_item['filename']} size={size_bytes} type={detected_mime}"
    )

    return {
        "success": True,
        "status": "ok",
        "message": "OK: file received",
        "fileId": file_item["id"],
        "url": f"/mock/chats/{effective_chat_id}/files",
        "chat_id": effective_chat_id,
        "file": file_item,
    }


@router.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    description: str = Form(default=""),
    chat_id: str = Form(default=""),
    chatId: str = Form(default=""),
):
    return await upload_file(file=file, description=description, chat_id=chat_id, chatId=chatId)


@router.post("/mock/chats/{chat_id}/messages")
async def create_mock_message(chat_id: str, payload: dict):
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    sender = str(payload.get("sender", "user"))
    message_type = str(payload.get("message_type", "text"))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    message = await add_message(
        chat_id=chat_id,
        sender=sender,
        content=content,
        message_type=message_type,
        metadata=metadata,
    )
    logger.info(lambda: f"Mock message added chat={chat_id} id={message['id']} sender={sender}")

    return _ok("OK: message saved", data=message)


@router.get("/mock/chats/{chat_id}/messages")
async def list_mock_messages(
    chat_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: str | None = Query(default=None),
):
    items = await get_messages(chat_id=chat_id, limit=limit, before_id=before_id)
    logger.info(lambda: f"Messages fetched chat={chat_id} count={len(items)} limit={limit} before_id={before_id}"
    )
    return _ok("OK: messages fetched", chat_id=chat_id, count=len(items), messages=items)


@router.get("/api/chats/{chat_id}/messages")
async def api_list_messages(
    chat_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: str | None = Query(default=None),
):
    return await list_mock_messages(chat_id=chat_id, limit=limit, before_id=before_id)


@router.get("/mock/chats/{chat_id}/messages/all")
async def list_all_mock_messages(chat_id: str):
    items = await get_all_messages(chat_id=chat_id)
    logger.info(lambda: f"All messages fetched chat={chat_id} count={len(items)}")
    return _ok("OK: all messages fetched", chat_id=chat_id, count=len(items), messages=items)


@router.post("/api/chats/{chat_id}/ask")
async def api_ask(chat_id: str, payload: dict):
    question = str(payload.get("question", payload.get("message", ""))).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    user_message = await add_message(
        chat_id=chat_id,
        sender="user",
        content=question,
        message_type="text",
        metadata={"source": "http-api"},
    )
    mock_answer_text = f"OK: mesajın alındı -> {question}"
    assistant_message = await add_message(
        chat_id=chat_id,
        sender="assistant",
        content=mock_answer_text,
        message_type="text",
        metadata={"source": "mock-ask"},
    )
    logger.info(lambda: f"Ask handled chat={chat_id} user_id={user_message['id']} assistant_id={assistant_message['id']}"
    )
    return _ok(
        "OK: question handled",
        chat_id=chat_id,
        question=question,
        answer=assistant_message["content"],
        user_message=user_message,
        assistant_message=assistant_message,
    )


@router.get("/mock/chats/{chat_id}/files")
async def list_mock_files(chat_id: str):
    items = await get_files(chat_id=chat_id)
    logger.info(lambda: f"Files fetched chat={chat_id} count={len(items)}")
    return _ok("OK: files fetched", chat_id=chat_id, count=len(items), files=items)


@router.get("/mock/sessions/{session_id}/messages")
async def list_messages_by_session(session_id: str):
    chat_id = session_chat_map.get(session_id)
    if not chat_id:
        raise HTTPException(status_code=404, detail="session not found")

    items = await get_all_messages(chat_id=chat_id)
    logger.info(lambda: f"Session messages fetched session={session_id} chat={chat_id} count={len(items)}")
    return _ok(
        "OK: session messages fetched",
        session_id=session_id,
        chat_id=chat_id,
        count=len(items),
        messages=items,
    )
