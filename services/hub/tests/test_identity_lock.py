"""Threads of one process take turns on the identity lock without msvcrt's one-second retry."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest

from orion.kernel.local_security import identity_lock


class IdentityLockTests(unittest.TestCase):
    def test_threads_take_turns_without_waiting_a_second(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inside = []

            def hold():
                with identity_lock(directory):
                    inside.append(1)
                    time.sleep(0.02)
                    self.assertEqual(len(inside), 1)  # one holder at a time
                    inside.pop()

            started = time.monotonic()
            with ThreadPoolExecutor(3) as pool:
                for future in [pool.submit(hold) for _ in range(3)]:
                    future.result()
            # Three at once took 2 s when each waiting thread retried once a second (2026-10-31).
            self.assertLess(time.monotonic() - started, 0.5)


if __name__ == '__main__':
    unittest.main()
