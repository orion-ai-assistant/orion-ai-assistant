"""Stop accepting work; personal browser processes belong to the user."""
import asyncio
from .manager import browser_root, _connections

async def close_all():
    root = browser_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / '.stopping').touch()
    current = asyncio.get_running_loop()
    for key, connection in list(_connections.items()):
        if key[0] is current:
            await connection.close()
            _connections.pop(key, None)
    return []

if __name__ == '__main__':
    asyncio.run(close_all())
