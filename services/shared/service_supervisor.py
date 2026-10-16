"""Supervise one installer-managed Python service; no shell or PID-only stop."""
import os
from pathlib import Path
import sys
import time

from services.shared.process_lifecycle import Runtime, assert_port_free, wait_health


def main():
    root, target, port, python = sys.argv[1:]
    target = Path(target).resolve(strict=True)
    target.relative_to(Path(root).resolve(strict=True))
    with Runtime(root, 'service') as runtime:
        if 'application' not in runtime.children:
            assert_port_free(port)
        child = runtime.launch('application', [python, str(target)], cwd=root, python=True,
                               **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        wait_health(child, port)
        runtime.ready()
        while not runtime.requested() and child.poll() is None:
            runtime.refresh_children()
            time.sleep(.1)


if __name__ == '__main__':
    main()
