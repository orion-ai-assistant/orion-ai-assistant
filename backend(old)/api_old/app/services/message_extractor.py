from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from api.app.core.message_protocol import IncomingEventType, ParseErrorCode


@dataclass(frozen=True)
class ParsedIncoming:
    event_type: IncomingEventType | None
    payload: dict[str, Any]
    message: str
    chat_id: str | None
    error: ParseErrorCode | None


def _to_dict(raw_data: str | dict) -> tuple[dict[str, Any], ParseErrorCode | None]:
    if isinstance(raw_data, dict):
        return raw_data, None

    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError:
        return {}, ParseErrorCode.INVALID_JSON

    if not isinstance(parsed, dict):
        return {}, ParseErrorCode.INVALID_PAYLOAD

    return parsed, None


def _payload_from_data(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload
    return data


def _extract_message_and_chat(payload: dict[str, Any]) -> tuple[str, str | None, ParseErrorCode | None]:
    message_obj = payload.get("message")
    chat_raw = payload.get("chatId") or payload.get("chat_id")

    message_text = ""
    if isinstance(message_obj, dict):
        message_text = str(message_obj.get("text") or message_obj.get("content") or "")
    elif isinstance(message_obj, str):
        message_text = message_obj

    chat_id = str(chat_raw) if chat_raw is not None else None
    if not message_text.strip():
        return "", chat_id, ParseErrorCode.EMPTY_MESSAGE

    return message_text, chat_id, None


def parse_incoming(raw_data: str | dict) -> ParsedIncoming:
    data, parse_error = _to_dict(raw_data)
    if parse_error is not None:
        return ParsedIncoming(None, {}, "", None, parse_error)

    payload = _payload_from_data(data)
    if not isinstance(payload, dict):
        return ParsedIncoming(None, {}, "", None, ParseErrorCode.MISSING_PAYLOAD)

    raw_type = data.get("type")
    event_type: IncomingEventType | None = None
    if isinstance(raw_type, str):
        try:
            event_type = IncomingEventType(raw_type)
        except ValueError:
            event_type = None

    # chat:agent:cancel event has no message payload; allow empty message
    if event_type == IncomingEventType.CANCEL:
        chat_raw = payload.get("chatId") or payload.get("chat_id")
        chat_id = str(chat_raw) if chat_raw is not None else None
        return ParsedIncoming(event_type, payload, "", chat_id, None)

    message, chat_id, msg_error = _extract_message_and_chat(payload)
    return ParsedIncoming(event_type, payload, message, chat_id, msg_error)


def extract_chat_id(raw_data: str | dict) -> tuple[str | None, ParseErrorCode | None]:
    parsed = parse_incoming(raw_data)
    if parsed.error in {ParseErrorCode.INVALID_JSON, ParseErrorCode.INVALID_PAYLOAD, ParseErrorCode.MISSING_PAYLOAD}:
        return None, parsed.error

    if not parsed.chat_id:
        return None, ParseErrorCode.MISSING_CHAT_ID

    return parsed.chat_id, None
