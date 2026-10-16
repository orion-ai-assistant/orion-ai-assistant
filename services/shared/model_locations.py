"""Native model paths scoped to the executing Hub, preserving legacy layouts."""
import os
from pathlib import Path
import re

from .installation_registry import Registry, RegistryError


def models_directory(service_id, service_dir, manifest, *, mode='docker', settings=None):
    legacy = Path(service_dir) / manifest.get('models_path', 'models')
    if mode != 'local':
        return legacy
    if not isinstance(service_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', service_id):
        raise RegistryError('Invalid model service identity')
    settings = settings or {}
    inventory = Registry(settings.get('ORION_INSTALLATION_REGISTRY')).read()
    base = None
    if inventory is not None:
        matches = [item for item in inventory['components'] if item['kind'] == 'hub'
                   and item['distribution'] in {'legacy-source', 'packaged'}
                   and Path(service_dir).resolve().is_relative_to(Path(item['code_path']).resolve())]
        if len(matches) > 1:
            raise RegistryError('Multiple Hub inventories claim the same service path')
        if matches:
            # Legacy imports do not relocate models merely by being registered.
            base = inventory.get('locations', {}).get('models')
        else:
            # A development tree and the installed Hub may coexist. Global
            # inventory/env must never redirect one tree into the other's data.
            return legacy
    else:
        base = settings.get('ORION_MODELS_PATH') or os.environ.get('ORION_MODELS_PATH')
    if base is None:
        return legacy
    path = Path(base)
    if not path.is_absolute():
        raise RegistryError('Native model root must be absolute')
    return path / service_id
