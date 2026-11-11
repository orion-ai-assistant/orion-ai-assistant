"""Renaming an account's login name (orion_users.id, the user_id everywhere).

Nothing in the database points at the user row with a foreign key, so every place that holds the name moves
here by hand:

- PostgreSQL, in one transaction: the user row (its created_at stays), settings, chats, the invitation it
  used; all of its sessions end (their tokens carry the old name). The caller gets a new token.
- Per-user folders named by a hash of the name: the managed browser's and the Google AI browser's
  profiles. They move first; a browser that has one open blocks the rename, nothing changes then.
- The Router: the account's subject is derived from the name, so a new Router account is made on the next
  use. The user's own Router key (".manual") moves to the new subject; the old account stays on the Router.
- Redis: caches keyed by the name (settings, chat index, assistant chat) and every chat's cached owner are
  dropped (they rebuild from PostgreSQL); the chosen phone for "PC mikrofonu" moves.

A reply on its way or a remote-microphone turn holds the old name in flight, so the rename waits for them.
"""
import asyncio
import logging
import os
import re
import hashlib
from pathlib import Path

from fastapi import HTTPException
from redis.asyncio import Redis

from orion.contracts.constants import (
    ASSISTANT_CHAT_KEY_PREFIX,
    CHAT_META_KEY_PREFIX,
    CHAT_STATE_KEY_PREFIX,
    CHAT_USER_INDEX_PREFIX,
    SETTINGS_HASH_KEY_PREFIX,
)

logger = logging.getLogger(__name__)

# Letters (Turkish ones too), digits, dot, dash and underscore; no spaces. 2-40 characters.
USERNAME = re.compile(r'^[^\W][\w.\-]{1,39}$', re.UNICODE)
ACTIVE = {'queued', 'processing'}


def valid_username(name: str) -> str:
    name = name.strip()
    if not USERNAME.fullmatch(name):
        raise HTTPException(400, 'Kullanıcı adı 2-40 karakter olmalı; harf, rakam, nokta, tire ve alt çizgi '
                                 'kullanılabilir, boşluk olamaz.')
    if name.casefold() == 'global':
        raise HTTPException(400, 'Bu kullanıcı adı ayrılmış.')
    return name


def _profile_folders(user_id: str) -> list[Path]:
    from orion.browser.manager import browser_root
    from orion.worker.tools.google_ai.profile import profile_root
    digest = hashlib.sha256(user_id.encode('utf-8')).hexdigest()
    return [browser_root() / digest, profile_root() / digest, profile_root() / (digest + '.lock')]


def _move_profiles(old: str, new: str) -> list[tuple[Path, Path]]:
    """Moves what exists; on any failure puts back what moved and raises."""
    moved = []
    try:
        for source, target in zip(_profile_folders(old), _profile_folders(new)):
            if not source.exists():
                continue
            if target.exists():
                raise OSError(f'{target.name} already exists')
            os.replace(source, target)
            moved.append((source, target))
    except OSError as error:
        _undo_moves(moved)
        logger.warning('Account rename: a profile could not move: %s', error)
        raise HTTPException(409, 'Orion’un tarayıcısı açık. Kapatıp tekrar dene.') from error
    return moved


def _undo_moves(moved: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moved):
        try:
            os.replace(target, source)
        except OSError:
            logger.exception('Account rename: could not put back %s', source)


def _move_router_keys(old_subject: str, new_subject: str) -> None:
    from orion.kernel.router_accounts import security_dir
    for path in security_dir().glob(f'router-access-*-{old_subject}.manual'):
        try:
            os.replace(path, path.with_name(path.name.replace(old_subject, new_subject)))
        except OSError:
            logger.exception('Account rename: the Router key %s did not move', path.name)


async def _subject(user: dict) -> str | None:
    try:
        from uuid import UUID, uuid5
        import json
        from orion.kernel.router_accounts import tls_identity
        identity = await asyncio.to_thread(tls_identity)
        return str(uuid5(UUID(identity['id']), json.dumps([user['id'], str(user['created_at'])])))
    except Exception:
        # No Hub identity yet means no Router account either.
        return None


async def rename_account(redis: Redis, user: dict, new: str) -> None:
    from orion.api.remote_mic import selected_key, turn_key
    from orion.kernel import registry

    old = user['id']
    chats = [chat['chat_id'] for chat in await registry.get_user_chats_db(old)]
    if chats:
        pipe = redis.pipeline()
        for chat_id in chats:
            pipe.hget(f'{CHAT_STATE_KEY_PREFIX}{chat_id}', 'status')
        if any(status in ACTIVE for status in await pipe.execute()):
            raise HTTPException(409, 'Bir yanıt hâlâ yazılıyor. Bitince tekrar dene.')
    if await redis.exists(turn_key(old)):
        raise HTTPException(409, 'Telefon mikrofonu şu an konuşmayı dinliyor. Bitince tekrar dene.')

    old_subject = await _subject(user)
    moved = await asyncio.to_thread(_move_profiles, old, new)
    try:
        await registry.rename_user(old, new)
    except BaseException:
        await asyncio.to_thread(_undo_moves, moved)
        raise

    if old_subject:
        new_subject = await _subject({**user, 'id': new})
        if new_subject:
            await asyncio.to_thread(_move_router_keys, old_subject, new_subject)
    try:
        pipe = redis.pipeline()
        for key in (f'{SETTINGS_HASH_KEY_PREFIX}{old}', f'{CHAT_USER_INDEX_PREFIX}{old}',
                    f'{ASSISTANT_CHAT_KEY_PREFIX}{old}'):
            pipe.delete(key)
        for chat_id in chats:
            pipe.delete(f'{CHAT_META_KEY_PREFIX}{chat_id}')
        await pipe.execute()
        selected = await redis.get(selected_key(old))
        if selected:
            await redis.set(selected_key(new), selected)
            await redis.delete(selected_key(old))
    except Exception:
        # Caches expire on their own; a stale chat owner only refuses until then.
        logger.exception('Account rename: Redis caches were not all cleared')
    try:
        from orion.kernel.router_accounts import provision_if_available
        await provision_if_available(new)
    except Exception:
        logger.info('Account rename: Router account is made on first use')
