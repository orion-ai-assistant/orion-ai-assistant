import os

# Worker requests carry the signed-in account's Router key; tests that build
# RuntimeSettings directly stand in for it here, never the Router admin password.
os.environ.setdefault('ROUTER_API_KEY', 'sk-orion-test-account')


import pytest


class MemorySessions:
    """Account sessions kept in memory: a test never writes to the Hub's real database (its password is the
    installation's own, so such a write now fails instead of leaving rows behind). test_account_sessions
    replaces this with a fake that also follows each user's password and activation."""

    def __init__(self):
        self.rows = {}

    async def create(self, session_id, user_id, device_id, credential, forget_after=None):
        self.rows[session_id] = {'id': session_id, 'user_id': user_id, 'device_id': device_id, 'revoked_at': None}

    async def get(self, session_id):
        from datetime import datetime, timezone
        from orion.api import auth_routes
        from orion.api.sessions import credential
        row = self.rows.get(session_id)
        if not row:
            return None
        # The test's own users decide activation (deactivating one ends its sessions); unknown users stay active.
        user = await auth_routes.get_user_by_username(row['user_id'])
        return {**row, 'last_seen_at': datetime.now(timezone.utc), 'is_active': user is None or user.get('is_active', True),
                'password_hash': 'test', 'credential': credential('test')}

    async def touch(self, session_id):
        pass

    async def revoke(self, *, session_id=None, user_id=None, device_id=None):
        from datetime import datetime, timezone
        for row in self.rows.values():
            if session_id in (None, row['id']) and user_id in (None, row['user_id']) and device_id in (None, row['device_id']):
                row['revoked_at'] = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def memory_sessions(monkeypatch):
    from orion.kernel import registry
    store = MemorySessions()
    monkeypatch.setattr(registry, 'create_session', store.create)
    monkeypatch.setattr(registry, 'get_session', store.get)
    monkeypatch.setattr(registry, 'touch_session', store.touch)
    monkeypatch.setattr(registry, 'revoke_sessions', store.revoke)
    return store
