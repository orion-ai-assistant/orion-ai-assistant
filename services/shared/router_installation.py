"""Read the Router native installer's public per-user provenance manifest."""
import json
import os
from pathlib import Path
import tomllib


def registered_router_root():
    path = (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion' / 'router-installation.json') if os.name == 'nt' else (Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion' / 'router-installation.json')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        root = Path(record['root'])
        if record.get('v') != 1 or record.get('kind') != 'orion-router-local-installation' or not root.is_absolute():
            return None
        root = root.resolve(strict=True)
        project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        if project.get('project', {}).get('name') != 'orion-router':
            return None
        return str(root)
    except (OSError, ValueError, KeyError, TypeError):
        return None
