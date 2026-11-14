"""Durable media: one copy of each file, reached through links from what uses it. JSON and Redis carry
references, never blobs.

orion_media holds a file once, keyed by the SHA-256 of its bytes. orion_media_links says who uses it: a chat
today (owner_kind 'chat', owner_id = chat id), a memory later ('memory', memory id). A chat's links go with the
chat (foreign key cascade). When a file's last link is deleted, a trigger deletes the file in the same
transaction, after locking it, so a writer linking the same file at that moment either keeps it alive or
stores it again: never a link to nothing. Writers lock files in id order, as the trigger does.

A user reaches a file only through an owner they hold that links it (today: their chat). Everything else
(another user's chat, a chat that is not there, a file that is not linked from it, a hash nobody stored) is
the same "not found".

orion_chat_media, the per-chat copy older versions kept, is moved over once (media_migration) and left in
place until a later version drops it.
"""
import asyncio
import base64
import copy
import hashlib
import io
from urllib.parse import quote

from orion.kernel import registry

MAX_MEDIA_BYTES = 64 * 1024 * 1024
OWNER_KINDS = ('chat', 'memory')
SCHEMA_LOCK = 51730104  # processes create the media tables one at a time
_encoders = asyncio.Semaphore(2)
_schema_lock = asyncio.Lock()
_schema_ready = False


async def ensure_table(conn):
    await conn.execute('select pg_advisory_lock($1)', SCHEMA_LOCK)
    try:
        await _create_tables(conn)
    finally:
        await conn.execute('select pg_advisory_unlock($1)', SCHEMA_LOCK)


