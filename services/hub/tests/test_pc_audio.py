import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from orion.api.pc_audio import DuckLease


class FakeMixer:
    def __init__(self, **levels):
        self.sessions = {sid: {"app": f"{sid}.exe", "level": level, "muted": False}
                         for sid, level in levels.items()}
        self.writes = []

    def __call__(self, levels=None):
        if levels:
            self.writes.append(dict(levels))
            for sid, level in levels.items():
                if sid in self.sessions:
                    self.sessions[sid]["level"] = level
        return {sid: dict(s) for sid, s in self.sessions.items()}


class DuckLeaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Path(self.folder.name) / "duck.json"

    def tearDown(self):
        self.folder.cleanup()

    def lease(self, mixer, **options):
        return DuckLease(mixer, level=0.15, poll=0.01, store=self.store, **options)

    async def test_ducks_each_app_and_restores_and_wrong_token_ignored(self):
        mixer = FakeMixer(brave=0.12, chrome=1.0)
        mixer.sessions["muted"] = {"app": "muted.exe", "level": 0.8, "muted": True}
        lease = self.lease(mixer)
        token = await lease.acquire("user")
        self.assertAlmostEqual(mixer.sessions["brave"]["level"], 0.018)
        self.assertAlmostEqual(mixer.sessions["chrome"]["level"], 0.15)
        self.assertEqual(mixer.sessions["muted"]["level"], 0.8)
        self.assertTrue(self.store.exists())
        self.assertFalse(await lease.release("other", token))
        with self.assertRaises(ValueError):
            await lease.acquire("user")
        self.assertTrue(await lease.release("user", token))
        self.assertAlmostEqual(mixer.sessions["brave"]["level"], 0.12)
        self.assertAlmostEqual(mixer.sessions["chrome"]["level"], 1.0)
        self.assertFalse(self.store.exists())
        self.assertIsNone(lease.token)

    async def test_user_change_during_duck_is_kept(self):
        mixer = FakeMixer(spotify=0.6)
        lease = self.lease(mixer)
        token = await lease.acquire("user")
        mixer.sessions["spotify"]["level"] = 0.4
        await lease.release("user", token)
        self.assertEqual(mixer.sessions["spotify"]["level"], 0.4)

    async def test_app_started_during_lease_is_ducked_and_restored(self):
        mixer = FakeMixer(chrome=1.0)
        lease = self.lease(mixer)
        token = await lease.acquire("user")
        mixer.sessions["spotify"] = {"app": "spotify.exe", "level": 0.5, "muted": False}
        await asyncio.sleep(0.05)
        self.assertAlmostEqual(mixer.sessions["spotify"]["level"], 0.075)
        await lease.release("user", token)
        self.assertAlmostEqual(mixer.sessions["spotify"]["level"], 0.5)

    async def test_expiry_restores_after_disconnect(self):
        mixer = FakeMixer(chrome=1.0)
        lease = self.lease(mixer, ttl=0.02)
        await lease.acquire("user")
        await asyncio.sleep(0.08)
        self.assertAlmostEqual(mixer.sessions["chrome"]["level"], 1.0)
        self.assertIsNone(lease.token)

    async def test_renew_requires_ownership_and_shutdown_restores(self):
        mixer = FakeMixer(chrome=0.7)
        lease = self.lease(mixer)
        token = await lease.acquire("user")
        with self.assertRaises(ValueError):
            await lease.renew("user", "wrong")
        await lease.renew("user", token)
        await lease.close()
        self.assertAlmostEqual(mixer.sessions["chrome"]["level"], 0.7)

    async def test_recover_restores_levels_left_by_a_crash(self):
        self.store.write_text(json.dumps({
            "old-instance": {"app": "spotify.exe", "level": 0.5, "target": 0.075},
            "chrome": {"app": "chrome.exe", "level": 1.0, "target": 0.15},
        }), encoding="utf-8")
        # Spotify restarted after the crash under a new instance; Chrome is still running.
        mixer = FakeMixer(chrome=0.15)
        mixer.sessions["new-instance"] = {"app": "spotify.exe", "level": 0.075, "muted": False}
        await self.lease(mixer).recover()
        self.assertAlmostEqual(mixer.sessions["chrome"]["level"], 1.0)
        self.assertAlmostEqual(mixer.sessions["new-instance"]["level"], 0.5)
        self.assertFalse(self.store.exists())


if __name__ == "__main__":
    unittest.main()
