import asyncio
import json
import re
from contextlib import aclosing
from dataclasses import replace
from urllib.parse import quote

from orion.contracts.tools import LEGACY_WEB_TOOLS
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry

WEB_TOOLS = {"research_web", "google_ai_mode", *LEGACY_WEB_TOOLS}


def google_failure_message(result):
    return {
        'timeout': 'Google AI Mode’dan süre içinde okunabilir bir yanıt alınamadı.',
        'blocked': 'Google, CAPTCHA veya trafik engeli gösterdi.',
        'interaction_required': 'Google tarayıcıda giriş veya doğrulama istiyor.',
        'busy': 'Başka bir tarayıcı işlemi sürüyor. Tamamlandıktan sonra yeniden deneyin.',
        'verification_required': 'Tarayıcı doğrulaması gerekiyor. Ayarlar → Tarayıcı bölümünden bağlanın.',
        'not_configured': 'Mevcut tarayıcınıza bağlanmanız gerekiyor.',
        'unavailable': 'Google AI Mode bu oturumda kullanılamıyor.',
    }.get(result.get('status'), 'Google AI Mode yanıtı alınamadı.')


class ToolCancelled(Exception):
    pass


def google_direct_text(answer, result=None, *, answer_format=None):
    def escape(text):
        return re.sub(r'([\\`*_{}\[\]<>])', r'\\\1', text)
    answer_format = answer_format or (result or {}).get('answer_format', 'text')
    text = '**Google AI Mode**\n\n' + (answer if answer_format == 'markdown' else escape(answer))
    if result:
        if result.get('sources'):
            text += '\n\n**Kaynaklar**\n\n' + '\n'.join(
                '- [' + escape(source.get('title') or source['url']) + '](<' +
                quote(source['url'], safe=':/?&=%#@+;,-._~') + '>)' for source in result['sources'])
    return text


async def stream_tool_answer(tool, arguments, context):
    """Coalesce page snapshots; closing the consumer also cancels its browser task."""
    snapshots = asyncio.Queue(maxsize=1)

    async def emit(text):
        if snapshots.full():
            snapshots.get_nowait()
        snapshots.put_nowait(text)

    task = asyncio.create_task(execute_tool(tool, arguments, replace(context, emit_answer=emit)))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.05)
            if not snapshots.empty():
                yield 'answer', snapshots.get_nowait()
        if not snapshots.empty():
            yield 'answer', snapshots.get_nowait()
        yield 'result', task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
                    return json.loads(result)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class ToolConversation:
    def __init__(self, context, enabled):
        self.context = context
        self.enabled = set(enabled)
        # Queued jobs can still contain the old catalog IDs after an upgrade.
        if self.enabled & LEGACY_WEB_TOOLS:
            self.enabled.difference_update(LEGACY_WEB_TOOLS)
            self.enabled.add("research_web")
        self.transcript = []
        self.activity = []
        self.final_content = ""
        self.direct_tool_call_id = None
        self.rounds = 0
        self.research_used = False

    async def stream(self, messages, settings, stop_checker, stream_fn):
        registry = get_registry()
        unknown = self.enabled - registry.tools.keys()
        if unknown:
            raise RuntimeError(f"Tools no longer installed: {', '.join(sorted(unknown))}")
        context = ToolContext(self.context.user_id, self.context.chat_id, self.context.turn_id, stop_checker)
        prefix = ""
        seen_ids = set()
        while not await stop_checker():
            schemas = [registry.tools[name].schema() for name in sorted(self.enabled)
                       if not (self.research_used and name in WEB_TOOLS)]
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
            if len({call['id'] for call in calls}) != len(calls):
                raise RuntimeError("Repeated tool call ID in one response")
            # Some model backends reuse IDs on the next response. Give those
            # calls unique transcript IDs before sending their tool results.
            occupied_ids = seen_ids | {call['id'] for call in calls}
            for index, call in enumerate(calls):
                if call['id'] in seen_ids:
                    original = call['id']
                    replacement = f'{original}_{self.rounds}_{index}'
                    while replacement in occupied_ids:
                        replacement += '_'
                    call['id'] = replacement
                    occupied_ids.add(replacement)
            seen_ids.update(call["id"] for call in calls)
            assistant = {"role": "assistant", "content": round_content or None, "tool_calls": calls}
            messages.append(assistant)
            self.transcript.append(assistant)
            self.final_content = ""
            cancelled = False
            google_failure = None
            google_direct_result = None
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
                    if self.research_used and name in WEB_TOOLS:
                        raise ValueError("Research is complete for this request. Answer from the existing sources "
                                         "or explain missing evidence; do not call web tools again.")
                    if name in {"research_web", "google_ai_mode"}:
                        self.research_used = True
                    if name == 'google_ai_mode' and settings.google_ai_direct_stream:
                        async with aclosing(stream_tool_answer(registry.tools[name], arguments, context)) as updates:
                            async for update, value in updates:
                                if update == 'answer':
                                    self.final_content = google_direct_text(value, answer_format='markdown')
                                    yield 'content_snapshot', prefix + round_content + self.final_content
                                else:
                                    result = value
                        google_direct_result = result
                    else:
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
            if google_direct_result and google_direct_result.get('status') in ('ok', 'partial') and google_direct_result.get('answer'):
                self.final_content = google_direct_text(google_direct_result['answer'], google_direct_result)
                self.direct_tool_call_id = next(
                    entry['call_id'] for entry in self.activity
                    if entry['name'] == 'google_ai_mode' and entry['result'] is google_direct_result)
                yield 'content_snapshot', prefix + round_content + self.final_content
                return
            prefix += round_content
            self.final_content = ""
