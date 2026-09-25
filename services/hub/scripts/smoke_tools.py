"""Run an isolated API → Redis → worker tool-call smoke check.

Uses a unique chat/user and private queue stream, then removes its own records.
Run from the repository root with PYTHONPATH=services/hub/src;.
"""
import asyncio
import json
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from services.shared.environment import get_redis_url
from orion.api.auth_routes import get_current_user
from orion.api.main import app
from orion.api.services import job_service
from orion.contracts.constants import (
    ACTIVE_TURN_KEY_PREFIX, CHAT_HISTORY_KEY_PREFIX, CHAT_META_KEY_PREFIX,
    CHAT_STATE_KEY_PREFIX, CHAT_USER_INDEX_PREFIX, SETTINGS_HASH_KEY_PREFIX,
)
from orion.kernel.registry import delete_chat_db
from orion.worker.services.job_processor import process_message
from orion.worker.services import job_processor
from orion.worker.services.router import close_session


async def main():
    live_router = '--live' in sys.argv
    suffix = uuid4().hex
    user_id = f"tools_smoke_{suffix}"
    stream = f"tools_smoke_queue_{suffix}"
    redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    app.state.redis = redis
    app.dependency_overrides[get_current_user] = lambda: user_id
    chat_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            catalog = await client.get('/api/v1/tools')
            catalog.raise_for_status()
            assert any(cat['id'] == 'datetime' for cat in catalog.json())

            with patch.object(job_service, 'STREAM_NAME', stream):
                created = await client.post('/api/v1/chats/messages', json={
                    'user_id': user_id,
                    'input': {'text': 'Call get_current_time for Europe/Istanbul, then tell me the returned time. Do not guess.', 'audio': False},
                    'tool_selection': {'categories': {'datetime': True}, 'functions': {'get_current_time': True}},
                })
            created.raise_for_status()
            chat_id = created.json()['chat_id']
            selected = await client.get(f'/api/v1/chats/{chat_id}/tools')
            selected.raise_for_status()
            assert selected.json()['enabled_tools'] == ['get_current_time']
            assert not selected.json()['inherited']

            jobs = await redis.xrange(stream)
            assert len(jobs) == 1
            async def fixture_router(messages, settings, **kwargs):
                if messages[-1]['role'] == 'tool':
                    result = json.loads(messages[-1]['content'])
                    assert result['timezone'] == 'Europe/Istanbul'
                    yield 'thinking', 'Formatting the clock result.'
                    yield 'content', f"Saat: {result['time']}"
                else:
                    assert kwargs['tools'][0]['function']['name'] == 'get_current_time'
                    yield 'thinking', 'Checking the clock.'
                    yield 'content', 'Checking the time. '
                    yield 'tool_calls', json.dumps([{
                        'id': 'clock_call', 'type': 'function',
                        'function': {'name': 'get_current_time', 'arguments': '{"timezone":"Europe/Istanbul"}'},
                    }])
            if live_router:
                await process_message(redis, jobs[0][0], jobs[0][1], 'tools-smoke')
            else:
                with patch.object(job_processor, 'llama_stream_chat_typed', fixture_router):
                    await process_message(redis, jobs[0][0], jobs[0][1], 'tools-smoke')
            history = await client.get(f'/api/v1/chats/{chat_id}/history')
            history.raise_for_status()
            messages = history.json()
            assert any(msg.get('role') == 'tool' for msg in messages), messages
            answer = next(msg for msg in reversed(messages) if msg.get('role') == 'assistant')
            assert answer['tool_activity'][0]['status'] == 'completed', answer
            assert answer.get('display_parts'), answer
            if not live_router:
                assert [part['type'] for part in answer['display_parts']] == [
                    'thinking', 'content', 'tool', 'thinking', 'content',
                ], answer['display_parts']
            print(f'API -> queue -> worker -> {"live Router" if live_router else "Router fixture"} -> clock -> persisted history: PASS')
            print('Answer:', answer['content'][:250])

            # The chat-specific override must survive a Redis cache miss.
            await redis.delete(f'orion:tool-selection:{chat_id}')
            restored = await client.get(f'/api/v1/chats/{chat_id}/tools')
            restored.raise_for_status()
            assert restored.json()['enabled_tools'] == ['get_current_time']
            print('Chat selection DB hydration: PASS')

            disabled = await client.put(f'/api/v1/chats/{chat_id}/tools', json={
                'categories': {'datetime': False}, 'functions': {'get_current_time': True},
            })
            disabled.raise_for_status()
            assert disabled.json()['enabled_tools'] == []
            assert disabled.json()['selection']['functions']['get_current_time']
            reset = await client.delete(f'/api/v1/chats/{chat_id}/tools')
            reset.raise_for_status()
            assert reset.json()['inherited']
            print('Category disable and chat reset: PASS')
    finally:
        app.dependency_overrides.clear()
        try:
            if chat_id:
                await delete_chat_db(chat_id)
                await redis.delete(*(
                    f'{CHAT_META_KEY_PREFIX}{chat_id}', f'{CHAT_STATE_KEY_PREFIX}{chat_id}',
                    f'{CHAT_HISTORY_KEY_PREFIX}{chat_id}', f'{ACTIVE_TURN_KEY_PREFIX}{chat_id}',
                    f'orion:tool-selection:{chat_id}',
                ))
                await redis.zrem(f'{CHAT_USER_INDEX_PREFIX}{user_id}', chat_id)
                await redis.delete(f'{CHAT_USER_INDEX_PREFIX}{user_id}')
            await redis.delete(stream, f'{SETTINGS_HASH_KEY_PREFIX}{user_id}')
        finally:
            await close_session()
            await redis.aclose()


if __name__ == '__main__':
    asyncio.run(main())
