"""Durable single-use invitations; account creation and redemption are one transaction."""
import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from orion.kernel.registry import _connect, _ensure_tables


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def connection():
    conn = await _connect()
    if conn is None:
        raise HTTPException(503, 'Kullanıcı veritabanına ulaşılamıyor.')
    return conn


async def create_invitation(identifier, token, label, expires_at):
    conn = await connection()
    try:
        await _ensure_tables(conn)
        await conn.execute('insert into orion_invitations (id,token_hash,label,expires_at) values ($1,$2,$3,$4)',
                           identifier, token_hash(token), label, expires_at)
    finally:
        await conn.close()


async def preview_invitation(token):
    conn = await connection()
    try:
        await _ensure_tables(conn)
        row = await conn.fetchrow('select label,expires_at from orion_invitations where token_hash=$1 and used_by is null and not revoked and expires_at>now()', token_hash(token))
        if row is None:
            raise HTTPException(410, 'Bu davet kullanılmış, iptal edilmiş veya süresi dolmuş.')
        return dict(row)
    finally:
        await conn.close()


async def redeem_invitation(token, username, password_hash):
    conn = await connection()
    try:
        await _ensure_tables(conn)
        async with conn.transaction():
            row = await conn.fetchrow('select id,expires_at,used_by,revoked from orion_invitations where token_hash=$1 for update', token_hash(token))
            if (row is None or row['used_by'] or row['revoked'] or row['expires_at'] <= datetime.now(timezone.utc)):
                raise HTTPException(410, 'Bu davet kullanılmış, iptal edilmiş veya süresi dolmuş.')
            result = await conn.execute('insert into orion_users (id,password_hash,is_active) values ($1,$2,true) on conflict (id) do nothing', username, password_hash)
            if result != 'INSERT 0 1':
                raise HTTPException(409, 'Bu kullanıcı adı kullanılıyor. Başka bir ad seçin.')
            await conn.execute('update orion_invitations set used_by=$2 where id=$1', row['id'], username)
    finally:
        await conn.close()


async def list_people():
    conn = await connection()
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch('select id,created_at,is_active from orion_users order by created_at desc')
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def list_invitations():
    conn = await connection()
    try:
        await _ensure_tables(conn)
        rows = await conn.fetch('select id,label,expires_at,created_at,used_by,revoked from orion_invitations order by created_at desc limit 100')
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def revoke_invitation(identifier):
    conn = await connection()
    try:
        await _ensure_tables(conn)
        result = await conn.execute('update orion_invitations set revoked=true where id=$1 and used_by is null', identifier)
        if result != 'UPDATE 1':
            raise HTTPException(404, 'Davet bulunamadı veya zaten kullanılmış.')
    finally:
        await conn.close()
