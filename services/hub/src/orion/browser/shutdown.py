"""Stop accepting work; personal browser processes belong to the user."""
import asyncio
from .manager import browser_root
from .bridge import descriptor_path, SharedConnection

async def close_all():
    root = browser_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / '.stopping').touch()
    if descriptor_path().exists():
        await SharedConnection('').rpc('shutdown')
    return []

if __name__ == '__main__':
    asyncio.run(close_all())
