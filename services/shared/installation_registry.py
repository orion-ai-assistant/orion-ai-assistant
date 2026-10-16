"""Versioned, public installation registry. Paths are descriptions, never authority.

Reading does not probe disks, launch code, recover corrupt records, or create roots.
The previous valid record is retained as ``install.json.bak``. Repair must be an
explicit action; a broken primary is never silently replaced by an empty install.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import tomllib
import uuid

SCHEMA_VERSION = 1
DISTRIBUTIONS = {'legacy-source', 'packaged', 'development', 'docker', 'remote'}
PREFERENCES = {'automatic_updates': True, 'start_with_windows': False, 'show_client': True}


class RegistryError(ValueError):
    """Registry cannot be trusted; preserve the record and request repair."""


def default_registry_path():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion'
    return base / 'install.json'


def normalized_path(value):
    """Normalize an absolute native path without requiring the disk to exist."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise RegistryError('Path must be an absolute string')
    path = Path(value)
    if not path.is_absolute():
        raise RegistryError('Relative paths are not permitted')
    # Do not resolve links: resolution could rewrite the recorded target when a
    # disk is absent, or conflate a user-selected path with management authority.
    return os.path.normpath(value)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise RegistryError('Identity must be a canonical UUID') from exc


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise RegistryError('Invalid registry fields (unknown fields and secrets are not accepted)')


def _validate_record(record):
    """Return an independent validated record; unknown schema/fields fail closed."""
    _keys(record, {'schema_version', 'installation_id', 'root', 'components', 'preferences'})
    if type(record['schema_version']) is not int or record['schema_version'] != SCHEMA_VERSION:
        raise RegistryError('Unsupported installation registry schema')
    _uuid(record['installation_id'])
    if normalized_path(record['root']) != record['root']:
        raise RegistryError('Root path must be normalized')
    _keys(record['preferences'], PREFERENCES)
    if any(type(value) is not bool for value in record['preferences'].values()):
        raise RegistryError('Preferences must be booleans')
    if not isinstance(record['components'], list):
        raise RegistryError('Components must be a list')
    ids = set()
    managed_paths = set()
    for component in record['components']:
        _keys(component, {'component_id', 'kind', 'distribution', 'code_path', 'data_path', 'state_path', 'ownership', 'management'}, {'router_uuid', 'tls_spki_sha256', 'identity_path', 'certificate_path', 'tls_port', 'lifecycle_ids'})
        _uuid(component['component_id'])
        if component['component_id'] in ids:
            raise RegistryError('Duplicate component identity')
        ids.add(component['component_id'])
        if component['kind'] not in {'hub', 'router', 'client'} or component['distribution'] not in DISTRIBUTIONS:
            raise RegistryError('Unsupported component kind or distribution')
        if component['ownership'] not in {'owned', 'external'} or component['management'] not in {'local', 'connect-only'}:
            raise RegistryError('Unsupported ownership or management relationship')
        remote = component['distribution'] == 'remote'
        if (remote or component['distribution'] == 'docker') and component['management'] != 'connect-only':
            raise RegistryError('Remote/container components cannot grant local management')
        for field in ('code_path', 'data_path', 'state_path'):
            value = component[field]
            if remote:
                if value is not None:
                    raise RegistryError('Remote components cannot claim local paths')
            elif normalized_path(value) != value:
                raise RegistryError('Component paths must be normalized')
        if component['management'] == 'local':
            for field in ('data_path', 'state_path'):
                identity = os.path.normcase(component[field])
                if identity in managed_paths:
                    raise RegistryError('Managed components cannot share data or state paths')
                managed_paths.add(identity)
        if 'router_uuid' in component:
            if component['kind'] != 'router':
                raise RegistryError('Router identity belongs only to a Router')
            _uuid(component['router_uuid'])
        if 'tls_spki_sha256' in component:
            fingerprint = component['tls_spki_sha256']
            if component['kind'] not in {'hub', 'router'} or not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
                raise RegistryError('Expected lowercase SHA256 SPKI fingerprint')
        for field in ('identity_path', 'certificate_path'):
            if field in component and (remote or normalized_path(component[field]) != component[field]):
                raise RegistryError('Public identity reference must be a normalized local path')
        if 'tls_port' in component and (type(component['tls_port']) is not int or not 1 <= component['tls_port'] <= 65535):
            raise RegistryError('Invalid TLS port')
        if 'lifecycle_ids' in component:
            identities = component['lifecycle_ids']
            if not isinstance(identities, dict) or identities.keys() - {'hub', 'router-prod', 'router-dev', 'service'}:
                raise RegistryError('Invalid lifecycle identity references')
            for value in identities.values():
                _uuid(value)
    return deepcopy(record)


def validate_record(record):
    try:
        return _validate_record(record)
    except (KeyError, TypeError, AttributeError) as exc:
        raise RegistryError('Invalid registry field type') from exc


def _lock(path):
    # Read-only bootstrap consumers run before third-party dependencies exist.
    try:
        from .process_lifecycle import InstanceLock
    except ImportError:
        from process_lifecycle import InstanceLock
    return InstanceLock(path.parent, 'installation-registry-' + path.name)


def component_record(kind, root, *, distribution='legacy-source', component_id=None,
                     data_path=None, state_path=None, ownership='external', management='connect-only', **identity):
    """Construct a local record; discovering it does not grant management rights."""
    root = normalized_path(str(root))
    return dict(component_id=component_id or str(uuid.uuid4()), kind=kind,
                distribution=distribution, code_path=root,
                data_path=normalized_path(str(data_path)) if data_path is not None else root,
                state_path=normalized_path(str(state_path)) if state_path is not None else str(Path(root) / '.orion-lifecycle'),
                ownership=ownership, management=management, **identity)


