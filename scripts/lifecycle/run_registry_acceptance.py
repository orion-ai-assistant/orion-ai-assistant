"""Stage 02 fresh AppData inventory fixture; no user product is started/copied.

Only creates a unique Orion/lifecycle-tests child. Retains artifacts and a result
record. Public live manifests are hashed before/after, never modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lifecycle.test_environment import LifecycleLab, snapshot
from services.shared.installation_control import import_legacy
from services.shared.installation_registry import Registry
from services.shared.router_installation import inspect_router_folder, registered_router_root


def public_manifests(appdata):
    result = {}
    for name in ('install.json', 'router-installation.json', 'hub-installation.json'):
        path = appdata / 'Orion' / name
        try:
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            result[name] = None
    return result


def align_synthetic_certificate(path, identity):
    """Use the real product CN format on new synthetic fixture certificates."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from datetime import datetime, timedelta, timezone
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
                   .sign(key, hashes.SHA256()))
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = path.with_name('hub-key.pem' if path.name == 'hub-cert.pem' else 'private-key.pem')
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appdata', type=Path, required=True)
    args = parser.parse_args()
    appdata = args.appdata.resolve(strict=True)
    before = public_manifests(appdata)
    base = appdata / 'Orion/lifecycle-tests'
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='stage02-', dir=base))
    lab = LifecycleLab(scratch)
    # Complete source recognition fixtures; contents deliberately fail if run.
    for item in lab.installations:
        router = item['router']
        align_synthetic_certificate(router / 'persistent/tls/certificate.pem',
                                    'Orion Router ' + (router / 'persistent/mdns-id').read_text().strip())
        security = item['hub'] / 'files/security'
        align_synthetic_certificate(security / 'hub-cert.pem', (security / 'hub-id').read_text().strip())
        (router / 'pyproject.toml').write_text('[project]\nname="orion-router"\nversion="0.1.2"\n', encoding='utf-8')
        for name in ('main.py', 'cli.py', 'core/config.py', 'bin/prod.py', 'bin/stop.py'):
            path = router / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('raise RuntimeError("Validation must never execute selected contents")\n', encoding='utf-8')
    preserved = {str(item['root']): snapshot(item['hub']) | {'router:' + key: value for key, value in snapshot(item['router']).items()}
                 for item in lab.installations}
    checks = []
    for index, item in enumerate(lab.installations):
        with patch.dict(os.environ, lab.environment(index)):
            store = Registry()
            record = import_legacy(store, hub_manifest=item['profile'] / 'Orion/hub-installation.json',
                                   router_manifest=item['profile'] / 'Orion/router-installation.json')
            assert store.read() == record
            assert registered_router_root() == str(item['router'])
            component = next(value for value in record['components'] if value['kind'] == 'router')
            candidate = inspect_router_folder(item['router'], expected_identity=component['router_uuid'],
                                              expected_spki=component['tls_spki_sha256'])
            assert candidate.status == 'closed-unverified'
            assert inspect_router_folder(item['router'], expected_spki='0' * 64).status == 'identity-mismatch'
            checks.append({'root': str(item['root']), 'registry': str(store.path), 'router_id': candidate.identity,
                           'status': candidate.status, 'pin_mismatch_rejected': True})
    for item in lab.installations:
        actual = snapshot(item['hub']) | {'router:' + key: value for key, value in snapshot(item['router']).items()}
        assert actual == preserved[str(item['root'])]
    after = public_manifests(appdata)
    assert before == after
    assert checks[0]['router_id'] != checks[1]['router_id']
    report = {'success': True, 'scope': 'synthetic inventory only; no product runtime installation',
              'checks': checks, 'live_public_manifests_preserved': before == after,
              'fixture_source_data_identity_hashes_preserved': True, 'processes_started': 0}
    (scratch / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'result': str(scratch / 'result.json'), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
