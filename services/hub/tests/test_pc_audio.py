import asyncio
import unittest
from orion.api.pc_audio import MuteLease

class MuteLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_state_and_device_restored_and_wrong_token_ignored(self):
        calls = []
        def backend(device=None, muted=None):
            calls.append((device, muted))
            return {"device": "original-endpoint", "muted": True}
        lease = MuteLease(backend)
        token = await lease.acquire("user")
        self.assertFalse(await lease.release("other", token))
        with self.assertRaises(ValueError):
            await lease.acquire("user")
        await lease.release("user", token)
        self.assertEqual(calls, [(None, None), ("original-endpoint", True), ("original-endpoint", True)])
        self.assertIsNone(lease.token)

    async def test_expiry_restores_unmuted_state_after_disconnect(self):
        calls = []
        def backend(device=None, muted=None):
            calls.append((device, muted))
            return {"device": "speaker", "muted": False}
        lease = MuteLease(backend, ttl=0.02)
        await lease.acquire("user")
        await asyncio.sleep(0.08)
        self.assertEqual(calls[-1], ("speaker", False))
        self.assertIsNone(lease.token)

    async def test_renew_requires_ownership_and_shutdown_restores(self):
        calls = []
        def backend(device=None, muted=None):
            calls.append((device, muted))
            return {"device": "speaker", "muted": False}
        lease = MuteLease(backend)
        token = await lease.acquire("user")
        with self.assertRaises(ValueError):
            await lease.renew("user", "wrong")
        await lease.renew("user", token)
        await lease.close()
        self.assertEqual(calls[-1], ("speaker", False))
