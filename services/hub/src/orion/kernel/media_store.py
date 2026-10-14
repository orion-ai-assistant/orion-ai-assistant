"""Durable, chat-scoped media. JSON and Redis carry references, never blobs."""
import asyncio
import base64
import copy
import hashlib
import io
from urllib.parse import quote

from orion.kernel import registry

MAX_MEDIA_BYTES = 64 * 1024 * 1024
_encoders = asyncio.Semaphore(2)


async def ensure_table(conn):
    await conn.execute("""
        create table if not exists orion_chat_media (
            chat_id text not null references orion_chats(id) on delete cascade,
            id text not null,
            mime_type text not null,
            data bytea not null,
            created_at timestamptz not null default now(),
            primary key(chat_id, id)
        )
    """)


def media_path(chat_id, media_id):
    return f"/api/v1/chats/{quote(chat_id, safe='')}/media/{media_id}"


def decode_data_url(value):
    header, separator, encoded = value.partition(',')
    if not separator or not header.startswith('data:') or not header.endswith(';base64'):
        raise ValueError('Invalid media data URL')
    if len(encoded) > (MAX_MEDIA_BYTES + 2) // 3 * 4:
        raise ValueError('Media exceeds 64 MiB')
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > MAX_MEDIA_BYTES:
        raise ValueError('Empty or oversized media')
    return header[5:].split(';', 1)[0].lower(), raw


async def _connection():
    conn = await registry._connect()
    if conn is None:
        raise RuntimeError('Media database unavailable')
    try:
        await registry._ensure_tables(conn)
        await ensure_table(conn)
        return conn
    except BaseException:
        await conn.close()
        raise


async def _put(conn, chat_id, mime, raw):
    media_id = hashlib.sha256(raw).hexdigest()
    await conn.execute("""
        insert into orion_chat_media(chat_id,id,mime_type,data) values($1,$2,$3,$4)
        on conflict(chat_id,id) do nothing
    """, chat_id, media_id, mime, raw)
    stored = await conn.fetchrow('select mime_type, octet_length(data) as size from orion_chat_media where chat_id=$1 and id=$2',
                                 chat_id, media_id)
    return dict(media_id=media_id, media_path=media_path(chat_id, media_id),
                mime_type=stored['mime_type'], size=stored['size'])


async def store_attachments(chat_id, user_id, items):
    """Commit every attachment before admission to Redis. Fail without losing input."""
    conn = await _connection()
    try:
        async with conn.transaction():
            # Serialize with chat deletion; references cannot point at a deleted chat.
            owner = await conn.fetchval('select user_id from orion_chats where id=$1 for update', chat_id)
            if owner != user_id:
                raise ValueError('Chat unavailable for media storage')
            result = []
            for item in items:
                item = dict(item)
                if item.get('media_id'):
                    row = await conn.fetchrow(
                        'select mime_type, octet_length(data) as size from orion_chat_media where chat_id=$1 and id=$2',
                        chat_id, item['media_id'])
                    if row is None:
                        raise ValueError('Media reference does not belong to this chat')
                    item.update(mime_type=row['mime_type'], size=row['size'],
                                media_path=media_path(chat_id, item['media_id']))
                    item.pop('data', None)
                elif not item.get('isText') and item.get('data', '').startswith('data:'):
                    mime, raw = decode_data_url(item['data'])
                    item.update(await _put(conn, chat_id, mime, raw))
                    item.pop('data', None)
                result.append(item)
            return result
    finally:
        await conn.close()


async def read_media(chat_id, media_id):
    conn = await _connection()
    try:
        row = await conn.fetchrow(
            'select mime_type,data from orion_chat_media where chat_id=$1 and id=$2', chat_id, media_id)
        if row is None:
            raise LookupError('Media not found')
        return row['mime_type'], bytes(row['data'])
    finally:
        await conn.close()


async def hydrate_attachments(chat_id, items):
    result = []
    for item in items:
        item = dict(item)
        if item.get('media_id'):
            mime, raw = await read_media(chat_id, item['media_id'])
            item['data'] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            item['mime_type'] = mime
        result.append(item)
    return result


async def hydrate_model_media(chat_id, messages):
    """Resolve only media selected by the model's context boundary."""
    messages = copy.deepcopy(messages)
    for message in messages:
        if isinstance(message.get('content'), list):
            parts = []
            for part in message['content']:
                if part.get('type') == 'hub_media':
                    from orion.worker.services.attachments import media_part
                    item = (await hydrate_attachments(chat_id, [part['attachment']]))[0]
                    part = media_part(item['data'])
                parts.append(part)
            message['content'] = parts
    return messages


def _opus(raw):
    """Encode the history copy; live PCM remains untouched. Fully decode to verify."""
    import av
    output = io.BytesIO()
    source_seconds = 0
    with av.open(io.BytesIO(raw), options={'protocol_whitelist': 'pipe'}) as source:
        with av.open(output, 'w', format='ogg') as destination:
            stream = destination.add_stream('libopus', rate=48000)
            stream.bit_rate = 32000
            stream.layout = 'mono'
            resampler = av.AudioResampler(format='s16', layout='mono', rate=48000)
            def write(frame):
                frame.pts = None
                for packet in stream.encode(frame):
                    destination.mux(packet)
            for frame in source.decode(audio=0):
                source_seconds += frame.samples / frame.sample_rate
                for converted in resampler.resample(frame):
                    write(converted)
            for frame in resampler.resample(None):
                write(frame)
            for packet in stream.encode(None):
                destination.mux(packet)
    encoded = output.getvalue()
    if not encoded or len(encoded) > MAX_MEDIA_BYTES:
        raise ValueError('Invalid Opus recording size')
    with av.open(io.BytesIO(encoded)) as check:
        seconds = sum(frame.samples / frame.sample_rate for frame in check.decode(audio=0))
    if not seconds or abs(seconds - source_seconds) > 0.1:
        raise ValueError('Incomplete Opus recording')
    return encoded


async def store_audio(chat_id, audio):
    import logging
    raw = base64.b64decode(audio['audio'], validate=True)
    if not raw or len(raw) > MAX_MEDIA_BYTES:
        raise ValueError('Invalid audio recording size')
    fmt = audio.get('format', 'wav')
    mime = 'audio/mpeg' if fmt == 'mp3' else f'audio/{fmt}'
    if fmt == 'wav':
        try:
            async with _encoders:
                raw = await asyncio.to_thread(_opus, raw)
            fmt, mime = 'ogg', 'audio/ogg'
        except Exception:
            # Keep a durable original if compression is unavailable.
            logging.exception('TTS Opus conversion failed; storing original binary')
    conn = await _connection()
    try:
        async with conn.transaction():
            if not await conn.fetchval('select id from orion_chats where id=$1 for update', chat_id):
                raise LookupError('Chat deleted before recording was saved')
            ref = await _put(conn, chat_id, mime, raw)
        result = {k: v for k, v in audio.items() if k != 'audio'}
        result.update(ref, format=fmt)
        if fmt == 'ogg' and audio.get('format', 'wav') == 'wav':
            result['source_sample_rate'] = audio.get('sample_rate')
            result['sample_rate'] = 48000
        return result
    finally:
        await conn.close()
