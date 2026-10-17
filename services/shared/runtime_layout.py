"""Read-only code/data/state mapping shared with the standalone Router.

Only an explicit registry, matching this exact code path, enables packaged mode.
Source entrypoints retain their existing layout. No registry or directory is made.
"""
import json
import os
from pathlib import Path


def component(root):
    registry = os.getenv('ORION_INSTALLATION_REGISTRY')
    if not registry:
        if (Path(root) / 'orion-release.json').exists():
            raise RuntimeError('Packaged runtime requires an explicit installation registry')
        return None
    try:
        record = json.loads(Path(registry).read_text(encoding='utf-8'))
    except FileNotFoundError:
        if not (Path(root) / 'orion-release.json').exists():
            return None
        raise RuntimeError('Packaged installation registry missing') from None
    if record.get('schema_version') != 1:
        raise RuntimeError('Unsupported runtime registry')
    matches = [c for c in record['components'] if c['distribution'] == 'packaged'
               and c.get('code_path') and Path(c['code_path']).resolve() == Path(root).resolve()]
    if not matches:
        if (Path(root) / 'orion-release.json').exists():
            raise RuntimeError('Package is not registered at this path')
        return None
    if len(matches) != 1:
        raise RuntimeError('Ambiguous runtime mapping')
    selected = matches[0]
    for field in ('data_path', 'state_path'):
        path = Path(selected[field])
        if (not path.is_absolute() or '..' in path.parts or path == Path(root)
                or Path(root) in path.parents or path in Path(root).parents):
            raise RuntimeError('Packaged data/state must be outside the immutable release')
    return selected


def data_root(root):
    selected = component(root)
    return Path(selected['data_path']) if selected else Path(root)


def state_root(root):
    selected = component(root)
    return Path(selected['state_path']) if selected else Path(root) / '.orion-lifecycle'


def python_path(root):
    return Path(root) / 'runtime/python/python.exe'
