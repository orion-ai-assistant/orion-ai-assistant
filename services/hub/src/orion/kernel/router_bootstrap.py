"""Installer-provenance bootstrap for same-PC Router trust, never network TOFU."""
import asyncio
import json
import logging
import os
import socket
import ipaddress
from pathlib import Path
import tomllib
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from orion.kernel.local_security import SecurityStore, atomic_write, security_dir, spki_fingerprint

logger = logging.getLogger(__name__)


def installer_manifest_path():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion' / 'router-installation.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion' / 'router-installation.json'


def validate_registration(record):
    if record.get('v') != 1 or record.get('kind') != 'orion-router-local-installation':
        raise ValueError('Unsupported local Router installation manifest')
    raw_root = Path(record['root'])
    if not raw_root.is_absolute():
        raise ValueError('Router installation root must be absolute')
    root = raw_root.resolve(strict=True)
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    if project.get('project', {}).get('name') != 'orion-router':
        raise ValueError('Registered installation is not Orion Router')
    paths = {}
    for field in ('certificate_path', 'identity_path'):
        raw = Path(record[field])
        if not raw.is_absolute():
            raise ValueError('Router public identity paths must be absolute')
        resolved = raw.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Router public identity file escapes its registered installation')
        paths[field] = resolved
    port = int(record.get('tls_port', 9443))
    if not 1 <= port <= 65535:
        raise ValueError('Router TLS port out of range')
    return {**record, 'root': str(root), **{key: str(path) for key, path in paths.items()}, 'tls_port': port}


def register_local_router_installation(root):
    """Called only after the Hub installer installs/starts that local directory."""
    root = Path(root).resolve(strict=True)
    published = installer_manifest_path()
    if published.exists():
        record = validate_registration(json.loads(published.read_text(encoding='utf-8')))
        if Path(record['root']) == root:
            directory = security_dir()
            directory.mkdir(parents=True, exist_ok=True)
            atomic_write(directory / 'local-router-installation.json', json.dumps(record).encode())
            return record
    port = 9443
    for filename in ('.env', '.env.local'):
        path = root / filename
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip().startswith('ORION_ROUTER_TLS_PORT='):
                    port = int(line.split('=', 1)[1].strip().strip('"\''))
    record = validate_registration({'v': 1, 'kind': 'orion-router-local-installation', 'root': str(root),
                                   'certificate_path': str(root / 'persistent' / 'tls' / 'certificate.pem'),
                                   'identity_path': str(root / 'persistent' / 'mdns-id'), 'tls_port': port})
    directory = security_dir()
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write(directory / 'local-router-installation.json', json.dumps(record).encode())
    return record


def read_local_router_candidate():
    # Hub-installer registration and Router's own native-installer registration
    # are explicit filesystem provenance. Never consult PATH or mDNS here.
    for path in (security_dir() / 'local-router-installation.json', installer_manifest_path()):
        if path.exists():
            return validate_registration(json.loads(path.read_text(encoding='utf-8')))
    # Earlier native installers used these fixed per-user owned locations.
    roots = [Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'OrionRouter'] if os.name == 'nt' else [Path.home() / '.orion-router', Path.home() / '.local' / 'share' / 'OrionRouter']
    for root in roots:
        wrapper = root / ('orionrouter.ps1' if os.name == 'nt' else 'orionrouter')
        if wrapper.is_file() and (root / 'pyproject.toml').is_file():
            return register_local_router_installation(root)
    return None


async def bootstrap_local_router():
    try:
        candidate = await asyncio.to_thread(read_local_router_candidate)
        if candidate is None:
            return {'status': 'missing'}
        certificate = x509.load_pem_x509_certificate(Path(candidate['certificate_path']).read_bytes())
        key = certificate.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('Local Router identity must use P-256')
        identity = str(UUID(Path(candidate['identity_path']).read_text(encoding='ascii').strip()))
        from services.shared.router_connection import read_connection
        selected = await asyncio.to_thread(read_connection)
        if selected and selected.get('id') != identity:
            return {'status': 'owner-configured'}
        host = os.environ.get('ROUTER_HOST', 'router').strip().lower()
        if host not in {'router', 'localhost', '127.0.0.1', '::1', socket.gethostname().lower()}:
            local_addresses = {entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None)}
            try:
                local = ipaddress.ip_address(host).is_loopback or host in local_addresses
            except ValueError:
                local = False
            if not local:
                return {'status': 'owner-configured'}
        fp = spki_fingerprint(certificate.public_bytes(serialization.Encoding.DER))
        store = SecurityStore()
        source = candidate['root']
        if not store.bootstrap_allowed(source, identity, fp):
            return {'status': 'owner-configured'}
        from orion.kernel.pinned_router import verify_router
        url = f'https://127.0.0.1:{candidate["tls_port"]}'
        verified = await asyncio.wait_for(verify_router(url, identity, fp), timeout=2)
        accepted = store.bootstrap_router(source, verified, url)
        return {'status': 'trusted' if accepted else 'owner-configured', 'id': identity}
    except FileNotFoundError:
        return {'status': 'pending'}
    except Exception as error:
        logger.info('Local Router bootstrap remains untrusted: %s', error)
        return {'status': 'untrusted', 'reason': str(error)}
