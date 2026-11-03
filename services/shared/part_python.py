"""Run one of Orion's own Python services (a Bileşenler part: STT, TTS) on the packaged Python.

    python -m services.shared.part_python <service script> <engine site folder or "">

The engine's extra packages (Parts/engines/<engine>/site, unpacked wheels) go on the path; then the service
script runs as __main__ from its own folder, the way it runs from source.
"""
from pathlib import Path
import runpy
import sys


def main():
    script, site = Path(sys.argv[1]), sys.argv[2]
    if site:
        sys.path.insert(0, site)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()
