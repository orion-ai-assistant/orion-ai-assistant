"""Location validation refuses unsafe targets without touching user contents."""
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services.shared import installation_paths as paths
from services.shared.installation_registry import component_record, new_registry, RegistryError, validate_record
from services.shared.installation_registry import default_registry_path


ROOT = Path(__file__).resolve().parents[2]


class InstallationPathTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='konum Türkçe ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_defaults_follow_selected_root_with_spaces_and_turkish(self):
        record = new_registry(self.root / 'Özel Orion')
        self.assertEqual(self.root / 'Özel Orion' / 'Models', paths.resolve_location(record, 'models'))
        self.assertEqual(self.root / 'Özel Orion' / 'Downloads', paths.resolve_location(record, 'downloads'))
        self.assertFalse((self.root / 'Özel Orion').exists())

    def test_explicit_shared_locations_override_root_without_inference(self):
        record = new_registry(self.root / 'Orion')
        record['locations'] = {'models': str(self.root / 'Paylaşılan Modeller')}
        self.assertEqual(self.root / 'Paylaşılan Modeller', paths.resolve_location(record, 'models'))
        self.assertEqual(self.root / 'Orion' / 'backups', paths.resolve_location(record, 'backups'))
        with self.assertRaises(paths.PathValidationError):
            paths.resolve_location(record, 'passwords')

    def test_registry_locations_reject_unknown_relative_and_secret_fields(self):
        for location in ({'models': 'relative'}, {'Models': str(self.root)}, {'models': None}, {'password': 'secret'}):
            record = new_registry(self.root)
            record['locations'] = location
            with self.subTest(location=location), self.assertRaises(RegistryError):
                validate_record(record)

    def test_explicit_registry_path_is_honored_and_invalid_override_fails(self):
        selected = str(self.root / 'isolated' / 'install.json')
        with patch.dict(os.environ, ORION_INSTALLATION_REGISTRY=selected):
            self.assertEqual(Path(selected), default_registry_path())
        for invalid in ('', 'relative.json', str(self.root / 'part' / '..' / 'install.json')):
            with self.subTest(invalid=invalid), patch.dict(os.environ, ORION_INSTALLATION_REGISTRY=invalid):
                with self.assertRaises(RegistryError):
                    default_registry_path()

    def test_uncreated_target_is_validated_without_creating_directories(self):
        target = self.root / 'Yeni kurulum' / 'Models'
        before = list(self.root.iterdir())
        result = paths.validate_destination(target, required_bytes=1)
        self.assertEqual(target, result.path)
        self.assertFalse(result.existing)
        self.assertGreaterEqual(result.free_bytes, 1)
        self.assertEqual(before, list(self.root.iterdir()))

    def test_empty_target_permitted_but_occupied_target_never_modified(self):
        target = self.root / 'hedef'
        target.mkdir()
        self.assertTrue(paths.validate_destination(target).existing)
        user_file = target / 'user.txt'
        user_file.write_bytes(b'do not overwrite')
        with self.assertRaisesRegex(paths.PathValidationError, 'occupied'):
            paths.validate_destination(target)
        self.assertEqual(b'do not overwrite', user_file.read_bytes())

    def test_same_and_both_nested_directions_rejected(self):
        source = self.root / 'source'
        source.mkdir()
        child = source / 'child'
        child.mkdir()
        for target, selected_source in ((source, source), (child, source), (source, child)):
            with self.subTest(target=target), self.assertRaisesRegex(paths.PathValidationError, 'same or nested'):
                paths.validate_destination(target, source=selected_source)

    def test_other_component_data_overlap_rejected(self):
        occupied = self.root / 'RouterData'
        for target in (occupied, occupied / 'subdir', self.root):
            with self.subTest(target=target), self.assertRaisesRegex(paths.PathValidationError, 'registered data'):
                paths.validate_destination(target, occupied_paths=[occupied])

    def test_space_and_write_failures_do_not_create_destination(self):
        destination = self.root / 'never-created'
        with patch.object(paths.shutil, 'disk_usage', return_value=SimpleNamespace(free=99)):
            with self.assertRaisesRegex(paths.PathValidationError, 'Insufficient'):
                paths.validate_destination(destination, required_bytes=100)
        with patch.object(paths, '_probe_writable', side_effect=paths.PathValidationError('Target is not writable')):
            with self.assertRaisesRegex(paths.PathValidationError, 'not writable'):
                paths.validate_destination(destination)
        self.assertFalse(destination.exists())

    def test_invalid_space_requests_rejected(self):
        for size in (-1, True, 1.5, '100'):
            with self.subTest(size=size), self.assertRaises(paths.PathValidationError):
                paths.validate_destination(self.root / 'target', required_bytes=size)

    def test_missing_source_and_disk_are_explicit_errors(self):
        with self.assertRaises(paths.PathValidationError):
            paths.validate_destination(self.root / 'target', source=self.root / 'missing')
        with patch.object(paths, '_fixed_local_disk', side_effect=paths.PathValidationError('Only available fixed local disks are supported')):
            with self.assertRaisesRegex(paths.PathValidationError, 'fixed local'):
                paths.validate_destination(self.root / 'target')
        self.assertEqual([], list(self.root.iterdir()))

    def test_non_directory_ancestor_rejected(self):
        file = self.root / 'file'
        file.write_bytes(b'preserve me')
        with self.assertRaises(paths.PathValidationError):
            paths.validate_destination(file / 'child')
        self.assertEqual(b'preserve me', file.read_bytes())

    def test_relative_parent_traversal_and_windows_aliases_rejected(self):
        cases = ['relative', self.root / '..' / 'escape']
        if os.name == 'nt':
            cases.extend([str(self.root / 'CON.txt'), str(self.root / 'name.'), str(self.root / 'file:stream'), '\\\\server\\share\\Orion', '\\\\?\\C:\\Orion'])
        for target in cases:
            with self.subTest(target=target), self.assertRaises(paths.PathValidationError):
                paths.canonical_path(target)

    def test_reparse_ancestor_refused_even_when_destination_is_missing(self):
        real_lstat = Path.lstat
        def fake_lstat(path):
            info = real_lstat(path)
            if path == self.root:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'lstat', fake_lstat):
            with self.assertRaisesRegex(paths.PathValidationError, 'junction'):
                paths.canonical_path(self.root / 'new')

    def test_access_denied_is_not_a_missing_path(self):
        with patch.object(Path, 'lstat', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(paths.PathValidationError, 'inaccessible'):
                paths.canonical_path(self.root / 'new')

    def test_component_paths_keep_independent_roots_and_modes(self):
        installed = component_record('router', self.root / 'installed', data_path=self.root / 'persistent')
        development = component_record('router', self.root / 'development', distribution='development')
        record = new_registry(self.root / 'Hub', [installed, development])
        selected = paths.registered_component_paths(record, 'router')
        self.assertEqual(self.root / 'installed', selected['code_path'])
        self.assertEqual(self.root / 'persistent', selected['data_path'])
        self.assertEqual(self.root / 'development', paths.registered_component_paths(record, 'router', distribution='development')['code_path'])
        with self.assertRaises(paths.PathValidationError):
            paths.registered_component_paths(record, 'hub')

    def test_private_acl_allows_current_user_system_admin_and_readonly_groups(self):
        sid = 'S-1-5-21-123-456-789-1000'
        record = {'current_sid': sid, 'owner_sid': sid, 'rules': [
            {'sid': sid, 'rights': 0x1f01ff, 'allow': True},
            {'sid': 'S-1-5-18', 'rights': 0x1f01ff, 'allow': True},
            {'sid': 'S-1-5-32-544', 'rights': 0x1f01ff, 'allow': True},
            {'sid': 'S-1-1-0', 'rights': 0x120089, 'allow': True}]}
        paths._validate_windows_acl(record)
        record['rules'].append({'sid': 'S-1-1-0', 'rights': 2, 'allow': False})
        paths._validate_windows_acl(record)

    def test_private_acl_rejects_other_writer_owner_and_unreadable_policy(self):
        sid = 'S-1-5-21-123-456-789-1000'
        for record in (
            {'current_sid': sid, 'owner_sid': sid, 'rules': [{'sid': 'S-1-5-11', 'rights': 2, 'allow': True}]},
            {'current_sid': sid, 'owner_sid': 'S-1-5-21-other-user', 'rules': [{'sid': sid, 'rights': 2, 'allow': True}]},
            {'current_sid': sid, 'owner_sid': sid, 'rules': []},
            {}):
            with self.subTest(record=record), self.assertRaises(paths.PathValidationError):
                paths._validate_windows_acl(record)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL adapter')
    def test_private_directory_fails_closed_on_acl_access_denied(self):
        with patch.object(paths, '_windows_acl', side_effect=paths.PathValidationError('Cannot verify private directory permissions')):
            with self.assertRaisesRegex(paths.PathValidationError, 'Cannot verify'):
                paths.validate_private_directory(self.root / 'new')

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL adapter')
    def test_real_current_user_directory_acl_is_read_without_modification(self):
        # Actual OS ACL observation is independent of whether this developer's
        # workspace inherited broad write rights: policy must match observation.
        observed = paths._windows_acl(self.root)
        before = list(self.root.iterdir())
        try:
            paths._validate_windows_acl(observed)
        except paths.PathValidationError:
            with self.assertRaises(paths.PathValidationError):
                paths.validate_private_directory(self.root)
        else:
            self.assertEqual(self.root, paths.validate_private_directory(self.root))
        self.assertEqual(before, list(self.root.iterdir()))


if __name__ == '__main__':
    unittest.main()
