"""Failed turns remain visible, but never enter model context."""

import json
import logging


async def persist_failed_turn(redis, chat_id, turn_id, user_message, error, state=None, metrics=None):
    """Save the failed question and response before publishing the terminal event."""
    from orion.contracts.constants import CHAT_HISTORY_KEY_PREFIX
    from orion.kernel.registry import insert_messages
    from orion.kernel.job_safety import job_key, TERMINAL_TTL
    state = state or {}
    partial = state.get('partial_text', '')
    assistant = {
        'role': 'assistant', 'turn_id': turn_id, 'failed_turn': True,
        'generation_error': error,
        'content': f'{partial}\n\n[Hata: {error}]'.strip(),
    }
    if state.get('partial_thinking'):
        assistant['thinking'] = state['partial_thinking']
    if state.get('partial_tools'):
        assistant['tool_activity'] = json.loads(state['partial_tools'])
    if metrics:
        assistant['metrics'] = metrics
    messages = [{**user_message, 'role': 'user', 'turn_id': turn_id, 'failed_turn': True}, assistant]
    pipe = redis.pipeline()
    for message in messages:
        pipe.rpush(CHAT_HISTORY_KEY_PREFIX + chat_id, json.dumps(message))
    pipe.expire(CHAT_HISTORY_KEY_PREFIX + chat_id, TERMINAL_TTL)
    pipe.hset(job_key(turn_id), 'history_saved', '1')
    await pipe.execute()
    try:
        await insert_messages(chat_id=chat_id, messages=messages)
    except Exception:
        logging.exception('Failed turn saved in Redis but not PostgreSQL for chat %s', chat_id)


def visible_history(history: list[dict]) -> list[dict]:
    """Also label older error records and their questions without deleting them."""
    keep = {id(message) for message in successful_history(history)}
    return [message if id(message) in keep else {**message, 'failed_turn': True}
            for message in history]


def successful_history(history: list[dict]) -> list[dict]:
    groups = []
    for message in history:
        if message.get('role') == 'user' or not groups:
            groups.append([])
        groups[-1].append(message)
    prefixes = (
        '⚠️ **Yönlendirici (Router) veya AI Modeline Bağlanılamadı',
        '⚠️ **Model Yanıt Zaman Aşımı**',
        '⚠️ **Yönlendirici (Router) Hatası:**',
    )
    return [message for group in groups
            if not any(item.get('failed_turn') or item.get('generation_error') or
                       (item.get('role') == 'assistant' and
                        isinstance(item.get('content'), str) and
                        item['content'].startswith(prefixes)) for item in group)
            for message in group]
