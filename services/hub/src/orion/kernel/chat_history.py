"""Failed turns remain visible, but never enter model context."""

import json
import logging
import hashlib


def turn_groups(history):
    groups = []
    for message in history:
        if message.get('role') == 'user' or not groups:
            groups.append([])
        groups[-1].append(message)
    return groups


def failed_turn_key(group):
    """Stable across deletions, including old records without a turn id."""
    turn_id = next((m['turn_id'] for m in group if m.get('turn_id')), None)
    return turn_id or 'legacy-' + hashlib.sha256(
        json.dumps(group, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def remove_failed_turn(history, key):
    groups = turn_groups(history)
    target = next((g for g in groups if failed_turn_key(g) == key), None)
    if target is None:
        return history, []
    if successful_history(target):
        raise ValueError('Only failed turns can be deleted')
    return [m for g in groups if g is not target for m in g], target


def referenced_media_ids(value):
    if isinstance(value, list):
        return set().union(*(referenced_media_ids(item) for item in value))
    if isinstance(value, dict):
        result = {value['media_id']} if value.get('media_id') else set()
        return result.union(*(referenced_media_ids(item) for item in value.values()))
    return set()


def failed_content(error, partial=''):
    """Keep Markdown fences on their own lines, without a trailing bracket."""
    return (partial.rstrip() + '\n\n' if partial else '') + f'Hata: {error}\n'


async def persist_failed_turn(redis, chat_id, turn_id, user_message, error, state=None, metrics=None):
    """Save the failed question and response before publishing the terminal event."""
    from orion.contracts.constants import CHAT_HISTORY_KEY_PREFIX
    from orion.kernel.registry import insert_messages
    from orion.kernel.job_safety import job_key, TERMINAL_TTL
    if not await redis.hsetnx(job_key(turn_id), 'history_saved', '1'):
        return  # A newer turn replaced this one and already saved it.
    state = state or {}
    partial = state.get('partial_text', '')
    assistant = {
        'role': 'assistant', 'turn_id': turn_id, 'failed_turn': True,
        'generation_error': error,
        'content': failed_content(error, partial),
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
    await pipe.execute()
    try:
        await insert_messages(chat_id=chat_id, messages=messages)
    except Exception:
        logging.exception('Failed turn saved in Redis but not PostgreSQL for chat %s', chat_id)


def visible_history(history: list[dict]) -> list[dict]:
    """Also label older error records and their questions without deleting them."""
    keep = {id(message) for message in successful_history(history)}
    visible = []
    for group in turn_groups(history):
        key = failed_turn_key(group) if any(id(m) not in keep for m in group) else None
        for message in group:
            if id(message) in keep:
                visible.append(message)
                continue
            message = {**message, 'failed_turn': True, 'failed_turn_key': key}
            error = message.get('generation_error')
            content = message.get('content')
            if error and isinstance(content, str) and content.endswith(f'[Hata: {error}]'):
                message['content'] = failed_content(error, content[:-len(f'[Hata: {error}]')].rstrip())
            visible.append(message)
    return visible


def successful_history(history: list[dict]) -> list[dict]:
    groups = turn_groups(history)
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
