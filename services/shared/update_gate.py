"""Persistent admission barrier shared by helper, launcher, API and Worker.

Only the helper opens admission by publishing a terminal transaction. Malformed
or missing journals behind an existing pointer keep admission closed.
"""
import asyncio
import json
import os
from pathlib import Path

TERMINAL = {'completed', 'rolled_back', 'failed'}
PREPARING = {'checking', 'downloading', 'verifying', 'staged', 'waiting_for_idle'}


def blocked(registry=None):
    registry = registry or os.getenv('ORION_INSTALLATION_REGISTRY')
    if not registry:
        return False
    pointer = Path(registry).parent / 'updates/active.json'
    try:
        value = json.loads(pointer.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    try:
        from uuid import UUID
        identity = value['operation_id']
        if str(UUID(identity)) != identity or value['schema'] != 1:
            return True
        journal = json.loads((pointer.parent / identity / 'journal.json').read_text(encoding='utf-8'))
        return (journal.get('schema') != 1 or journal['operation_id'] != identity
                or journal['phase'] not in TERMINAL | PREPARING)
    except (OSError, ValueError, KeyError, TypeError):
        return True


async def wait_for_admission():
    while blocked():
        await asyncio.sleep(.2)


def require_start_admission(root=None):
    """Only the fixed health helper may start a quarantined candidate."""
    if root is not None:
        marker = Path(root) / '.orion-converted.json'
        if marker.exists():
            raise RuntimeError('Legacy installation converted; use the registered launcher')
        from .runtime_layout import component
        if component(root) is None:
            return
    if not blocked():
        return
    registry = os.getenv('ORION_INSTALLATION_REGISTRY')
    active = json.loads((Path(registry).parent / 'updates/active.json').read_text(encoding='utf-8'))
    if not os.getenv('ORION_UPDATE_OPERATION') or active.get('operation_id') != os.getenv('ORION_UPDATE_OPERATION'):
        raise RuntimeError('Update/recovery in progress; use the retained helper')


async def admitted(coroutine):
    try:
        await wait_for_admission()
        return await coroutine
    finally:
        # A task can be cancelled before its wrapped coroutine ever starts.
        coroutine.close()


class UpdateAdmission:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'} and blocked() and not (
                scope['type'] == 'http' and scope.get('method') == 'GET'
                and scope.get('path') in {'/health', '/ready', '/api/v1/tls/identity'}):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1013})
            else:
                await send({'type': 'http.response.start', 'status': 503,
                            'headers': [(b'retry-after', b'5'), (b'content-type', b'application/json')]})
                await send({'type': 'http.response.body', 'body': b'{"detail":"Update recovery in progress"}'})
            return
        await self.app(scope, receive, send)
