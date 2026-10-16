"""Stage 00 fixtures only. Never starts, signals or discovers OS services."""
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from unittest.mock import patch
import ipaddress


@dataclass(frozen=True)
class FakeProcess:
    pid: int
    started: int
    executable: Path
    entrypoint: Path
    data: Path
    installation_id: str | None
    port: int
    # Descriptors are not OS process handles and must never be signalled.


def snapshot(root):
    """Include names as well as bytes, detecting deletion, addition and mutation."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class LifecycleLab:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.installations = []
        self.processes = {}
        for index, name in enumerate(('Kurulum A', 'İkinci kurulum ş')):
            base = self.root / name
            hub, router = base / 'OrionAIAssistant', base / 'Özel Router'
            install_id = str(uuid.uuid4())
            for path, content in {
                hub / 'services/hub/pyproject.toml': '[project]\nname="orion-hub"\n',
                hub / 'services/.env.global': 'ORION_INSTALL_MODE=local\n',
                hub / 'services/.env.global.local': 'HUB_PORT=18080\n',
                hub / '.local_db/postgres/data/PG_VERSION': '16\n',
                hub / '.local_db/postgres/data/SYNTHETIC': 'Not a PostgreSQL cluster\n',
                hub / '.local_db/redis/dump.rdb': 'synthetic redis sentinel\n',
                hub / 'services/hub/hub.pid': str(810100 + index),
                hub / 'services/hub/data/browser-profiles/profile/Preferences': '{"synthetic":true}',
                hub / 'services/llm/llama-cpp/models/sample.gguf': 'synthetic model, not loadable\n',
                hub / 'services/llm/llama-cpp/models/incomplete.gguf.part': 'partial\n',
                router / 'pyproject.toml': '[project]\nname="orion-router"\n',
                router / '.env': 'ORION_ROUTER_TLS_PORT=19443\n',
                router / '.pgdata-prod/PG_VERSION': '16\n',
                router / '.pgdata-prod/SYNTHETIC': 'Not a PostgreSQL cluster\n',
                router / 'persistent/encryption.key': 'synthetic encryption sentinel\n',
                router / 'persistent/mdns-id': str(uuid.uuid4()),
                router / '.orion.pid': str(810200 + index),
                router / '.orion.prod.lock': 'synthetic stale lock\n',
            }.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
            security = hub / 'files/security'
            security.mkdir(parents=True)
            self._identity(security, 'hub')
            self._identity(router / 'persistent/tls', 'router')
            # Stand-in payload DB, not a production schema or PostgreSQL backup.
            with closing(sqlite3.connect(hub / 'services/hub/data/synthetic.sqlite3')) as db:
                with db:
                    db.execute('create table samples (kind text, value text)')
                    db.executemany('insert into samples values (?, ?)', [
                        ('account', 'fake-user'), ('settings', '{"autoupdate":false}'),
                        ('history', 'synthetic conversation'), ('draft', 'unsent draft')])
            profile = base / 'user-profile'
            locator = profile / 'Orion/router-installation.json'
            locator.parent.mkdir(parents=True)
            locator.write_text(json.dumps({
                'v': 1, 'kind': 'orion-router-local-installation', 'root': str(router),
                'certificate_path': str(router / 'persistent/tls/certificate.pem'),
                'identity_path': str(router / 'persistent/mdns-id'), 'tls_port': 19443,
            }), encoding='utf-8')
            (profile / 'Orion/hub-installation.json').write_text(json.dumps({
                'v': 1, 'kind': 'orion-hub-local-installation', 'root': str(hub),
                'id': (security / 'hub-id').read_text(encoding='ascii'), 'name': 'Synthetic Hub',
                'certificate_path': str(security / 'hub-cert.pem'),
                'local_token_path': str(security / 'native-bootstrap-token'), 'mobile_port': 18443,
            }), encoding='utf-8')
            (security / 'native-bootstrap-token').write_text('synthetic-test-only-proof-' * 2, encoding='ascii')
            self.installations.append(dict(root=base, hub=hub, router=router,
                                           profile=profile, id=install_id))
            pid = 810000 + index  # Synthetic only, never sent to an OS API.
            self.processes[pid] = FakeProcess(pid, index + 100, hub / '.local_db/postgres/bin/postgres.exe',
                                             hub / 'manager.py', hub / '.local_db/postgres/data', install_id, 15432 + index)
        foreign = self.root / 'Yabancı PostgreSQL'
        foreign.mkdir()
        (foreign / 'sentinel').write_text('foreign data must survive', encoding='utf-8')
        self.processes[820000] = FakeProcess(820000, 99, foreign / 'postgres.exe',
                                           foreign / 'postgres.exe', foreign, None, 15432)
        self.foreign = foreign

    @staticmethod
    def _identity(root, prefix):
        # Fresh test-only self-signed identity; never copies a user's key/cert.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta, timezone
        root.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Orion synthetic fixture')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        (root / ('hub-key.pem' if prefix == 'hub' else 'private-key.pem')).write_bytes(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (root / ('hub-cert.pem' if prefix == 'hub' else 'certificate.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        if prefix == 'hub':
            (root / 'hub-id').write_text(str(uuid.uuid4()), encoding='ascii')

    def reused_pid(self, pid):
        return replace(self.processes[820000], pid=pid, started=999)

    def environment(self, index=0):
        install = self.installations[index]
        return {
            'LOCALAPPDATA': str(install['profile']), 'APPDATA': str(install['profile'] / 'Roaming'),
            'XDG_CONFIG_HOME': str(install['profile']),
            'ORION_SECURITY_DIR': str(install['hub'] / 'files/security'),
            'ORION_MDNS_ID_FILE': str(install['hub'] / 'services/hub/data/mdns-id'),
            'ORION_ROUTER_CONNECTION_FILE': str(install['hub'] / 'services/hub/data/router-connection.json'),
        }


@contextmanager
def temporary_lab():
    with tempfile.TemporaryDirectory(prefix='orion-lifecycle-') as directory:
        lab = LifecycleLab(directory)
        with patch.dict(os.environ, lab.environment()):
            yield lab