async def _create_tables(conn):
    await conn.execute("""
        create table if not exists orion_media (
            id text primary key check (id ~ '^[0-9a-f]{64}$'),
            mime_type text not null,
            size bigint not null,
            data bytea not null,
            created_at timestamptz not null default now()
        )
    """)
    # Media is already compressed (JPEG, Opus, MP4): storing it uncompressed out of line skips a useless
    # compression attempt and lets a range request read only the slices it needs.
    if await conn.fetchval("""select attstorage <> 'e' from pg_attribute
                              where attrelid = 'orion_media'::regclass and attname = 'data'"""):
        await conn.execute('alter table orion_media alter column data set storage external')
    await conn.execute("""
        create table if not exists orion_media_links (
            media_id text not null references orion_media(id),
            owner_kind text not null check (owner_kind in ('chat', 'memory')),
            owner_id text not null,
            chat_id text generated always as (case when owner_kind = 'chat' then owner_id end) stored
                references orion_chats(id) on delete cascade,
            created_at timestamptz not null default now(),
            last_used_at timestamptz not null default now(),
            primary key (media_id, owner_kind, owner_id)
        )
    """)
    await conn.execute('create index if not exists idx_orion_media_links_owner on orion_media_links(owner_kind, owner_id)')
    await conn.execute('create index if not exists idx_orion_media_links_chat on orion_media_links(chat_id) '
                       'where chat_id is not null')
    await conn.execute("create index if not exists idx_orion_media_links_chat_last_used on orion_media_links(last_used_at) "
                       "where owner_kind = 'chat'")
    await conn.execute("""
        create or replace function orion_media_drop_unlinked() returns trigger language plpgsql as $$
        begin
            -- In id order, like every writer, so two deletions never wait on each other. A writer linking one
            -- of these files holds a lock on it until it commits; waiting here means its link is seen below.
            perform 1 from orion_media where id in (select media_id from gone) order by id for update;
            delete from orion_media m where m.id in (select media_id from gone)
                and not exists (select 1 from orion_media_links l where l.media_id = m.id);
            return null;
        end $$
    """)
    if not await conn.fetchval("""select exists(select 1 from pg_trigger
                                  where tgname = 'orion_media_links_gone' and tgrelid = 'orion_media_links'::regclass)"""):
        await conn.execute("""
            create trigger orion_media_links_gone after delete on orion_media_links
            referencing old table as gone for each statement execute function orion_media_drop_unlinked()
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
    global _schema_ready
    conn = await registry._connect()
    if conn is None:
        raise RuntimeError('Media database unavailable')
    try:
        # Run DDL once per process. Repeating ALTER TABLE on each read would
        # wait on chat row locks before the cleaner could use SKIP LOCKED.
        if not _schema_ready:
            async with _schema_lock:
                if not _schema_ready:
                    await registry._ensure_tables(conn)
                    await ensure_table(conn)
                    # Before the first read or write: media of older versions is reachable from the start.
                    from orion.kernel.media_migration import migrate_chat_media_table
                    await migrate_chat_media_table(conn)
                    _schema_ready = True
        return conn
    except BaseException:
        await conn.close()
        raise


async def prepare():
    """At start: create the tables and move older media over before anything asks for it."""
    conn = await _connection()
    await conn.close()


def _check_owner(owner_kind, owner_id):
    if owner_kind not in OWNER_KINDS or not isinstance(owner_id, str) or not owner_id:
        raise ValueError('Unknown media owner')


async def _hold(conn, media_id, mime, raw):
    """Keep the file for this transaction, storing it if it is new. Call in media id order."""
    # A share lock: the last-link trigger waits for this transaction, so a link added here is seen there.
    if not await conn.fetchval('select true from orion_media where id = $1 for share', media_id):
        # Locks the row also when another writer stored it meanwhile (DO UPDATE ... WHERE false writes nothing).
        await conn.execute("""
            insert into orion_media(id, mime_type, size, data) values($1, $2, $3, $4)
            on conflict (id) do update set id = excluded.id where false
        """, media_id, mime, len(raw), raw)


async def _link(conn, media_id, owner_kind, owner_id):
    await conn.execute("""
        insert into orion_media_links(media_id, owner_kind, owner_id) values($1, $2, $3)
        on conflict (media_id, owner_kind, owner_id) do update set last_used_at = now()
    """, media_id, owner_kind, owner_id)


async def _put_many(conn, owner_kind, owner_id, files):
    """Store {media_id: (mime, raw)} and link each to the owner; returns {media_id: (mime, size)}."""
    for media_id in sorted(files):
        await _hold(conn, media_id, *files[media_id])
    for media_id in sorted(files):
        await _link(conn, media_id, owner_kind, owner_id)
    rows = await conn.fetch('select id, mime_type, size from orion_media where id = any($1::text[])', list(files))
    return {row['id']: (row['mime_type'], row['size']) for row in rows}


async def _put(conn, chat_id, mime, raw):
    media_id = hashlib.sha256(raw).hexdigest()
    stored_mime, size = (await _put_many(conn, 'chat', chat_id, {media_id: (mime, raw)}))[media_id]
    return dict(media_id=media_id, media_path=media_path(chat_id, media_id), mime_type=stored_mime, size=size)


async def link_media(media_id, owner_kind, owner_id, *, conn=None):
    """Link a stored file to another owner (a memory keeps the photo its chat had). LookupError if the file
    is not stored. With `conn`, inside the caller's transaction; the caller checks that the owner is theirs."""
    _check_owner(owner_kind, owner_id)
    own = conn is None
    conn = conn or await _connection()
    try:
        async with conn.transaction():
            if not await conn.fetchval('select true from orion_media where id = $1 for share', media_id):
                raise LookupError('Media not found')
            await _link(conn, media_id, owner_kind, owner_id)
    finally:
        if own:
            await conn.close()


async def unlink_media(media_id, owner_kind, owner_id, *, conn=None):
    """Drop one owner's link; the file goes with its last link. Returns whether a link was removed."""
    _check_owner(owner_kind, owner_id)
    own = conn is None
    conn = conn or await _connection()
    try:
        return await conn.fetchval("""delete from orion_media_links
            where media_id = $1 and owner_kind = $2 and owner_id = $3 returning true""",
            media_id, owner_kind, owner_id) is not None
    finally:
        if own:
            await conn.close()


async def store_attachments(chat_id, user_id, items):
    """Commit every attachment before admission to Redis. Fail without losing input."""
    conn = await _connection()
    try:
        async with conn.transaction():
            # Serialize with chat deletion; references cannot point at a deleted chat.
            owner = await conn.fetchval('select user_id from orion_chats where id=$1 for update', chat_id)
            if owner != user_id:
                raise ValueError('Chat unavailable for media storage')
            items = [dict(item) for item in items]
            files, reused = {}, set()
            for item in items:
                if item.get('media_id'):
                    reused.add(item['media_id'])
                elif not item.get('isText') and item.get('data', '').startswith('data:'):
                    mime, raw = decode_data_url(item['data'])
                    item['media_id'] = hashlib.sha256(raw).hexdigest()
                    files.setdefault(item['media_id'], (mime, raw))
            for media_id in sorted(reused - files.keys()):
                # Locks the link: the cleaner re-reads last_used_at and keeps it.
                if not await conn.fetchval("""update orion_media_links set last_used_at = now()
                        where media_id = $1 and owner_kind = 'chat' and owner_id = $2 returning true""",
                        media_id, chat_id):
                    raise ValueError('Media reference does not belong to this chat')
            stored = await _put_many(conn, 'chat', chat_id, files)
            if reused - files.keys():
                rows = await conn.fetch('select id, mime_type, size from orion_media where id = any($1::text[])',
                                        list(reused - files.keys()))
                stored.update({row['id']: (row['mime_type'], row['size']) for row in rows})
            for item in items:
                if item.get('media_id'):
                    mime, size = stored[item['media_id']]
                    item.update(mime_type=mime, size=size, media_path=media_path(chat_id, item['media_id']))
                    item.pop('data', None)
            return items
    finally:
        await conn.close()


_OWNED = """
    from orion_media m
    join orion_media_links l on l.media_id = m.id and l.owner_kind = 'chat' and l.owner_id = $1
    where m.id = $2 and ($3::text is null or exists(select 1 from orion_chats c where c.id = $1 and c.user_id = $3))
"""


async def read_media_metadata(chat_id, media_id, *, user_id=None):
    """Read size and MIME without transferring the bytea payload to the Hub. With `user_id`, only through that
    user's chat; any other case is the same LookupError."""
    conn = await _connection()
    try:
        row = await conn.fetchrow('select m.mime_type, m.size' + _OWNED, chat_id, media_id, user_id)
        if row is None:
            raise LookupError('Media not found')
        return row['mime_type'], row['size']
    finally:
        await conn.close()


async def read_media(chat_id, media_id, *, offset=0, length=None, user_id=None):
    conn = await _connection()
    try:
        if length is None:
            row = await conn.fetchrow('select m.mime_type, m.data' + _OWNED, chat_id, media_id, user_id)
        else:
            if offset < 0 or length < 0:
                raise ValueError('Invalid media slice')
            row = await conn.fetchrow('select m.mime_type, substring(m.data from $4::int for $5::int) as data' + _OWNED,
                                      chat_id, media_id, user_id, offset + 1, length)
        if row is None:
            raise LookupError('Media not found')
        return row['mime_type'], bytes(row['data'])
    finally:
        await conn.close()


async def cleanup_orphan_media(redis, *, limit=100):
    """Remove old chat links no message of that chat references, retaining cached and in-flight chat data.
    A file goes with its last link; a link from a memory keeps it. Returns how many chat links were removed."""
    from orion.contracts.constants import CHAT_STATE_KEY_PREFIX, ACTIVE_TURN_KEY_PREFIX
    if not 1 <= limit <= 100:
        raise ValueError('Cleanup batch must be between 1 and 100')
    conn = await _connection()
    try:
        async with conn.transaction():
            candidates = await conn.fetch('''
                select l.owner_id as chat_id, l.media_id from orion_media_links l
                where l.owner_kind = 'chat' and l.last_used_at < now() - interval '48 hours'
                  and not exists (select 1 from orion_chat_messages msg
                      where msg.chat_id=l.owner_id and strpos(msg.content_json,l.media_id)>0)
                order by l.last_used_at,l.owner_id,l.media_id limit $1
            ''', limit)
            removed = 0
            allowed = {}
            for candidate in candidates:
                chat_id = candidate['chat_id']
                if chat_id not in allowed:
                    # Admission and TTS persistence take this same chat lock.
                    # Skip busy chats rather than blocking a send or deletion.
                    locked = await conn.fetchval('select id from orion_chats where id=$1 for update skip locked', chat_id)
                    allowed[chat_id] = (bool(locked), bool(locked) and not await redis.exists(
                        CHAT_STATE_KEY_PREFIX + chat_id,
                        ACTIVE_TURN_KEY_PREFIX + chat_id))
                locked, can_delete = allowed[chat_id]
                if not can_delete:
                    if locked:
                        # Cached/in-flight data counts as recent use. Defer it
                        # so hot chats cannot monopolize the oldest-first batch.
                        await conn.execute("""update orion_media_links set last_used_at=now()
                            where media_id=$2 and owner_kind='chat' and owner_id=$1""", chat_id, candidate['media_id'])
                    continue
                deleted = await conn.fetchval('''
                    delete from orion_media_links l where l.media_id=$2 and l.owner_kind='chat' and l.owner_id=$1
                      and l.last_used_at < now() - interval '48 hours'
                      and not exists (select 1 from orion_chat_messages msg
                          where msg.chat_id=l.owner_id and strpos(msg.content_json,l.media_id)>0)
                    returning l.media_id
                ''', chat_id, candidate['media_id'])
                removed += deleted is not None
            return removed
    finally:
        await conn.close()


async def media_cleanup_loop(redis, owner):
    import logging
    from orion.kernel.job_safety import OWNER_KEY
    while True:
        await asyncio.sleep(3600)
        try:
            if await redis.get(OWNER_KEY) != owner:
                return
            removed = await cleanup_orphan_media(redis)
            if removed:
                logging.info('Removed %d orphan chat media links', removed)
        except Exception:
            # A Redis/DB failure must roll back deletion and never stop inference.
            logging.exception('Orphan media cleanup skipped after a storage/cache error')


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
