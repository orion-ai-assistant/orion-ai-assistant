"""Agent package public API.

Exports stay stable, but imports are lazy so importing submodules like
`agent.tools` does not eagerly require model provider dependencies.
"""

from __future__ import annotations

from typing import Any


def set_active_model(model_name: str | None) -> None:
	from .model import set_active_model as _set_active_model

	_set_active_model(model_name)


def set_runtime_debug_mode(enabled: bool) -> None:
	from .debug import set_runtime_debug_mode as _set_runtime_debug_mode

	_set_runtime_debug_mode(enabled)


async def run_request(*args: Any, **kwargs: Any) -> dict:
	from .facade import run_request as _run_request

	return await _run_request(*args, **kwargs)


def stream_request(*args: Any, **kwargs: Any):
	from .facade import stream_request as _stream_request

	return _stream_request(*args, **kwargs)


__all__ = [
	"set_active_model",
	"set_runtime_debug_mode",
	"run_request",
	"stream_request",
]
