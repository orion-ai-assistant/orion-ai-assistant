"""Integration-only supervisor. Creates no Orion services or user data."""
from pathlib import Path
import sys
import time

from services.shared.process_lifecycle import Runtime


def main():
    root = Path(sys.argv[1])
    component = sys.argv[2]
    with Runtime(root, component) as runtime:
        while not runtime.requested():
            runtime.launch('first', [sys.executable, str(root / 'child.py')], python=True)
            if (root / 'second.enabled').exists():
                runtime.launch('second', [sys.executable, str(root / 'child.py')], python=True)
            runtime.refresh_children()
            runtime.ready()
            time.sleep(.1)


if __name__ == '__main__':
    main()
