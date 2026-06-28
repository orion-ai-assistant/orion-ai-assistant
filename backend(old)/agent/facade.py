"""Public facade for agent execution.

This module hides graph internals from callers. External layers should call
`run_request` / `stream_request` and avoid importing `agent.graph` directly.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage

from .graph import get_graph


def _content_to_text(content) -> str:
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
        return "".join(parts).strip()
    return str(content) if content is not None else ""


def _build_messages(
    *,
    messages: list | None,
    text: str | None,
    prompt: str | None,
    voice: str | None,
    image: str | None,
) -> list:
    if messages is not None:
        return list(messages)

    sections: list[str] = []
    if prompt:
        sections.append(prompt)
    if text:
        sections.append(text)
    if voice:
        sections.append(f"[voice]\n{voice}")
    if image:
        sections.append(f"[image]\n{image}")

    if not sections:
        raise ValueError("run_request requires messages or one of text/prompt/voice/image")

    return [HumanMessage(content="\n\n".join(sections))]


async def stream_request(
    *,
    messages: list | None = None,
    text: str | None = None,
    prompt: str | None = None,
    voice: str | None = None,
    image: str | None = None,
    active_agent_name: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
) -> AsyncIterator[tuple[str, object]]:
    """Stream low-level graph events while hiding graph selection details."""
    req_messages = _build_messages(
        messages=messages,
        text=text,
        prompt=prompt,
        voice=voice,
        image=image,
    )
    graph_instance = get_graph(active_agent_name)

    run_config: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    configurable: dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if active_agent_name:
        metadata["active_agent_name"] = active_agent_name
        configurable["active_agent_name"] = active_agent_name
    if thread_id:
        metadata["thread_id"] = thread_id
        configurable["thread_id"] = thread_id
    if metadata:
        run_config["metadata"] = metadata
    if configurable:
        run_config["configurable"] = configurable

    async for stream_type, data in graph_instance.astream(
        {"messages": req_messages},
        config=run_config or None,
        stream_mode=["messages", "values"],
    ):
        yield stream_type, data


async def run_request(
    *,
    messages: list | None = None,
    text: str | None = None,
    prompt: str | None = None,
    voice: str | None = None,
    image: str | None = None,
    active_agent_name: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
) -> dict:
    """Run one full request and return final text plus final message list."""
    req_messages = _build_messages(
        messages=messages,
        text=text,
        prompt=prompt,
        voice=voice,
        image=image,
    )
    graph_instance = get_graph(active_agent_name)

    run_config: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    configurable: dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if active_agent_name:
        metadata["active_agent_name"] = active_agent_name
        configurable["active_agent_name"] = active_agent_name
    if thread_id:
        metadata["thread_id"] = thread_id
        configurable["thread_id"] = thread_id
    if metadata:
        run_config["metadata"] = metadata
    if configurable:
        run_config["configurable"] = configurable

    state = await graph_instance.ainvoke({"messages": req_messages}, config=run_config or None)

    final_messages = list(state.get("messages", req_messages)) if isinstance(state, dict) else req_messages

    final_text = ""
    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage):
            final_text = _content_to_text(getattr(msg, "content", ""))
            if final_text:
                break

    return {
        "final_text": final_text,
        "final_messages": final_messages,
    }
