"""Read-only selection tests; fixtures hold fresh identities and synthetic DBs."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_environment import temporary_lab, snapshot
from services.shared.router_installation import (
    LiveRouterEvidence, RouterSelectionError, discover_router_candidates,
    find_router_script, inspect_router_folder, legacy_locator_path,
    registered_router_root, select_router_candidate,
)


class RouterInstallationTests(unittest.TestCase):
    def setUp(self):
        self.context = temporary_lab()
        # Windows sandbox's redirected AC/Temp rejects atomic rename; fixtures
        # live in the writable workspace, as the lifecycle baseline runner does.
        temporary_root = Path(__file__).resolve().parents[2] / '.tmp/router-selection'
        temporary_root.mkdir(parents=True, exist_ok=True)
        temporary_directory = tempfile.TemporaryDirectory
        with patch('test_environment.tempfile.TemporaryDirectory',
                   side_effect=lambda **kwargs: temporary_directory(dir=temporary_root, **kwargs)):
            self.lab = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.roots = [item['router'] for item in self.lab.installations]
        for root in self.roots:
            self.set_certificate_name(root, 'Orion Router ' + (root / 'persistent/mdns-id').read_text())
            (root / 'pyproject.toml').write_text('[project]\nname="orion-router"\nversion="0.1.2"\n')
            for entry in ('main.py', 'core/config.py', 'bin/prod.py', 'bin/stop.py',
                          'orionrouter.ps1', 'orionrouter'):
                path = root / entry
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_text('raise RuntimeError("must never execute")\n')

    @staticmethod
    def set_certificate_name(root, common_name):
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID
        key = serialization.load_pem_private_key((root / 'persistent/tls/private-key.pem').read_bytes(), password=None)
        previous = x509.load_pem_x509_certificate((root / 'persistent/tls/certificate.pem').read_bytes())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.now(timezone.utc)
        builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                   .not_valid_after(now + timedelta(days=2)))
        for extension in previous.extensions:
            builder = builder.add_extension(extension.value, critical=extension.critical)
        (root / 'persistent/tls/certificate.pem').write_bytes(builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))

    def test_recognized_closed_does_not_execute_or_modify(self):
        before = snapshot(self.lab.root)
        with patch('subprocess.Popen', side_effect=AssertionError('executed')):
            candidate = inspect_router_folder(self.roots[0])
        self.assertEqual(candidate.status, 'closed-unverified')
        self.assertIsNotNone(candidate.identity)
        self.assertEqual(len(candidate.spki), 64)
        self.assertEqual(snapshot(self.lab.root), before)

    def test_missing_and_wrong_product(self):
        self.assertEqual(inspect_router_folder(self.lab.root / 'absent').status, 'missing-broken')
        (self.roots[0] / 'pyproject.toml').write_text('[project]\nname="fake"\nversion="0.1.2"')
        self.assertEqual(inspect_router_folder(self.roots[0]).status, 'missing-broken')

    def test_entrypoint_missing_and_fake_incomplete_source(self):
        (self.roots[0] / 'main.py').unlink()
        self.assertEqual(inspect_router_folder(self.roots[0]).status, 'missing-broken')

    def test_malformed_project_table_is_broken_not_an_uncaught_error(self):
        (self.roots[0] / 'pyproject.toml').write_text('project="orion-router"')
        self.assertEqual(inspect_router_folder(self.roots[0]).status, 'missing-broken')
        with self.assertRaises(RouterSelectionError):
            registered_router_root()

    def test_version_and_signed_package_are_not_silently_accepted(self):
        (self.roots[0] / 'pyproject.toml').write_text('[project]\nname="orion-router"\nversion="9.0.0"')
        self.assertEqual(inspect_router_folder(self.roots[0]).status, 'incompatible')
        self.assertEqual(inspect_router_folder(self.roots[1], distribution='packaged').status, 'incompatible')

    def test_access_denied_is_not_closed(self):
        original = Path.read_text
        def blocked(path, *args, **kwargs):
            if path.name == 'mdns-id':
                raise PermissionError('fixture ACL denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', blocked):
            self.assertEqual(inspect_router_folder(self.roots[0]).status, 'access-denied')

    def test_uuid_and_spki_mismatch_preserve_all_data(self):
        before = snapshot(self.lab.root)
        for kwargs in ({'expected_identity': str(uuid4())}, {'expected_spki': '0' * 64}):
            self.assertEqual(inspect_router_folder(self.roots[0], **kwargs).status, 'identity-mismatch')
        self.assertEqual(snapshot(self.lab.root), before)

    def test_certificate_wrong_uuid_with_same_key_is_identity_mismatch(self):
        root = self.roots[0]
        previous = inspect_router_folder(root)
        key = (root / 'persistent/tls/private-key.pem').read_bytes()
        self.set_certificate_name(root, 'Orion Router ' + str(uuid4()))
        before = snapshot(self.lab.root)
        candidate = inspect_router_folder(root, expected_identity=previous.identity, expected_spki=previous.spki)
        self.assertEqual(candidate.status, 'identity-mismatch')
        self.assertEqual(candidate.spki, previous.spki)
        self.assertEqual((root / 'persistent/tls/private-key.pem').read_bytes(), key)
        self.assertEqual(snapshot(self.lab.root), before)

    def test_ready_requires_trusted_runtime_evidence_and_existing_pin(self):
        root = self.roots[0]
        closed = inspect_router_folder(root)
        evidence = LiveRouterEvidence(closed.root, closed.identity, closed.spki, True, True, True, True)
        self.assertEqual(inspect_router_folder(root, live_verifier=lambda _: evidence).status, 'closed-unverified')
        kwargs = dict(expected_identity=closed.identity, expected_spki=closed.spki)
        self.assertEqual(inspect_router_folder(root, live_verifier=lambda _: evidence, **kwargs).status, 'ready')
        for field in ('process_verified', 'tls_verified', 'database_verified'):
            with self.subTest(field=field):
                incomplete = replace(evidence, **{field: False})
                self.assertEqual(inspect_router_folder(root, live_verifier=lambda _: incomplete, **kwargs).status,
                                 'closed-unverified')
        foreign = replace(evidence, root=str(self.roots[1]))
        self.assertEqual(inspect_router_folder(root, live_verifier=lambda _: foreign, **kwargs).status, 'identity-mismatch')
        incompatible = replace(evidence, api_compatible=False)
        self.assertEqual(inspect_router_folder(root, live_verifier=lambda _: incompatible, **kwargs).status, 'incompatible')

    def test_discovery_and_selection_require_explicit_ambiguity_resolution(self):
        before = snapshot(self.lab.root)
        candidates = discover_router_candidates(registered_root=self.roots[1], legacy_roots=self.roots)
        self.assertEqual([item.root for item in candidates], [str(self.roots[1]), str(self.roots[0])])
        with self.assertRaises(RouterSelectionError):
            select_router_candidate(candidates)
        selected = select_router_candidate(candidates, root=self.roots[0])
        self.assertEqual(selected.root, str(self.roots[0]))
        self.assertEqual(snapshot(self.lab.root), before)

    def test_installed_and_development_are_separate(self):
        candidates = discover_router_candidates(legacy_roots=[self.roots[0]], development_roots=[self.roots[1]])
        self.assertEqual(select_router_candidate(candidates).root, str(self.roots[0]))
        self.assertEqual(select_router_candidate(candidates, distribution='development').root, str(self.roots[1]))
        with self.assertRaises(RouterSelectionError):
            select_router_candidate(candidates, root=self.roots[1])

    def test_bad_legacy_registration_requires_repair_no_known_path_fallback(self):
        legacy_locator_path().write_text('{broken')
        with self.assertRaises(RouterSelectionError):
            registered_router_root()
        with self.assertRaises(RouterSelectionError):
            find_router_script()

    def test_registered_missing_disk_and_wrong_product_do_not_fallback(self):
        path = legacy_locator_path()
        record = json.loads(path.read_text())
        record['root'] = str(self.lab.root / 'Missing disk')
        path.write_text(json.dumps(record))
        with self.assertRaises(RouterSelectionError):
            find_router_script()
        record['root'] = str(self.roots[0])
        path.write_text(json.dumps(record))
        (self.roots[0] / 'pyproject.toml').write_text('[project]\nname="other-product"')
        with self.assertRaises(RouterSelectionError):
            registered_router_root()

    def test_registered_wrapper_priority_and_missing_wrapper_no_fallback(self):
        with patch('shutil.which', side_effect=AssertionError('PATH queried')):
            kind, wrapper = find_router_script()
        self.assertEqual(kind, 'win' if os.name == 'nt' else 'unix')
        self.assertEqual(Path(wrapper).parent, self.roots[0])
        Path(wrapper).unlink()
        with self.assertRaises(RouterSelectionError):
            find_router_script()

    def test_two_known_roots_never_select_first(self):
        legacy_locator_path().unlink()
        with patch('services.shared.router_installation.known_legacy_roots', return_value=self.roots):
            with self.assertRaises(RouterSelectionError):
                find_router_script()

    def test_custom_public_identity_and_data_paths(self):
        candidate = inspect_router_folder(self.roots[0], data_path=self.roots[1] / 'persistent',
                                         state_path=self.roots[1] / 'state')
        second = inspect_router_folder(self.roots[1])
        self.assertEqual(candidate.identity, second.identity)
        self.assertEqual(candidate.spki, second.spki)
        self.assertEqual(candidate.status, 'closed-unverified')

    def test_canonical_registry_overrides_legacy_and_verifies_pin(self):
        from services.shared.installation_registry import Registry, component_record, new_registry
        selected = inspect_router_folder(self.roots[1])
        component = component_record('router', self.roots[1], data_path=self.roots[1] / 'persistent',
                                     router_uuid=selected.identity, tls_spki_sha256=selected.spki)
        Registry().write(new_registry(self.lab.root, [component]))
        self.assertEqual(registered_router_root(), str(self.roots[1]))
        self.assertEqual(Path(find_router_script()[1]).parent, self.roots[1])
        component['tls_spki_sha256'] = '0' * 64
        Registry().write(new_registry(self.lab.root, [component]))
        with self.assertRaisesRegex(RouterSelectionError, 'identity-mismatch'):
            registered_router_root()
        with self.assertRaisesRegex(RouterSelectionError, 'identity-mismatch'):
            find_router_script()
        component['tls_spki_sha256'] = selected.spki
        component['router_uuid'] = str(uuid4())
        Registry().write(new_registry(self.lab.root, [component]))
        with self.assertRaisesRegex(RouterSelectionError, 'identity-mismatch'):
            registered_router_root()

    def test_canonical_no_local_router_does_not_fallback_to_legacy(self):
        from services.shared.installation_registry import Registry, component_record, new_registry
        component = component_record('router', self.roots[1], distribution='development')
        Registry().write(new_registry(self.lab.root, [component]))
        self.assertIsNone(registered_router_root())
        self.assertEqual(find_router_script(), (None, None))

    def test_canonical_corruption_is_not_hidden_by_valid_legacy(self):
        from services.shared.installation_registry import RegistryError, default_registry_path
        default_registry_path().write_text('{invalid')
        with self.assertRaises(RegistryError):
            registered_router_root()

    def test_canonical_external_identity_paths_are_used(self):
        from services.shared.installation_registry import Registry, component_record, new_registry
        selected = inspect_router_folder(self.roots[1])
        external = self.roots[1] / 'persistent'
        component = component_record('router', self.roots[0], data_path=self.roots[0] / 'persistent',
                                     router_uuid=selected.identity, tls_spki_sha256=selected.spki,
                                     identity_path=str(external / 'mdns-id'),
                                     certificate_path=str(external / 'tls/certificate.pem'))
        Registry().write(new_registry(self.lab.root, [component]))
        self.assertEqual(Path(find_router_script()[1]).parent, self.roots[0])


if __name__ == '__main__':
    unittest.main()
