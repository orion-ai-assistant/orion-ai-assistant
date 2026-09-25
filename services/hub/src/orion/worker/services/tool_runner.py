import asyncio
import json
from contextlib import aclosing

from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry


class ToolCancelled(Exception):
    pass


async def execute_tool(tool, arguments, context):
    """Only async, cooperative handlers; cancellation never retries a handler."""
    if await context.cancelled():
        raise ToolCancelled()
    args = tool.input_model.model_validate_json(arguments)
    task = asyncio.create_task(tool.handler(args, context))
    try:
        async with asyncio.timeout(tool.timeout_seconds):
            while True:
                ready, _ = await asyncio.wait({task}, timeout=0.05)
                if await context.cancelled():
                    raise ToolCancelled()
                if ready:
                    result = json.dumps(task.result(), ensure_ascii=False, allow_nan=False)
                    if len(result.encode("utf-8")) > 32768:
                        raise ValueError("Tool result exceeds 32 KiB")
                    return json.loads(result)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class ToolConversation:
    def __init__(self, context, enabled):
        self.context = context
        self.enabled = set(enabled)
        self.transcript = []
        self.activity = []
        self.final_content = ""
        self.rounds = 0

    async def stream(self, messages, settings, stop_checker, stream_fn):
        registry = get_registry()
        unknown = self.enabled - registry.tools.keys()
        if unknown:
            raise RuntimeError(f"Tools no longer installed: {', '.join(sorted(unknown))}")
        schemas = [registry.tools[name].schema() for name in sorted(self.enabled)]
        context = ToolContext(self.context.user_id, self.context.chat_id, self.context.turn_id, stop_checker)
        prefix = ""
        seen_ids = set()
        while not await stop_checker():
            calls = []
            round_content = ""
            kwargs = {"stop_checker": stop_checker}
            if schemas:
                kwargs["tools"] = schemas
            async with aclosing(stream_fn(messages, settings, **kwargs)) as stream:
                async for kind, value in stream:
                    if kind == "tool_calls":
                        calls = json.loads(value)
                    elif kind == "tool_delta":
                        continue
                    elif kind == "content":
                        round_content += value
                        self.final_content = round_content
                        yield kind, value
                    elif kind == "content_snapshot":
                        round_content = value
                        self.final_content = round_content
                        yield kind, prefix + value
                    else:
                        # A multi-request turn is timed by the worker, not the last request.
                        if kind != "metrics" or self.rounds == 0:
                            yield kind, value
            if await stop_checker() or not calls:
                return
            self.rounds += 1
            if self.rounds > 8 or len(seen_ids) + len(calls) > 16:
                raise RuntimeError("Tool limit exceeded (8 rounds / 16 calls)")
            if any(call["id"] in seen_ids for call in calls):
                raise RuntimeError("Repeated tool call ID; execution refused")
            seen_ids.update(call["id"] for call in calls)
            assistant = {"role": "assistant", "content": round_content or None, "tool_calls": calls}
            messages.append(assistant)
            self.transcript.append(assistant)
            self.final_content = ""
            cancelled = False
            for call in calls:
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
                item = {"call_id": call["id"], "name": name,
                        "label": registry.tools[name].name if name in registry.tools else name,
                        "arguments": arguments, "status": "running", "turn_id": context.turn_id}
                self.activity.append(item)
                yield "tool_call", json.dumps(item)
                try:
                    if cancelled or await stop_checker():
                        raise ToolCancelled()
                    if name not in self.enabled or name not in registry.tools:
                        raise ValueError("Tool is not enabled or registered")
                    result = await execute_tool(registry.tools[name], arguments, context)
                    item.update(status="completed", result=result)
                except ToolCancelled:
                    cancelled = True
                    item.update(status="cancelled", result={"error": {"type": "cancelled", "message": "Tool execution cancelled"}})
                except Exception as exc:
                    item.update(status="error", result={"error": {"type": type(exc).__name__, "message": str(exc)[:2000] or "Tool timed out"}})
                result_message = {"role": "tool", "tool_call_id": call["id"],
                                  "content": json.dumps(item["result"], ensure_ascii=False)}
                messages.append(result_message)
                self.transcript.append(result_message)
                yield "tool_result", json.dumps(item)
            if cancelled or await stop_checker():
                return
            prefix += round_content
            self.final_content = ""
