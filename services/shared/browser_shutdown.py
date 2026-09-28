"""Stop only Orion-owned browsers before terminating the Hub process tree."""

import logging
import os
from pathlib import Path
import subprocess


def stop_hub_browsers(hub_dir):
    hub = Path(hub_dir).resolve()
    profiles = hub / 'data' / 'browser-profiles'
    if not profiles.exists():
        return True
    (profiles / '.stopping').touch()
    python = hub / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        logging.error('Cannot close Orion browsers: Hub Python is missing')
        return False
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([str(hub / 'src'), str(hub.parents[1])])
    try:
        result = subprocess.run([str(python), '-m', 'orion.browser.shutdown'],
            cwd=str(hub), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            logging.error('Orion browser shutdown failed: %s', result.stderr.decode('utf-8', errors='replace'))
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        logging.exception('Could not complete Orion browser shutdown')
        return False


def allow_hub_browsers(hub_dir):
    (Path(hub_dir) / 'data' / 'browser-profiles' / '.stopping').unlink(missing_ok=True)
