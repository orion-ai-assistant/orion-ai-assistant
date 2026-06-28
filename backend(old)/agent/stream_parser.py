from __future__ import annotations

import json
from typing import Any


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content) if content is not None else ""


def serialize_message(msg: Any) -> dict[str, str]:
    role_map = {
        "human": "user",
        "ai": "assistant",
        "assistant": "assistant",
        "system": "system",
        "tool": "tool",
    }

    if isinstance(msg, dict):
        role = str(msg.get("role") or "user").lower()
        content = _content_to_text(msg.get("content", ""))
        return {"role": role, "content": content}

    msg_type = str(getattr(msg, "type", "user")).lower()
    role = role_map.get(msg_type, "user")
    content = _content_to_text(getattr(msg, "content", ""))
    return {"role": role, "content": content}


def _extract_text_from_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content", "response", "output", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = _extract_text_from_payload(value)
                if nested:
                    return nested
            if isinstance(value, list):
                parts = [_extract_text_from_payload(v) for v in value]
                merged = "".join([p for p in parts if p])
                if merged:
                    return merged
        return ""
    if isinstance(payload, list):
        parts = [_extract_text_from_payload(item) for item in payload]
        return "".join([p for p in parts if p])
    return ""


def _normalize_chunk(text: str) -> str:
    return text.replace("\r\n", "\n") if text else ""


def _is_assistant_role(value: Any) -> bool:
    return isinstance(value, str) and value.lower() in {
        "assistant",
        "ai",
        "model",
        "aimessage",
        "aimessagechunk",
    }


