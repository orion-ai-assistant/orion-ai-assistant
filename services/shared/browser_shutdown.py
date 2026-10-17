"""Stop only Orion-owned browsers before terminating the Hub process tree."""

import logging
import os
from pathlib import Path
import subprocess


def hub_data(hub):
    from .runtime_layout import data_root
    return data_root(Path(hub).resolve().parents[1]) / 'services/hub/data'


def stop_hub_browsers(hub_dir):
    hub = Path(hub_dir).resolve()
    profiles = hub_data(hub) / 'browser-profiles'
    if not profiles.exists():
        return True
    (profiles / '.stopping').touch()
    from .native_services import python_at
    python = Path(python_at(hub))
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
    (hub_data(hub_dir) / 'browser-profiles' / '.stopping').unlink(missing_ok=True)
