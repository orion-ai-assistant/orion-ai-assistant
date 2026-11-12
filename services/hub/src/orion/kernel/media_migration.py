"""Older messages kept their media inside the message: base64 images, attachments, voice recordings.

Versions before the media store (September-October 2026) wrote them into content_json, so a chat of a few
hundred messages was tens of megabytes that every read of the chat carried. At start, each such message
gets the same shape new messages have: the bytes go to orion_chat_media (by SHA-256, as media_store
writes them) and the message keeps a reference. The text of a message never changes.
"""
import base64
import binascii
import json
import logging

from orion.kernel import registry
from orion.kernel.media_store import _put, decode_data_url, ensure_table

logger = logging.getLogger(__name__)

BIG = 100_000  # characters of content_json; the only rows worth opening
BATCH = 5
LOCK = 51730102  # one API process migrates; the others skip
_AUDIO_MIME = {'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg', 'flac': 'audio/flac', 'm4a': 'audio/mp4'}


def _audio_mime(fmt: str) -> str:
    return _AUDIO_MIME.get(fmt, f'audio/{fmt}')


async def _reference(conn, chat_id, mime, raw):
    ref = await _put(conn, chat_id, mime, raw)
    return {'mime_type': ref['mime_type'], 'media_id': ref['media_id'], 'media_path': ref['media_path'],
            'size': ref['size']}


async def _migrate_message(conn, chat_id: str, message: dict) -> bool:
    changed = False
    attachments = message.get('attachments')
    for item in attachments if isinstance(attachments, list) else []:
        data = item.get('data') if isinstance(item, dict) else None
        if isinstance(data, str) and data.startswith('data:') and not item.get('isText') and not item.get('media_id'):
            mime, raw = decode_data_url(data)
            item.pop('data')
            item.update(await _reference(conn, chat_id, mime, raw))
            changed = True
    content = message.get('content')
    if isinstance(content, list):
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            kind, mime, raw = part.get('type'), None, None
            if kind == 'image_url' and str((part.get('image_url') or {}).get('url', '')).startswith('data:'):
                mime, raw = decode_data_url(part['image_url']['url'])
            elif kind in ('input_audio', 'input_video') and isinstance((part.get(kind) or {}).get('data'), str):
                fmt = part[kind].get('format') or ('wav' if kind == 'input_audio' else 'mp4')
                mime = _audio_mime(fmt) if kind == 'input_audio' else f'video/{fmt}'
                raw = base64.b64decode(part[kind]['data'], validate=True)
            if raw:
                # The Worker turns this back into the same part when the model needs it (hydrate_model_media).
                content[index] = {'type': 'hub_media', 'attachment': await _reference(conn, chat_id, mime, raw)}
                changed = True
    audio = message.get('audio')
    if isinstance(audio, dict) and isinstance(audio.get('audio'), str) and audio['audio'] and not audio.get('media_id'):
        raw = base64.b64decode(audio['audio'], validate=True)
        fmt = audio.get('format') or 'wav'
        message['audio'] = {**{k: v for k, v in audio.items() if k != 'audio'},
                            **await _reference(conn, chat_id, _audio_mime(fmt), raw), 'format': fmt}
        changed = True
    return changed


async def migrate_inline_media() -> int:
    """Move every older message's inline media to the media store; returns how many messages changed."""
    conn = await registry._connect()
    if conn is None:
        return 0
    try:
        await registry._ensure_tables(conn)
        await ensure_table(conn)
        if not await conn.fetchval('select pg_try_advisory_lock($1)', LOCK):
            return 0
        try:
            moved, last_id = 0, 0
            while True:
                rows = await conn.fetch(
                    'select id, chat_id, content_json from orion_chat_messages '
                    'where id > $1 and length(content_json) > $2 order by id limit $3', last_id, BIG, BATCH)
                if not rows:
                    break
                for row in rows:
                    last_id = row['id']
                    try:
                        message = json.loads(row['content_json'])
                        async with conn.transaction():
                            # Serialized with chat deletion, like every media write.
                            if not await conn.fetchval('select id from orion_chats where id=$1 for update', row['chat_id']):
                                continue
                            if isinstance(message, dict) and await _migrate_message(conn, row['chat_id'], message):
                                await conn.execute('update orion_chat_messages set content_json = $2 where id = $1',
                                                   row['id'], json.dumps(message))
                                moved += 1
                    except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
                        logger.warning('Inline media of message %s left in place: %s', row['id'], exc)
            if moved:
                await _compact(conn)
            return moved
        finally:
            await conn.execute('select pg_advisory_unlock($1)', LOCK)
    finally:
        await conn.close()


async def _compact(conn) -> None:
    """Give the freed space back to the disk. VACUUM FULL holds the table for the second or two it takes to
    copy what is left; if a turn is writing, it is not waited for and runs at a later start."""
    try:
        await conn.execute("set lock_timeout = '3s'")
        await conn.execute('vacuum (full, analyze) orion_chat_messages')
    except Exception as exc:
        logger.info('Chat messages table not compacted this time: %s', exc)
    finally:
        await conn.execute('reset lock_timeout')