def new_registry(root, components=(), *, installation_id=None, preferences=None):
    return validate_record(dict(schema_version=SCHEMA_VERSION,
                                installation_id=installation_id or str(uuid.uuid4()),
                                root=normalized_path(str(root)), components=list(components),
                                preferences={**PREFERENCES, **(preferences or {})}))


def import_legacy_installations(hub_manifest=None, router_manifest=None, root=None):
    """Describe explicit legacy manifests in place, without writing any file.

    Caller chooses discovery candidates; this adapter never executes their code or
    imports their modules. Missing manifest arguments mean no such component.
    A present but corrupt manifest/identity raises instead of generating identity.
    Existing phase-01 IDs and public bootstrap identities remain independent.
    """
    components = []
    for kind, manifest in (('hub', hub_manifest), ('router', router_manifest)):
        if manifest is None:
            continue
        try:
            value = json.loads(Path(manifest).read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1 or value.get('kind') != f'orion-{kind}-local-installation':
                raise RegistryError('Invalid legacy manifest')
            source = Path(normalized_path(value['root']))
            metadata = source / ('services/hub/pyproject.toml' if kind == 'hub' else 'pyproject.toml')
            project = tomllib.loads(metadata.read_text(encoding='utf-8'))
            if project.get('project', {}).get('name') != f'orion-{kind}':
                raise RegistryError('Legacy path does not identify the expected product')
            identity = {}
            for field in ('certificate_path', 'identity_path'):
                if field in value:
                    path = Path(normalized_path(value[field]))
                    if not path.resolve().is_relative_to(source.resolve()):
                        raise RegistryError('Legacy public identity escapes its root')
                    identity[field] = str(path)
            if kind == 'router':
                identity_path = Path(identity.get('identity_path', source / 'persistent' / 'mdns-id'))
                component_id = identity_path.read_text(encoding='ascii').strip()
                _uuid(component_id)
                identity['router_uuid'] = component_id
                identity['identity_path'] = str(identity_path)
                if 'tls_port' in value:
                    identity['tls_port'] = value['tls_port']
            else:
                component_id = value['id']
                _uuid(component_id)
                if 'mobile_port' in value:
                    identity['tls_port'] = value['mobile_port']
            if 'certificate_path' in identity:
                # Read public material only. An established local locator is a
                # provenance input; this does not trust a network certificate.
                import hashlib
                from cryptography import x509
                from cryptography.hazmat.primitives import serialization
                from cryptography.x509.oid import NameOID
                certificate = x509.load_pem_x509_certificate(Path(identity['certificate_path']).read_bytes())
                expected_name = f'Orion Router {component_id}' if kind == 'router' else component_id
                names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if not names or names[0].value != expected_name:
                    raise RegistryError('Legacy public certificate identity mismatch')
                identity['tls_spki_sha256'] = hashlib.sha256(certificate.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
            lifecycle_ids = {}
            for component_kind in (('hub',) if kind == 'hub' else ('router-prod', 'router-dev')):
                path = source / '.orion-lifecycle' / component_kind / 'identity.json'
                if path.exists():
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    if saved.get('v') != 1 or saved.get('component') != component_kind or os.path.normcase(saved['root']) != os.path.normcase(str(source.resolve())):
                        raise RegistryError('Legacy lifecycle identity mismatch')
                    _uuid(saved['id'])
                    lifecycle_ids[component_kind] = saved['id']
            if lifecycle_ids:
                identity['lifecycle_ids'] = lifecycle_ids
            components.append(component_record(kind, source, component_id=component_id, **identity))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RegistryError(f'Legacy {kind} manifest cannot be imported; repair required') from exc
    return new_registry(root or default_registry_path().parent, components)


def _decode(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RegistryError('Duplicate JSON field')
            value[key] = item
        return value
    try:
        return validate_record(json.loads(data.decode('utf-8'), object_pairs_hook=unique))
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise RegistryError('Installation registry is invalid; repair required') from exc


def _atomic_write(path, data):
    """Flush a same-directory temporary before replace; keep target on failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Registry:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_registry_path()
        self.backup_path = self.path.with_name(self.path.name + '.bak')

    def _bytes(self, path):
        if path.is_symlink():
            raise RegistryError('Registry cannot be a symlink')
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RegistryError('Installation registry inaccessible; repair required') from exc

    def read(self):
        data = self._bytes(self.path)
        return None if data is None else _decode(data)

    def read_backup(self):
        data = self._bytes(self.backup_path)
        return None if data is None else _decode(data)

    def _write_locked(self, record):
        record = validate_record(record)
        previous = self._bytes(self.path)
        if previous is not None:
            _decode(previous)  # Never overwrite a corrupt or future-schema primary.
            if self.backup_path.is_symlink():
                raise RegistryError('Registry backup cannot be a symlink')
            _atomic_write(self.backup_path, previous)
        encoded = (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        _atomic_write(self.path, encoded)
        return record

    def write(self, record):
        validate_record(record)
        with _lock(self.path):
            return self._write_locked(record)

    def update(self, mutate):
        """Serialize a read/modify/write transaction across processes."""
        with _lock(self.path):
            return self._write_locked(mutate(self.read()))
