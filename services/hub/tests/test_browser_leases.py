"""Leftover browser lock files are removed; background helper tasks are held until they end."""
import asyncio
import os
import time
from unittest.mock import patch

from orion.browser import manager


def test_old_leases_are_removed_and_the_current_one_kept(tmp_path):
    with patch.object(manager, 'browser_root', return_value=tmp_path):
        old = tmp_path / 'shared-old.lock'
        recent = tmp_path / 'shared-recent.lock'
        other = tmp_path / 'bridge.lock'
        for path in (old, recent, other):
            path.write_bytes(b'0')
        week = manager.SHARED_LEASE_DAYS * 86400
        os.utime(old, (time.time() - week - 60,) * 2)
        os.utime(other, (time.time() - week - 60,) * 2)
        lease = manager.shared_lease('ws://127.0.0.1:9222/devtools/browser/guid')
        lease.write_bytes(b'0')
        os.utime(lease, (time.time() - week - 60,) * 2)
        assert manager.shared_lease('ws://127.0.0.1:9222/devtools/browser/guid') == lease
        assert not old.exists() and recent.exists() and other.exists()
        assert lease.exists() and lease.stat().st_mtime > time.time() - 60  # In use: kept fresh.


def test_background_tasks_are_held_until_they_end():
    async def run():
        done = asyncio.Event()

        async def work():
            await done.wait()
        task = manager._background(work())
        assert task in manager._tasks
        done.set()
        await task
        await asyncio.sleep(0)
        assert task not in manager._tasks
    asyncio.run(run())
