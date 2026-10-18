"""Single-key release signing for a small project (one Ed25519 key, TUF threshold 1).

`init` creates one passphrase-encrypted private key OUTSIDE the repository and a
public `trusted-root.json` that is safe to commit and to ship in the installer.
The same key signs root, targets, snapshot and timestamp. Losing the key means
existing installations can no longer trust new releases: back it up.
"""
import argparse
from datetime import datetime, timedelta, timezone
import getpass
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner, SSlibKey
from tuf.api.metadata import Metadata, Root

ROOT_VALID_DAYS = 36500  # 100 years: an installation must never be locked out by an expired root.
PASSPHRASE_ENV = 'ORION_RELEASE_KEY_PASSPHRASE'


def _passphrase(confirm=False, enforce_length=True):
    value = os.environ.get(PASSPHRASE_ENV)
    if value is None:
        value = getpass.getpass('Release key passphrase (typing is hidden): ')
        print(f'  ({len(value)} characters entered)')
        if confirm and getpass.getpass('Repeat passphrase: ') != value:
            raise ValueError('Passphrases differ')
    if enforce_length and len(value) < 8:
        raise ValueError('Passphrase must be at least 8 characters')
    return value.encode('utf-8')


def load_signer(key_path, passphrase=None):
    """Decrypt the release key. `passphrase` (bytes) avoids prompting again."""
    try:
        private = serialization.load_pem_private_key(
            Path(key_path).read_bytes(), passphrase or _passphrase(enforce_length=False))
    except (ValueError, TypeError) as error:
        raise ValueError('Could not unlock the release key: wrong passphrase?') from error
    return CryptoSigner(private, SSlibKey.from_crypto(private.public_key()))


def init(key_dir, public_root):
    key_dir, public_root = Path(key_dir), Path(public_root)
    key_path = key_dir / 'release-key.pem'
    if key_path.exists() or public_root.exists():
        raise ValueError('Refusing to overwrite an existing release key or public root')
    password = _passphrase(confirm=True)
    private = Ed25519PrivateKey.generate()
    signer = CryptoSigner(private, SSlibKey.from_crypto(private.public_key()))
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password)))
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=ROOT_VALID_DAYS)))
    for role in ('root', 'targets', 'snapshot', 'timestamp'):
        root.signed.add_key(signer.public_key, role)
    root.sign(signer)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    public_root.write_bytes(root.to_bytes())
    return key_path, public_root


def single_key_root(root):
    """True only for the explicit one-key policy created by `init`."""
    roles = root.signed.roles
    keyids = {role: set(roles[role].keyids) for role in roles}
    return all(len(ids) == 1 and roles[role].threshold == 1 for role, ids in keyids.items()) \
        and len({next(iter(ids)) for ids in keyids.values()}) == 1


def renew_root(key_path, public_root, passphrase=None, *, now=None):
    """Next root version: same key and roles, valid ROOT_VALID_DAYS from now, signed by the release key (which
    satisfies both the old and the new root, as TUF requires). The next release publishes it as N.root.json;
    installed clients walk 1 -> 2 -> ... from the root they ship with, so nothing else changes for them."""
    public_root = Path(public_root)
    root = Metadata.from_file(str(public_root))
    if not isinstance(root.signed, Root) or not single_key_root(root):
        raise ValueError('renew-root handles the one-key root created by init only')
    signer = load_signer(key_path, passphrase)
    if signer.public_key.keyid not in root.signed.roles['root'].keyids:
        raise ValueError('This release key does not sign the current root')
    root.signed.version += 1
    root.signed.expires = (now or datetime.now(timezone.utc)) + timedelta(days=ROOT_VALID_DAYS)
    root.signatures.clear()
    root.sign(signer)
    root.verify_delegate('root', root)
    public_root.write_bytes(root.to_bytes())
    return root.signed.version, root.signed.expires


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'check', 'renew-root'],
                        help='renew-root: next root version valid for 100 years (commit trusted-root.json, then release)')
    parser.add_argument('--key-dir', type=Path, default=Path.home() / '.orion-release')
    parser.add_argument('--public-root', type=Path, default=Path(__file__).with_name('trusted-root.json'))
    args = parser.parse_args()
    if args.action == 'check':
        load_signer(args.key_dir / 'release-key.pem')
        print('Passphrase is correct; the release key unlocks.')
        sys.exit(0)
    if args.action == 'renew-root':
        version, expires = renew_root(args.key_dir / 'release-key.pem', args.public_root)
        print(f'Root version {version}, valid until {expires:%Y-%m-%d}: {args.public_root}\n'
              'Commit it; the next release.cmd publishes it to both feeds.')
        sys.exit(0)
    key, public = init(args.key_dir, args.public_root)
    print(f'Private key (keep secret, back it up): {key}\nPublic root (commit this): {public}')
    sys.exit(0)
