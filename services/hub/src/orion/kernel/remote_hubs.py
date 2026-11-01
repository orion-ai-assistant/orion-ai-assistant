"""Other Hubs this computer uses like the phone app does: paired by a QR link, pinned to their certificate.

The Orion window cannot pin a certificate, so this Hub pairs with the main Hub and relays the window's
requests (api/relay.py). Only the main Hub's PC adds devices; this side never shows a pairing QR.
"""
import base64
import contextlib
import hmac
import ipaddress
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp

from orion.kernel.local_security import security_dir, spki_fingerprint

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
LINK_HOST = 'orion-ai-assistant.github.io'
TIMEOUT = aiohttp.ClientTimeout(total=15, sock_connect=10)


def parse_link(text):
    """A pairing link from the main Hub's PC: the short web link, its orion:// form or the bare token.

    Returns the Hub's address, the one-time code and the first half of its SPKI fingerprint."""
    text = (text or '').strip()
    token = text
    if text.startswith('orion://'):
        token = (parse_qs(urlsplit(text).query).get('l') or [''])[0]
    elif text.startswith('https://'):
        parts = urlsplit(text)
        if parts.hostname != LINK_HOST or parts.path not in {'/p', '/p.html'}:
            raise ValueError('Bu bir Orion eşleştirme bağlantısı değil.')
        token = parts.fragment
    if not re.fullmatch(r'[A-Za-z0-9_-]{30,80}', token or ''):
        raise ValueError('Eşleştirme bağlantısı eksik veya bozuk.')
    raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
    size = {2: 4, 3: 16}.get(raw[0])
    if size is None or len(raw) != 1 + size + 2 + 5 + 16:
        raise ValueError('Eşleştirme bağlantısı eksik veya bozuk.')
    address = ipaddress.ip_address(raw[1:1 + size])
    if not address.is_private or address.is_loopback:
        raise ValueError('Eşleştirme bağlantısı yerel ağdaki bir Hub’a ait değil.')
    port = int.from_bytes(raw[1 + size:3 + size], 'big')
    number = int.from_bytes(raw[3 + size:8 + size], 'big')
    code = ''
    for _ in range(8):
        code = CODE_ALPHABET[number % 32] + code
        number //= 32
    if number or not port:
        raise ValueError('Eşleştirme bağlantısı eksik veya bozuk.')
    host = f'[{address}]' if address.version == 6 else str(address)
    return {'url': f'https://{host}:{port}', 'code': code, 'fp_prefix': raw[8 + size:].hex()}


class SPKIPrefixPin(aiohttp.Fingerprint):
    """Pins the TLS connection to an SPKI fingerprint, or to its first half from a short link."""
    def __init__(self, fingerprint):
        if not re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{64}', fingerprint or ''):
            raise ValueError('SPKI fingerprint must be lowercase SHA-256 hex')
        super().__init__(bytes(32))
        self.expected = fingerprint
        self.seen = None

    def check(self, transport):
        ssl_object = transport.get_extra_info('ssl_object')
        if ssl_object is None:
            raise aiohttp.ClientConnectionError('Hub connection requires TLS')
        actual = spki_fingerprint(ssl_object.getpeercert(binary_form=True))
        if not hmac.compare_digest(actual[:len(self.expected)], self.expected):
            peer = transport.get_extra_info('peername') or ('unknown', 0)
            raise aiohttp.ServerFingerprintMismatch(bytes.fromhex(self.expected.ljust(64, '0')),
                                                    bytes.fromhex(actual), peer[0], peer[1])
        self.seen = actual


def session(fingerprint, **kwargs):
    # auto_decompress=False: relayed bodies keep their encoding and length.
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SPKIPrefixPin(fingerprint)),
                                 timeout=kwargs.pop('timeout', TIMEOUT), trust_env=False, **kwargs)


def _check_identity(data, prefix):
    fp, hub_id, name = (data.get(key) if isinstance(data, dict) else None for key in ('fp', 'id', 'name'))
    if (not isinstance(fp, str) or not re.fullmatch(r'[0-9a-f]{64}', fp) or not fp.startswith(prefix)
            or not isinstance(hub_id, str) or not isinstance(name, str)):
        raise ValueError('Hub kimliği eşleştirme bağlantısıyla uyuşmuyor.')
    return {'id': str(uuid.UUID(hub_id)), 'name': name[:240], 'fp': fp}


async def resolve(url, prefix):
    """Ask the Hub itself, over a connection pinned to the link's half fingerprint, who it is."""
    async with session(prefix) as client:
        async with client.get(url + '/api/v1/tls/identity') as response:
            response.raise_for_status()
            return _check_identity(await response.json(content_type=None), prefix)


class PairingRejected(Exception):
    pass


async def claim(url, identity, code, device_name):
    async with session(identity['fp']) as client:
        async with client.post(url + '/api/v1/pair/claim',
                               json={'code': code, 'device_name': device_name, 'platform': 'windows'}) as response:
            if response.status in {403, 429}:
                detail = (await response.json(content_type=None) or {}).get('detail')
                raise PairingRejected(detail if response.status == 429 else 'pair_code_invalid')
            response.raise_for_status()
            data = await response.json(content_type=None)
    if (not isinstance(data, dict) or data.get('hub_id') != identity['id'] or data.get('fp') != identity['fp']
            or not isinstance(data.get('device_token'), str) or not data['device_token']
            or not isinstance(data.get('device_id'), str)):
        raise ValueError('Hub kimliği veya eşleştirme yanıtı uyuşmuyor.')
    return {**identity, 'url': url, 'device_id': data['device_id'], 'device_token': data['device_token'],
            'name': (data.get('hub_name') or data.get('name') or identity['name'])[:240]}


async def request_pairing(url, fingerprint, device_name):
    """Opens the pairing QR on the main Hub's PC; carries no secret, so an advertised pin is enough."""
    async with session(fingerprint) as client:
        async with client.post(url + '/api/v1/pair/request',
                               json={'device_name': device_name, 'platform': 'windows'}) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise PairingRejected((data or {}).get('detail') or 'Hub isteği kabul etmedi.')
            return data


class RemoteHubStore:
    """Paired Hubs and this computer's device token for each; never shown to the window."""
    def __init__(self, path=None):
        self.path = Path(path) if path else security_dir() / 'remote-hubs.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS remote_hubs(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                url TEXT NOT NULL, fp TEXT NOT NULL, device_id TEXT NOT NULL, device_token TEXT NOT NULL,
                paired REAL NOT NULL)''')

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, hub):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO remote_hubs VALUES (?,?,?,?,?,?,?)',
                       (hub['id'], hub['name'], hub['url'], hub['fp'], hub['device_id'], hub['device_token'], time.time()))

    def get(self, hub_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM remote_hubs WHERE id=?', (hub_id,)).fetchone()
            return dict(row) if row else None

    def hubs(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,name,url,fp,paired FROM remote_hubs ORDER BY paired')]

    def move(self, hub_id, url):
        with self.connect() as db:
            db.execute('UPDATE remote_hubs SET url=? WHERE id=?', (url, hub_id))

    def remove(self, hub_id):
        with self.connect() as db:
            return db.execute('DELETE FROM remote_hubs WHERE id=?', (hub_id,)).rowcount
