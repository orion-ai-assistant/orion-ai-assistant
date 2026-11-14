"""Older messages kept their media inside the message: base64 images, attachments, voice recordings.

Versions before the media store (September-October 2026) wrote them into content_json, so a chat of a few
hundred messages was tens of megabytes that every read of the chat carried. At start, each such message
gets the same shape new messages have: the bytes go to the media store (orion_media by SHA-256, linked
to the chat, as media_store writes them) and the message keeps a reference. The text of a message never changes.
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


TABLE_LOCK = 51730103  # one process moves orion_chat_media; the others wait for it, then find nothing left
TABLE_BATCH_ROWS = 20
TABLE_BATCH_BYTES = 64 * 1024 * 1024


async def migrate_chat_media_table(conn) -> dict:
    """Move orion_chat_media (one copy per chat, versions before November 2026) into orion_media and its chat
    links. Each row is marked migrated_at in the same transaction that stores it, so an interrupted run resumes
    where it stopped and a finished one never brings back a link deleted since. The old table keeps its bytes
    until a later version drops it (see legacy_chat_media_gaps). Returns counts of this run."""
    result = {'moved': 0, 'skipped': 0}
    if not await conn.fetchval("select to_regclass('orion_chat_media') is not null"):
        return result
    if not await conn.fetchval("""select exists(select 1 from information_schema.columns
            where table_name = 'orion_chat_media' and column_name = 'migrated_at')"""):
        await conn.execute('select pg_advisory_lock($1)', TABLE_LOCK)
        try:
            await conn.execute('alter table orion_chat_media add column if not exists migrated_at timestamptz')
        finally:
            await conn.execute('select pg_advisory_unlock($1)', TABLE_LOCK)
    if not await conn.fetchval('select exists(select 1 from orion_chat_media where migrated_at is null)'):
        return result
    # Blocking: a process that would read media meanwhile waits until everything is reachable.
    await conn.execute('select pg_advisory_lock($1)', TABLE_LOCK)
    try:
        after = ('', '')
        while True:
            rows = await conn.fetch("""select chat_id, id, octet_length(data) as size from orion_chat_media
                where migrated_at is null and (chat_id, id) > ($1, $2) order by chat_id, id limit 200""", *after)
            if not rows:
                break
            after = (rows[-1]['chat_id'], rows[-1]['id'])
            batch = []
            for row in rows:
                if batch and (row['chat_id'] != batch[0]['chat_id'] or len(batch) >= TABLE_BATCH_ROWS
                              or sum(r['size'] for r in batch) + row['size'] > TABLE_BATCH_BYTES):
                    await _move_rows(conn, batch, result)
                    batch = []
                batch.append(row)
            await _move_rows(conn, batch, result)
    finally:
        await conn.execute('select pg_advisory_unlock($1)', TABLE_LOCK)
    if result['moved'] or result['skipped']:
        logger.info('Chat media moved to the shared media store: %d file(s), %d left in the old table',
                    result['moved'], result['skipped'])
    return result


async def _move_rows(conn, rows, result) -> None:
    chat_id = rows[0]['chat_id']
    async with conn.transaction():
        # Serialized with chat deletion and media writes, which take this lock first too.
        if not await conn.fetchval('select id from orion_chats where id = $1 for update', chat_id):
            return  # Deleted meanwhile: its old rows went with it.
        for row in sorted(rows, key=lambda r: r['id']):
            media_id = row['id']
            row = await conn.fetchrow("""select encode(sha256(data), 'hex') = id as intact, created_at, last_used_at
                from orion_chat_media where chat_id = $1 and id = $2 and migrated_at is null""", chat_id, media_id)
            if row is None:
                continue
            if not row['intact']:
                logger.warning('Chat media %s of chat %s does not match its hash; left in the old table', media_id, chat_id)
                result['skipped'] += 1
                continue
            if not await conn.fetchval('select true from orion_media where id = $1 for share', media_id):
                # The bytes go from table to table inside the database, never through the Hub.
                await conn.execute("""
                    insert into orion_media(id, mime_type, size, data, created_at)
                    select id, mime_type, octet_length(data), data, created_at from orion_chat_media
                    where chat_id = $1 and id = $2
                    on conflict (id) do update set id = excluded.id where false
                """, chat_id, media_id)
            await conn.execute("""
                insert into orion_media_links(media_id, owner_kind, owner_id, created_at, last_used_at)
                values($2, 'chat', $1, $3, $4)
                on conflict (media_id, owner_kind, owner_id)
                do update set last_used_at = greatest(orion_media_links.last_used_at, excluded.last_used_at)
            """, chat_id, media_id, row['created_at'], row['last_used_at'])
            await conn.execute('update orion_chat_media set migrated_at = now() where chat_id = $1 and id = $2',
                               chat_id, media_id)
            result['moved'] += 1


async def legacy_chat_media_gaps(conn) -> int:
    """Rows of the old table whose bytes are not reachable, identical, through the same chat in the new store.
    Zero right after the move; the version that drops orion_chat_media checks the rows not deleted since."""
    if not await conn.fetchval("select to_regclass('orion_chat_media') is not null"):
        return 0
    return await conn.fetchval("""
        select count(*) from orion_chat_media o
        where not exists (select 1 from orion_media_links l join orion_media m on m.id = l.media_id
                          where l.media_id = o.id and l.owner_kind = 'chat' and l.owner_id = o.chat_id
                            and m.data = o.data)
    """)


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