def _extract_stream_signals(payload: Any) -> dict[str, list[Any]]:
    texts: list[str] = []
    thoughts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    message_obj = payload
    if isinstance(payload, (list, tuple)) and payload:
        message_obj = payload[0]

    if hasattr(message_obj, "content"):
        content = getattr(message_obj, "content")
        role = getattr(message_obj, "role", None) or getattr(message_obj, "type", None)
        additional_kwargs = getattr(message_obj, "additional_kwargs", None)
    elif isinstance(message_obj, dict):
        content = message_obj.get("content")
        role = message_obj.get("role") or message_obj.get("type")
        additional_kwargs = message_obj.get("additional_kwargs")
    elif hasattr(message_obj, "model_dump"):
        dumped = message_obj.model_dump()
        if isinstance(dumped, dict):
            content = dumped.get("content")
            role = dumped.get("role") or dumped.get("type")
            additional_kwargs = dumped.get("additional_kwargs")
        else:
            content = None
            role = None
            additional_kwargs = None
    else:
        content = None
        role = None
        additional_kwargs = None

    is_assistant_msg = _is_assistant_role(str(role)) if role is not None else True

    if isinstance(additional_kwargs, dict):
        function_call = additional_kwargs.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or function_call.get("tool_name") or "tool")
            args = function_call.get("args")
            if args is None:
                args = function_call.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            tool_calls.append({"name": name, "args": args if args is not None else {}})

    if isinstance(message_obj, dict):
        message_tool_calls = message_obj.get("tool_calls") or message_obj.get("tool_call_chunks")
    else:
        message_tool_calls = getattr(message_obj, "tool_calls", None) or getattr(message_obj, "tool_call_chunks", None)

    if isinstance(message_tool_calls, list):
        for tc in message_tool_calls:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or tc.get("tool_name") or tc.get("id") or "tool")
            args = tc.get("args")
            if args is None:
                args = tc.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            tool_calls.append({"name": name, "args": args if args is not None else {}})

    if isinstance(content, list):
        blocks = content
    elif isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(message_obj, dict) and "text" in message_obj:
        blocks = [{"type": "text", "text": message_obj.get("text", "")}]
    else:
        blocks = []

    for block in blocks:
        if isinstance(block, str):
            if is_assistant_msg and block:
                texts.append(block)
            continue
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type", "")).lower()

        function_call = block.get("function_call") or block.get("tool_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or function_call.get("tool_name") or "tool")
            args = function_call.get("args")
            if args is None:
                args = function_call.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            tool_calls.append({"name": name, "args": args if args is not None else {}})

        function_response = block.get("function_response") or block.get("tool_response")
        if isinstance(function_response, dict):
            name = str(function_response.get("name") or function_response.get("tool_name") or "tool")
            response_payload = function_response.get("response")
            if response_payload is None:
                response_payload = function_response.get("output")
            if response_payload is None:
                response_payload = function_response
            output_text = _extract_text_from_payload(response_payload)
            output_text = _normalize_chunk(output_text) if output_text else ""
            tool_results.append({"name": name, "output": output_text})

        if bool(block.get("thought")) or block_type in {"thinking", "thought"}:
            thought_text = block.get("text")
            if not isinstance(thought_text, str) or not thought_text:
                thought_text = block.get("thinking")
            if is_assistant_msg and isinstance(thought_text, str) and thought_text:
                thoughts.append(thought_text)
            continue

        text = block.get("text")
        if is_assistant_msg and isinstance(text, str) and text:
            texts.append(text)

    role_str = str(role).lower() if role is not None else ""
    if role_str == "tool" or getattr(message_obj, "type", "") == "tool":
        tool_name = "tool"
        if isinstance(message_obj, dict):
            tool_name = str(message_obj.get("name") or message_obj.get("tool_name") or "tool")
            tool_output = _normalize_chunk(_extract_text_from_payload(message_obj.get("content"))).strip()
        else:
            tool_name = str(getattr(message_obj, "name", None) or getattr(message_obj, "tool_name", None) or "tool")
            tool_output = _normalize_chunk(_extract_text_from_payload(getattr(message_obj, "content", ""))).strip()
        tool_results.append({"name": tool_name, "output": tool_output})

    return {
        "texts": texts,
        "thoughts": thoughts,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
    }


class StreamParser:
    def __init__(self):
        self.emitted_tool_signatures: set[str] = set()
        self.streamed_text_parts: list[str] = []
        self.streamed_thought_parts: list[str] = []
        self.streamed_any_chunk = False
        self._acc_text = ""
        self._acc_thought = ""
        self._last_values_msg_count = 0
        self._first_values_seen = False

    @staticmethod
    def _get_delta(acc: str, chunk: str) -> str:
        if not acc:
            return chunk
        if chunk.startswith(acc):
            return chunk[len(acc):]
        return chunk

    def extract_tool_events_from_values(self, payload: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        messages = []

        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            messages = payload.get("messages", [])
        elif isinstance(payload, list):
            messages = payload

        if not self._first_values_seen:
            self._last_values_msg_count = len(messages)
            self._first_values_seen = True
            return []

        new_messages = messages[self._last_values_msg_count :]
        self._last_values_msg_count = len(messages)

        for msg in new_messages:
            signals = _extract_stream_signals(msg)
            for tool_call in signals.get("tool_calls", []):
                name = str(tool_call.get("name", "tool"))
                args = tool_call.get("args", {})
                signature = f"call:{name}:{repr(args)}"
                if signature not in self.emitted_tool_signatures:
                    self.emitted_tool_signatures.add(signature)
                    events.append({"event": "tool_call", "name": name, "args": args})
            for tool_res in signals.get("tool_results", []):
                name = str(tool_res.get("name", "tool"))
                output = str(tool_res.get("output", ""))
                signature = f"result:{name}:{output.strip()}"
                if signature not in self.emitted_tool_signatures:
                    self.emitted_tool_signatures.add(signature)
                    events.append({"event": "tool_result", "name": name, "output": output})

        return events

    def parse_payload(self, payload: Any) -> list[dict[str, Any]]:
        signals = _extract_stream_signals(payload)
        events: list[dict[str, Any]] = []

        for thought_text in [str(t) for t in signals.get("thoughts", []) if t]:
            delta = self._get_delta(self._acc_thought, thought_text)
            if delta:
                self._acc_thought += delta
                self.streamed_thought_parts.append(delta)
                events.append({"event": "think", "content": delta})

        for text_chunk in [str(t) for t in signals.get("texts", []) if t]:
            delta = self._get_delta(self._acc_text, text_chunk)
            if delta:
                self._acc_text += delta
                self.streamed_text_parts.append(delta)
                self.streamed_any_chunk = True
                events.append({"event": "text", "content": delta})

        return events

    def get_final_texts(self) -> tuple[str, str]:
        return "".join(self.streamed_text_parts).strip(), "".join(self.streamed_thought_parts).strip()
