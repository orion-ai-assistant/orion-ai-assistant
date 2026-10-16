"""Installation registry failure, identity preservation and serialization checks."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared import installation_registry as registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='orion-kayıt ', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = registry.Registry(self.root / 'registry' / 'install.json')
        self.record = registry.new_registry(self.root / 'Kullanıcı Kurulumu', [
            registry.component_record('hub', self.root / 'Hub'),
            registry.component_record('router', self.root / 'Ayrı Router', distribution='development'),
        ])

    def test_missing_read_has_no_filesystem_effect(self):
        self.assertIsNone(self.store.read())
        self.assertFalse(self.store.path.parent.exists())

    def test_roundtrip_and_previous_valid_backup(self):
        self.store.write(self.record)
        self.assertEqual(self.record, self.store.read())
        self.assertIsNone(self.store.read_backup())
        updated = deepcopy(self.record)
        updated['preferences']['automatic_updates'] = False
        self.store.write(updated)
        self.assertEqual(updated, self.store.read())
        self.assertEqual(self.record, self.store.read_backup())
        self.assertEqual(self.record['installation_id'], self.store.read()['installation_id'])

    def test_corrupt_primary_never_overwritten_or_implicitly_recovered(self):
        self.store.write(self.record)
        self.store.write(self.record)
        broken = b'{partial registry'
        self.store.path.write_bytes(broken)
        with self.assertRaises(registry.RegistryError):
            self.store.read()
        with self.assertRaises(registry.RegistryError):
            self.store.write(self.record)
        self.assertEqual(broken, self.store.path.read_bytes())
        self.assertEqual(self.record, self.store.read_backup())

    def test_future_schema_and_duplicate_fields_fail(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('{"schema_version":1,"schema_version":2}', encoding='utf-8')
        with self.assertRaises(registry.RegistryError):
            self.store.read()
        record = deepcopy(self.record)
        record['schema_version'] = 2
        with self.assertRaises(registry.RegistryError):
            self.store.write(record)

    def test_invalid_fields_and_secrets_rejected_before_creation(self):
        cases = []
        for field, value in [('installation_id', 'not-a-uuid'), ('root', 'relative'),
                             ('schema_version', True), ('secret', 'not-public'), ('components', {})]:
            record = deepcopy(self.record)
            record[field] = value
            cases.append(record)
        record = deepcopy(self.record)
        record['components'][0]['password'] = 'never-write-me'
        cases.append(record)
        record = deepcopy(self.record)
        record['preferences']['automatic_updates'] = 'false'
        cases.append(record)
        record = deepcopy(self.record)
        record['components'][0]['distribution'] = []
        cases.append(record)
        for record in cases:
            with self.subTest(record=record), self.assertRaises(registry.RegistryError):
                self.store.write(record)
        self.assertFalse(self.store.path.parent.exists())

    def test_missing_disk_is_described_without_creating_or_replacing_root(self):
        self.store.write(self.record)
        self.assertFalse(Path(self.record['root']).exists())
        self.assertEqual(self.record, self.store.read())

    def test_interrupted_primary_replace_leaves_old_record_and_backup(self):
        self.store.write(self.record)
        old_replace = registry.os.replace
        def fail_primary(source, target):
            if Path(target) == self.store.path:
                raise PermissionError('Injected primary replacement interruption')
            return old_replace(source, target)
        updated = deepcopy(self.record)
        updated['preferences']['automatic_updates'] = False
        with mock.patch.object(registry.os, 'replace', side_effect=fail_primary):
            with self.assertRaises(PermissionError):
                self.store.write(updated)
        self.assertEqual(self.record, self.store.read())
        self.assertEqual(self.record, self.store.read_backup())
        self.assertFalse(list(self.store.path.parent.glob('.install.json-*')))

    def test_interrupted_backup_does_not_modify_primary(self):
        self.store.write(self.record)
        before = self.store.path.read_bytes()
        with mock.patch.object(registry.os, 'replace', side_effect=OSError('Injected interruption')):
            with self.assertRaises(OSError):
                self.store.write(self.record)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_update_preserves_disabled_automatic_updates(self):
        self.record['preferences']['automatic_updates'] = False
        self.store.write(self.record)
        def append_client(value):
            value['components'].append(registry.component_record('client', self.root / 'Client'))
            return value
        self.store.update(append_client)
        self.assertFalse(self.store.read()['preferences']['automatic_updates'])

    def test_cross_process_updates_are_serialized(self):
        self.store.write(self.record)
        source = '''
import sys, time
from pathlib import Path
from services.shared.installation_registry import Registry, component_record
store = Registry(sys.argv[1])
def add(value):
    time.sleep(.05)
    value['components'].append(component_record('client', Path(sys.argv[2])))
    return value
store.update(add)
'''
        children = [subprocess.Popen([sys.executable, '-c', source, str(self.store.path), str(self.root / f'client-{i}')], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
        results = [(child, child.communicate(timeout=25)) for child in children]
        for child, output in results:
            self.assertEqual(0, child.returncode, output)
        self.assertEqual(5, len(self.store.read()['components']))

    def test_remote_cannot_grant_local_management(self):
        value = registry.component_record('router', self.root / 'Remote', distribution='remote', management='local')
        with self.assertRaises(registry.RegistryError):
            registry.new_registry(self.root, [value])

    def test_duplicate_component_identity_rejected(self):
        record = deepcopy(self.record)
        record['components'].append(deepcopy(record['components'][0]))
        with self.assertRaises(registry.RegistryError):
            self.store.write(record)

    def test_managed_data_collision_rejected(self):
        first = registry.component_record('hub', self.root / 'Hub', management='local')
        second = registry.component_record('router', self.root / 'Router', data_path=first['data_path'], management='local')
        with self.assertRaises(registry.RegistryError):
            registry.new_registry(self.root, [first, second])

    def test_base_python_can_read_without_site_dependencies(self):
        self.store.write(self.record)
        source = 'import sys; sys.path.insert(0,sys.argv[1]); from services.shared.installation_registry import Registry; assert Registry(sys.argv[2]).read()'
        result = subprocess.run([sys.executable, '-I', '-S', '-c', source, str(ROOT), str(self.store.path)], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_legacy_import_preserves_identities_and_source_bytes(self):
        hub = self.root / 'Eski Hub'
        router = self.root / 'Eski Router'
        hub.mkdir()
        router.mkdir()
        (hub / 'services' / 'hub').mkdir(parents=True)
        (hub / 'services' / 'hub' / 'pyproject.toml').write_text('[project]\nname="orion-hub"\n', encoding='utf-8')
        (router / 'pyproject.toml').write_text('[project]\nname="orion-router"\n', encoding='utf-8')
        hub_id, router_id, lifecycle_id = (str(uuid.uuid4()) for _ in range(3))
        identity_path = router / 'persistent' / 'mdns-id'
        identity_path.parent.mkdir()
        identity_path.write_text(router_id, encoding='ascii')
        cert = router / 'certificate.pem'
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta, timezone
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'Orion Router {router_id}')])
        now = datetime.now(timezone.utc)
        certificate = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256())
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        hub_cert = hub / 'certificate.pem'
        hub_key = ec.generate_private_key(ec.SECP256R1())
        hub_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hub_id)])
        hub_certificate = x509.CertificateBuilder().subject_name(hub_name).issuer_name(hub_name).public_key(hub_key.public_key()).serial_number(2).not_valid_before(now).not_valid_after(now + timedelta(days=1)).sign(hub_key, hashes.SHA256())
        hub_cert.write_bytes(hub_certificate.public_bytes(serialization.Encoding.PEM))
        db = router / 'persistent' / 'database.bin'
        db.write_bytes(b'existing database contents')
        lifecycle = router / '.orion-lifecycle' / 'router-prod' / 'identity.json'
        lifecycle.parent.mkdir(parents=True)
        lifecycle.write_text(json.dumps(dict(v=1, id=lifecycle_id, root=str(router), component='router-prod')), encoding='utf-8')
        hub_manifest = self.root / 'hub-installation.json'
        hub_manifest.write_text(json.dumps(dict(v=1, kind='orion-hub-local-installation', root=str(hub), id=hub_id, certificate_path=str(hub_cert), local_token_path='private ignored')), encoding='utf-8')
        router_manifest = self.root / 'router-installation.json'
        router_manifest.write_text(json.dumps(dict(v=1, kind='orion-router-local-installation', root=str(router), identity_path=str(identity_path), certificate_path=str(cert), tls_port=4000)), encoding='utf-8')
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        record = registry.import_legacy_installations(hub_manifest, router_manifest, self.root)
        self.assertEqual([hub_id, router_id], [item['component_id'] for item in record['components']])
        self.assertEqual({'router-prod': lifecycle_id}, record['components'][1]['lifecycle_ids'])
        self.assertEqual(64, len(record['components'][1]['tls_spki_sha256']))
        import hashlib
        for component, public_key in zip(record['components'], (hub_key.public_key(), key.public_key())):
            expected = hashlib.sha256(public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
            self.assertEqual(expected, component['tls_spki_sha256'])
        self.assertNotIn('local_token_path', json.dumps(record))
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        self.assertFalse(self.store.path.exists())
        self.assertEqual(1, len(registry.import_legacy_installations(hub_manifest=hub_manifest)['components']))
        self.assertEqual(1, len(registry.import_legacy_installations(router_manifest=router_manifest)['components']))
        # Import compares local public certificate identity before retaining a
        # trust pin; it must not rewrite UUIDs to match a mismatching cert.
        identity_path.write_text(str(uuid.uuid4()), encoding='ascii')
        with self.assertRaises(registry.RegistryError):
            registry.import_legacy_installations(router_manifest=router_manifest)

    def test_legacy_wrong_product_and_missing_disk_fail(self):
        manifest = self.root / 'legacy.json'
        (self.root / 'pyproject.toml').write_text('[project]\nname="wrong-product"\n', encoding='utf-8')
        manifest.write_text(json.dumps(dict(v=1, kind='orion-router-local-installation', root=str(self.root))), encoding='utf-8')
        with self.assertRaises(registry.RegistryError):
            registry.import_legacy_installations(router_manifest=manifest)
        missing = self.root / 'Missing Disk'
        manifest.write_text(json.dumps(dict(v=1, kind='orion-router-local-installation', root=str(missing))), encoding='utf-8')
        with self.assertRaises(registry.RegistryError):
            registry.import_legacy_installations(router_manifest=manifest)
        self.assertFalse(missing.exists())

    def test_access_denied_is_not_absence(self):
        with mock.patch.object(Path, 'read_bytes', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(registry.RegistryError, 'inaccessible'):
                self.store.read()

    def test_bad_legacy_does_not_create_a_new_identity(self):
        manifest = self.root / 'legacy.json'
        manifest.write_text(json.dumps(dict(v=1, kind='orion-router-local-installation', root=str(self.root))), encoding='utf-8')
        with self.assertRaises(registry.RegistryError):
            registry.import_legacy_installations(router_manifest=manifest)
        self.assertFalse((self.root / 'persistent').exists())


if __name__ == '__main__':
    unittest.main()
