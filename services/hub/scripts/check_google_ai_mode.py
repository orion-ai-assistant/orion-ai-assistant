"""One live tool call, without a model call or changes to saved chat settings."""

import argparse
import asyncio
import json
from pathlib import Path

from orion.worker.services.tool_runner import execute_tool
from orion.worker.tools._base import ToolContext
from orion.worker.tools.google_ai.tools import GOOGLE_AI_MODE


async def run(query, output):
    async def cancelled():
        return False

    result = await execute_tool(GOOGLE_AI_MODE, json.dumps({'query': query}),
                                ToolContext('live-check', 'live-check', 'google-ai-check', cancelled))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] in ('ok', 'partial') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.query, args.output)))
