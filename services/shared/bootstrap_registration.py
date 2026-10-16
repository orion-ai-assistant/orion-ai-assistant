"""Finish an explicit successful source bootstrap; never moves existing data."""
import os
from pathlib import Path
import tomllib
from copy import deepcopy

from .installation_registry import Registry, RegistryError, component_record, new_registry, _atomic_write, validate_record, operation_lock


def register_bootstrap(code_path, *, root, models_path, registry_path):
    code = Path(code_path).resolve(strict=True)
    main_root, models = Path(root), Path(models_path)
    if not main_root.is_absolute() or not models.is_absolute():
        raise RegistryError('Bootstrap paths must be absolute')
    if any('\n' in str(value) or '\r' in str(value) for value in (main_root, models, registry_path)):
        raise RegistryError('Bootstrap paths cannot contain line breaks')
    project = tomllib.loads((code / 'services/hub/pyproject.toml').read_text(encoding='utf-8'))
    if project.get('project', {}).get('name') != 'orion-hub':
        raise RegistryError('Bootstrap target is not Orion Hub')
    store = Registry(registry_path)

    def register(existing):
        if existing is None:
            existing = new_registry(main_root, [component_record(
                'hub', code, ownership='owned', management='local')])
            existing['locations'] = {'models': str(models)}
            return existing
        if Path(existing['root']) != main_root:
            raise RegistryError('Bootstrap cannot change a registered installation root')
        hubs = [item for item in existing['components'] if item['kind'] == 'hub']
        if not hubs:
            existing['components'].append(component_record('hub', code, ownership='owned', management='local'))
            existing.setdefault('locations', {})['models'] = str(models)
            return existing
        if len(hubs) != 1 or Path(hubs[0]['code_path']).resolve() != code:
            raise RegistryError('Bootstrap cannot replace an existing Hub; use explicit registration or migration')
        saved = existing.get('locations', {}).get('models')
        if saved is not None and Path(saved) != models:
            raise RegistryError('Model location changes require controlled migration')
        # Reusing a legacy source preserves its previous per-service models.
        return existing

    with operation_lock(store.path):
        previous = store.read()
        record = validate_record(register(deepcopy(previous)))
        target = code / 'services/.env.global.local'
        before = target.read_bytes() if target.exists() else None
        encoded = _configuration(record, store, before)
        # Configuration failure must leave canonical inventory unchanged. If a
        # concurrent writer changes inventory, restore only our exact config.
        _atomic_write(target, encoded)
        try:
            def commit(current):
                if current != previous:
                    raise RegistryError('Installation changed during bootstrap; retry required')
                return record
            return store.update(commit)
        except BaseException:
            if target.read_bytes() == encoded:
                if before is None:
                    target.unlink()
                else:
                    _atomic_write(target, before)
            raise


def _configuration(record, store, before):
    values = {'ORION_INSTALL_ROOT': str(record['root']), 'ORION_INSTALLATION_REGISTRY': str(store.path.resolve())}
    if 'models' in record.get('locations', {}):
        values['ORION_MODELS_PATH'] = record['locations']['models']
    if any('\n' in value or '\r' in value for value in values.values()):
        raise RegistryError('Bootstrap paths cannot contain line breaks')
    text = before.decode('utf-8') if before is not None else ''
    lines = [line for line in text.splitlines() if line.partition('=')[0].strip() not in values]
    lines.extend(f'{key}={value}' for key, value in values.items())
    return ('\n'.join(lines) + '\n').encode('utf-8')


def main():
    if os.environ.get('ORION_BOOTSTRAP_REGISTER') != '1':
        raise RegistryError('Explicit bootstrap registration is required')
    register_bootstrap(Path(__file__).resolve().parents[2], root=os.environ['ORION_INSTALL_ROOT'],
                       models_path=os.environ['ORION_MODELS_PATH'],
                       registry_path=os.environ['ORION_INSTALLATION_REGISTRY'])
    if os.name == 'nt' and os.environ.get('ORION_SKIP_GLOBAL_REGISTRATION') != '1':
        from .launcher import install_entry
        store = Registry(os.environ['ORION_INSTALLATION_REGISTRY'])
        with operation_lock(store.path):
            install_entry(store)


if __name__ == '__main__':
    main()
