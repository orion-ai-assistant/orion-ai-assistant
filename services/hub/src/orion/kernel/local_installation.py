"""Filesystem provenance for a same-user native desktop's first connection."""
import json
import os
import secrets
from pathlib import Path

from orion.kernel.local_security import atomic_write, security_dir, tls_identity


def installation_manifest_path():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion' / 'hub-installation.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion' / 'hub-installation.json'


def local_token_path():
    return security_dir() / 'native-bootstrap-token'


def register_native_hub(root, port):
    # Called by the user's native launcher, never by LAN discovery or API probes.
    root = Path(root).resolve(strict=True)
    identity = tls_identity()
    certificate = Path(identity['cert']).resolve()
    proof = local_token_path().resolve()
    # A packaged Hub keeps its identity in its own data folder (the code folder is replaced by updates);
    # a source checkout keeps it inside the checkout. Either way it never leaves the installation.
    from services.shared.runtime_layout import component, data_root
    try:
        data = data_root(root).resolve() if component(root) else root
    except RuntimeError as error:  # An unregistered package: no desktop registration, never a failed start.
        raise ValueError('Installation data folder unavailable') from error
    if not certificate.is_relative_to(data) or not proof.is_relative_to(data):
        raise ValueError('Native bootstrap identity must stay inside the installation')
    if not proof.exists():
        atomic_write(proof, secrets.token_urlsafe(48).encode('ascii'))
    token = proof.read_text(encoding='ascii').strip()
    if not 32 <= len(token) <= 128:
        raise ValueError('Invalid saved native bootstrap proof')
    record = {'v': 1, 'kind': 'orion-hub-local-installation', 'root': str(root),
              'id': identity['id'], 'name': identity['name'], 'certificate_path': str(certificate),
              'local_token_path': str(proof), 'mobile_port': int(port)}
    if data != root:
        record['data_root'] = str(data)
    target = installation_manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps(record).encode('utf-8'))
    return record
