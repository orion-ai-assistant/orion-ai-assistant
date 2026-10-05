"""Persistent local identities and transactional pairing/trust storage.

Discovery addresses never constitute trust. Only an owner-confirmed SPKI pin does.
"""
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def security_dir():
    configured = os.getenv('ORION_SECURITY_DIR')
    if configured:
        return Path(configured)
    # Source checkouts and installed wheels/container paths have different depth.
    for parent in Path(__file__).resolve().parents:
        if (parent / 'services' / 'hub').is_dir():
            return parent / 'files' / 'security'
    return Path.cwd() / 'files' / 'security'


def spki_fingerprint(certificate_der):
    key = x509.load_der_x509_certificate(certificate_der).public_key()
    return hashlib.sha256(key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


@contextlib.contextmanager
def identity_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'identity.lock').open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path, data):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def tls_identity(directory=None, renew=False):
    directory = Path(directory) if directory else security_dir()
    with identity_lock(directory):
        key_path, cert_path, id_path = directory / 'hub-key.pem', directory / 'hub-cert.pem', directory / 'hub-id'
        if key_path.exists():
            # Corrupt/unsupported keys fail closed. Never silently replace a saved key.
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                raise RuntimeError('Hub identity must use ECDSA P-256')
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            atomic_write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        if not id_path.exists():
            legacy_path = Path(os.getenv('ORION_MDNS_ID_FILE', str(Path(__file__).resolve().parents[3] / 'data' / 'mdns-id')))
            legacy_id = str(uuid.UUID(legacy_path.read_text().strip())) if legacy_path.exists() else str(uuid.uuid4())
            atomic_write(id_path, legacy_id.encode())
        hub_id = str(uuid.UUID(id_path.read_text().strip()))
        name = os.getenv('ORION_HUB_NAME', os.getenv('ORION_MDNS_NAME', socket.gethostname()))
        now = datetime.now(timezone.utc)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes()) if cert_path.exists() else None
        san_names = [x509.DNSName('localhost'), x509.DNSName(hub_id), x509.DNSName(f'orion-{uuid.UUID(hub_id).hex}.local'),
                     x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                     x509.IPAddress(ipaddress.ip_address('::1')),
                     x509.IPAddress(ipaddress.ip_address(local_address()))]
        san = x509.SubjectAlternativeName(san_names)
        try:
            existing_san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value if cert else []
        except x509.ExtensionNotFound:
            existing_san = []
        missing_san = any(name not in existing_san for name in san_names)
        # Shared Docker listeners may observe different interfaces. Preserve all
        # prior SANs while adding the current address; never rotate keys or drop
        # the LAN address when another listener sees a container address.
        san = x509.SubjectAlternativeName(list(existing_san) + [name for name in san_names if name not in existing_san])
        if cert and cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise RuntimeError('Saved certificate and Hub private key disagree')
        if renew or not cert or cert.not_valid_after_utc <= now or missing_san:
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hub_id)])
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                    .not_valid_after(now + timedelta(days=365 * 50))
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(san, critical=False)
                    .sign(key, hashes.SHA256()))
            atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
        fp = spki_fingerprint(cert.public_bytes(serialization.Encoding.DER))
        return {'v': 1, 'id': hub_id, 'name': name, 'fp': fp, 'cert': str(cert_path), 'key': str(key_path)}


class SecurityStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else security_dir() / 'devices.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pairing(code_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, platform TEXT,
                  token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS attempts(peer TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS routers(id TEXT PRIMARY KEY, name TEXT NOT NULL, fp TEXT NOT NULL, url TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS router_bootstrap(source TEXT PRIMARY KEY, id TEXT NOT NULL, fp TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS router_revocations(id TEXT PRIMARY KEY);
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def create_pairing(self, ttl=120):
        code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        expires = time.time() + ttl
        with self.connect() as db:
            db.execute('DELETE FROM pairing WHERE expires <= ?', (time.time(),))
            db.execute('INSERT INTO pairing VALUES (?,?)', (self.digest(code), expires))
        return code, expires

    def claim(self, code, name, platform, peer):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE until <= ?', (now,))
            record = db.execute('SELECT count FROM attempts WHERE peer=?', (peer,)).fetchone()
            if record and record['count'] >= 10:
                raise PermissionError('Too many pairing attempts; retry in 120 seconds')
            global_attempts = db.execute("SELECT count FROM attempts WHERE peer='@global'", ()).fetchone()
            if global_attempts and global_attempts['count'] >= 100:
                raise PermissionError('Too many pairing attempts; retry in 120 seconds')
            db.execute("INSERT INTO attempts VALUES ('@global',1,?) ON CONFLICT(peer) DO UPDATE SET count=count+1", (now + 120,))
            db.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(peer) DO UPDATE SET count=count+1', (peer, now + 120))
            result = db.execute('DELETE FROM pairing WHERE code_hash=? AND expires > ? RETURNING code_hash', (self.digest(code), now)).fetchone()
            if not result:
                db.commit()  # Persist rate limiting, while leaving every valid code intact.
                raise ValueError('Invalid or expired pairing code')
            device_id, token = str(uuid.uuid4()), 'orion_device_' + secrets.token_urlsafe(32)
            db.execute('INSERT INTO devices(id,name,platform,token_hash,created) VALUES (?,?,?,?,?)', (device_id, name, platform, self.digest(token), now))
            return device_id, token

    def authenticate(self, token):
        if not token or len(token) > 256:
            return None
        with self.connect() as db:
            row = db.execute('SELECT id FROM devices WHERE token_hash=? AND revoked=0', (self.digest(token),)).fetchone()
            return row['id'] if row else None

    def active(self, device_id):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM devices WHERE id=? AND revoked=0', (device_id,)).fetchone())

    def devices(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,name,platform,created,revoked FROM devices ORDER BY created DESC')]

    def revoke(self, device_id):
        with self.connect() as db:
            return db.execute('UPDATE devices SET revoked=1 WHERE id=?', (device_id,)).rowcount

    def routers(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM routers')]

    def trust_router(self, identity, url):
        with self.connect() as db:
            existing = db.execute('SELECT fp FROM routers WHERE id=?', (identity['id'],)).fetchone()
            if existing and existing['fp'] != identity['fp']:
                raise ValueError('Router key changed; explicitly remove old trust and pair again')
            db.execute('INSERT OR REPLACE INTO routers VALUES (?,?,?,?)', (identity['id'], identity['name'], identity['fp'], url))
            # This method is an explicit owner grant, unlike automatic bootstrap.
            db.execute('DELETE FROM router_revocations WHERE id=?', (identity['id'],))
            db.execute("UPDATE router_bootstrap SET fp=?,status='approved' WHERE id=?", (identity['fp'], identity['id']))

    def remove_router(self, identity):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM routers WHERE id=?', (identity,))
            db.execute('INSERT OR IGNORE INTO router_revocations VALUES (?)', (identity,))
            db.execute("UPDATE router_bootstrap SET status='revoked' WHERE id=?", (identity,))

    def bootstrap_allowed(self, source, identity, fp):
        with self.connect() as db:
            marker = db.execute('SELECT * FROM router_bootstrap WHERE source=?', (source,)).fetchone()
            if marker and (marker['status'] == 'revoked' or marker['id'] != identity or marker['fp'] != fp):
                raise PermissionError('Local Router trust was revoked or its installation identity changed; owner confirmation required')
            if db.execute('SELECT 1 FROM router_revocations WHERE id=?', (identity,)).fetchone():
                raise PermissionError('Router trust was explicitly removed by the owner')
            existing = db.execute('SELECT fp FROM routers WHERE id=?', (identity,)).fetchone()
            if existing and existing['fp'] != fp:
                raise PermissionError('Local Router public key changed; owner confirmation required')
            return not db.execute('SELECT 1 FROM routers WHERE id != ?', (identity,)).fetchone()

    def bootstrap_router(self, source, identity, url):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            marker = db.execute('SELECT * FROM router_bootstrap WHERE source=?', (source,)).fetchone()
            if marker and (marker['status'] == 'revoked' or marker['id'] != identity['id'] or marker['fp'] != identity['fp']):
                raise PermissionError('Local Router bootstrap identity changed or was revoked')
            if db.execute('SELECT 1 FROM router_revocations WHERE id=?', (identity['id'],)).fetchone():
                raise PermissionError('Router trust was explicitly removed')
            existing = db.execute('SELECT fp FROM routers WHERE id=?', (identity['id'],)).fetchone()
            if existing and existing['fp'] != identity['fp']:
                raise PermissionError('Router public key changed')
            if db.execute('SELECT 1 FROM routers WHERE id != ?', (identity['id'],)).fetchone():
                return False
            db.execute('INSERT OR IGNORE INTO routers VALUES (?,?,?,?)', (identity['id'], identity['name'], identity['fp'], url))
            db.execute("INSERT OR IGNORE INTO router_bootstrap VALUES (?,?,?,'approved')", (source, identity['id'], identity['fp']))
            return True

    def update_router_address(self, identity, fp, url):
        with self.connect() as db:
            # Discovery cannot resurrect trust that an owner removed concurrently.
            db.execute('UPDATE routers SET url=? WHERE id=? AND fp=?', (url, identity, fp))


def local_address():
    configured = os.getenv('ORION_MOBILE_ADVERTISE_HOST')
    if configured:
        ipaddress.ip_address(configured)
        return configured
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('192.0.2.1', 9))
            return probe.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())
