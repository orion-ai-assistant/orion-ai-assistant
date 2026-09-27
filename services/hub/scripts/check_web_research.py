"""Run the live news acceptance check through Orion's configured router.

From the repository root, with hub/src and the root on PYTHONPATH:
python services/hub/scripts/check_web_research.py --model <router-model> --output <report.json>
This invokes the selected model and public websites; it does not modify chat/settings data.
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from orion.contracts.settings import RuntimeSettings
from orion.worker.services.router import close_session, llama_stream_chat_typed
from orion.worker.services.tool_runner import ToolConversation

PROMPT = "Bugün Türkiye’de teknoloji alanındaki en önemli 3 haberi web’de ara ve kaynaklarıyla özetle."


async def run(model, output):
    started = monotonic()
    report = {"prompt": PROMPT, "model": model, "started_at": datetime.now(ZoneInfo("Europe/Istanbul")).isoformat()}
    conversation = ToolConversation(
        SimpleNamespace(user_id='live-check', chat_id='live-check', turn_id=report['started_at']),
        ['research_web'],
    )
    settings = RuntimeSettings(router_model_group=model)

    async def stopped():
        return False

    model_requests = []

    async def measured_stream(messages, settings, **kwargs):
        request = {"input_json_bytes": len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))}
        model_requests.append(request)
        async for kind, value in llama_stream_chat_typed(messages, settings, **kwargs):
            if kind == 'metrics':
                request['metrics'] = json.loads(value)
            yield kind, value

    try:
        async with asyncio.timeout(180):
            async for kind, value in conversation.stream([
                {"role": "system", "content": settings.system_prompt},
                {"role": "user", "content": PROMPT},
            ], settings, stopped, measured_stream):
                if kind == 'tool_result':
                    item = json.loads(value)
                    print(json.dumps({'tool': item['name'], 'status': item['result'].get('status', item['status']),
                                      'duration_ms': item['result'].get('duration_ms')}, ensure_ascii=True), flush=True)
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:500]}
    finally:
        await close_session()
        report.update(duration_ms=round((monotonic() - started) * 1000),
                      final_answer=conversation.final_content,
                      research_used=conversation.research_used, model_calls=len(model_requests),
                      model_requests=model_requests, activity=conversation.activity)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Report: {output}', flush=True)
    return 1 if 'error' in report else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run(args.model, args.output)))
