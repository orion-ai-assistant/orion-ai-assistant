"""Renaming an account moves everything keyed by its name, or nothing."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException

from orion.api.services import account_rename
from orion.kernel import registry, router_accounts


class FakeRedis:
    def __init__(self, data):
        self.data = dict(data)

    def pipeline(self):
        return FakePipe(self)

    async def exists(self, key):
        return int(key in self.data)

    async def get(self, key):
        value = self.data.get(key)
        return value if isinstance(value, str) else None

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


class FakePipe:
    def __init__(self, redis):
        self.redis, self.calls = redis, []

    def hget(self, key, field):
        self.calls.append(lambda: (self.redis.data.get(key) or {}).get(field))

    def delete(self, key):
        self.calls.append(lambda: self.redis.data.pop(key, None))

    async def execute(self):
        return [call() for call in self.calls]


def digest(name):
    return hashlib.sha256(name.encode()).hexdigest()


class AccountRenameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.browser, self.google, self.security = (self.root / 'browser', self.root / 'google', self.root / 'sec')
        for folder in (self.browser, self.google, self.security):
            folder.mkdir()
        (self.browser / digest('alice')).mkdir()
        (self.google / digest('alice')).mkdir()
        (self.google / (digest('alice') + '.lock')).write_text('')
        self.hub = str(uuid4())
        self.user = {'id': 'alice', 'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc)}
        subject = lambda name: str(uuid5(UUID(self.hub), json.dumps([name, str(self.user['created_at'])])))
        self.old_subject, self.new_subject = subject('alice'), subject('bob')
        router = str(uuid4())
        self.manual = self.security / f'router-access-{router}-{self.old_subject}.manual'
        self.manual.write_bytes(b'secret')
        self.redis = FakeRedis({
            'chat:state:c1': {'status': 'completed'},
            'chat:meta:c1': {'user_id': 'alice'},
            'orion:settings:alice': {'x': '1'},
            'chat:user:index:alice': {'c1': 1},
            'assistant:chat:alice': 'c9',
            'orion:remote-mic:alice:selected': '{"id": "phone"}',
        })
        self.rename_user = AsyncMock()
        self.patches = [
            patch('orion.browser.manager.browser_root', return_value=self.browser),
            patch('orion.worker.tools.google_ai.profile.profile_root', return_value=self.google),
            patch.object(router_accounts, 'security_dir', return_value=self.security),
            patch.object(router_accounts, 'tls_identity', return_value={'id': self.hub}),
            patch.object(router_accounts, 'provision_if_available', AsyncMock()),
            patch.object(registry, 'get_user_chats_db', AsyncMock(return_value=[{'chat_id': 'c1'}])),
            patch.object(registry, 'rename_user', self.rename_user),
        ]
        for started in self.patches:
            started.start()

    async def asyncTearDown(self):
        for started in reversed(self.patches):
            started.stop()

    async def test_everything_keyed_by_the_name_moves(self):
        await account_rename.rename_account(self.redis, self.user, 'bob')
        self.rename_user.assert_awaited_once_with('alice', 'bob')
        self.assertTrue((self.browser / digest('bob')).is_dir())
        self.assertTrue((self.google / digest('bob')).is_dir())
        self.assertTrue((self.google / (digest('bob') + '.lock')).exists())
        self.assertFalse((self.browser / digest('alice')).exists())
        self.assertEqual(self.manual.with_name(self.manual.name.replace(self.old_subject, self.new_subject))
                         .read_bytes(), b'secret')
        for gone in ('chat:meta:c1', 'orion:settings:alice', 'chat:user:index:alice', 'assistant:chat:alice',
                     'orion:remote-mic:alice:selected'):
            self.assertNotIn(gone, self.redis.data)
        self.assertEqual(self.redis.data['orion:remote-mic:bob:selected'], '{"id": "phone"}')

    async def test_a_reply_on_its_way_blocks_it(self):
        self.redis.data['chat:state:c1'] = {'status': 'processing'}
        with self.assertRaises(HTTPException) as caught:
            await account_rename.rename_account(self.redis, self.user, 'bob')
        self.assertEqual(caught.exception.status_code, 409)
        self.rename_user.assert_not_awaited()
        self.assertTrue((self.browser / digest('alice')).is_dir())

    async def test_a_failed_database_step_puts_the_folders_back(self):
        self.rename_user.side_effect = registry.UsernameTaken('bob')
        with self.assertRaises(registry.UsernameTaken):
            await account_rename.rename_account(self.redis, self.user, 'bob')
        self.assertTrue((self.browser / digest('alice')).is_dir())
        self.assertTrue((self.google / (digest('alice') + '.lock')).exists())
        self.assertFalse((self.browser / digest('bob')).exists())
        self.assertTrue(self.manual.exists())
        self.assertIn('orion:settings:alice', self.redis.data)


if __name__ == '__main__':
    unittest.main()
