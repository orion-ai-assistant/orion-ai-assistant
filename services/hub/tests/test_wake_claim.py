import unittest

from orion.api.access_boundary import public_path
from orion.api.services.assistant_chat import claim_wake


class ExpiringRedis:
    def __init__(self):
        self.now = 0
        self.values = {}

    def _alive(self, key):
        value = self.values.get(key)
        if value and value[1] <= self.now:
            del self.values[key]
            return None
        return value

    async def set(self, key, value, nx=False, px=None):
        if nx and self._alive(key):
            return None
        self.values[key] = (value.encode(), self.now + px)
        return True

    async def get(self, key):
        value = self._alive(key)
        return value[0] if value else None

    async def pexpire(self, key, px):
        if self._alive(key):
            self.values[key] = (self.values[key][0], self.now + px)


class WakeClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_first_device_takes_the_turn_and_the_one_beside_it_steps_back(self):
        redis = ExpiringRedis()
        self.assertEqual(await claim_wake(redis, 'aa', 'phone'), (True, 'phone'))
        redis.now = 400
        self.assertEqual(await claim_wake(redis, 'aa', 'pc'), (False, 'phone'))
        self.assertEqual(await claim_wake(redis, 'aa', 'phone'), (True, 'phone'))  # its own turn goes on

    async def test_after_the_window_any_device_may_take_the_next_turn(self):
        redis = ExpiringRedis()
        await claim_wake(redis, 'aa', 'phone')
        redis.now = 2600
        self.assertEqual(await claim_wake(redis, 'aa', 'pc'), (True, 'pc'))

    async def test_users_do_not_block_each_other(self):
        redis = ExpiringRedis()
        await claim_wake(redis, 'aa', 'phone')
        self.assertEqual(await claim_wake(redis, 'bb', 'pc'), (True, 'pc'))

    def test_the_phone_reaches_it_over_the_network(self):
        self.assertTrue(public_path('/api/v1/assistant/wake'))


if __name__ == '__main__':
    unittest.main()
