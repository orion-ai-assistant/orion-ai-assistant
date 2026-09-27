"""Lazy bridges for SDK functions and LangChain-compatible tools.

Factories run per invocation, never during catalog discovery. Async integrations
must support cooperative cancellation. Thread mode is only for read-only calls:
cancelling an await cannot stop a running thread or undo its external effects.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from ._base import Tool, ToolContext


def _identity(value):
    return value


def _arguments(args):
    return args.model_dump()


def from_callable(
    *,
    id: str,
    name: str,
    description: str,
    input_model: type[BaseModel],
    factory: Callable[[ToolContext], Callable[..., Any]],
    execution: Literal["async", "thread"] = "async",
    read_only: bool = False,
    map_input: Callable[[Any], dict] = _arguments,
    map_output: Callable[[Any], Any] = _identity,
    timeout_seconds: float = 30,
) -> Tool:
    """Call a lazily constructed SDK function with validated keyword arguments.

Factories must be local/nonblocking in async mode. They receive trusted Orion
context for per-user client configuration; credentials are not model arguments.
Thread integrations must configure their own bounded network timeout as well.
Output mapping runs on the event loop and must be cheap and nonblocking.
"""
    if execution not in ("async", "thread"):
        raise ValueError("execution must be 'async' or 'thread'")
    if execution == "thread" and not read_only:
        raise ValueError("Thread integrations must explicitly declare read_only=True")

    async def handler(args, context):
        kwargs = map_input(args)
        if execution == "async":
            result = await factory(context)(**kwargs)
        else:
            # Construct sync clients on the same thread that will use them.
            result = await asyncio.to_thread(lambda: factory(context)(**kwargs))
        return map_output(result)

    return Tool(id=id, name=name, description=description, input_model=input_model,
                handler=handler, timeout_seconds=timeout_seconds)


def from_langchain(
    *,
    id: str,
    name: str,
    description: str,
    input_model: type[BaseModel],
    factory: Callable[[ToolContext], Any],
    native_async: bool,
    read_only: bool = False,
    map_input: Callable[[Any], Any] = _arguments,
    map_output: Callable[[Any], Any] = _identity,
    timeout_seconds: float = 30,
) -> Tool:
    """Wrap invoke/ainvoke without importing or depending on LangChain at startup.

Declare native_async only after checking the provider: the presence of ainvoke
alone is insufficient, since LangChain may implement it with a thread fallback.
Pass ordinary arguments, not a ToolCall envelope; normalize Document/ToolMessage
or other provider objects explicitly in map_output when necessary.
"""
    def make_call(context):
        external = factory(context)
        return external.ainvoke if native_async else external.invoke

    return from_callable(
        id=id, name=name, description=description, input_model=input_model,
        factory=make_call, execution="async" if native_async else "thread",
        read_only=read_only, map_input=lambda args: {"input": map_input(args)},
        map_output=map_output, timeout_seconds=timeout_seconds,
    )
