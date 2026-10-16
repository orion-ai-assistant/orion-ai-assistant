"""Isolated copy-and-switch recovery; never opens real models, DBs or processes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.shared.content_migration import (
    MigrationError, begin_content_migration, registry_revision, resume_content_migration,
)
from services.shared.installation_registry import Registry, component_record, new_registry
from services.shared.installation_paths import PathValidationError, resolve_location


class Crash(BaseException):
    pass


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


class ContentMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[2] / '.tmp/content-migration-tests'
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry, self.source, self.destination = self.environment(self.root)

    @staticmethod
    def environment(root):
        installed = root / 'Kurulum ş'
        source = installed / 'Models'
        (source / 'nested').mkdir(parents=True)
        (source / 'empty directory').mkdir()
        (source / 'model ü.gguf').write_bytes(b'synthetic model\x00' * 250)
        (source / 'nested/metadata.json').write_bytes(b'{"sample": true}')
        (installed / 'Downloads').mkdir()
        registry = Registry(root / 'profile/install.json')
        registry.write(new_registry(installed))
        return registry, source, root / 'Custom models ü'

    def begin(self, **kwargs):
        kwargs.setdefault('expected_revision', registry_revision(self.registry.read()))
        kwargs.setdefault('quiescence_gate', lambda: True)
        return begin_content_migration(self.registry, 'models', self.destination, **kwargs)

    def journal_path(self):
        return next((self.registry.path.parent / 'migrations').glob('*.json'))

    def test_success_retains_source_and_switches_one_authority(self):
        before = snapshot(self.source)
        with patch('subprocess.Popen', side_effect=AssertionError('no processes')):
            result = self.begin()
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.destination)
        self.assertEqual(snapshot(self.source), before)
        copied = snapshot(self.destination)
        copied.pop('.orion-content-migration.json')
        self.assertEqual(copied, before)
        self.assertTrue((self.destination / 'empty directory').is_dir())
        self.assertEqual(self.registry.read_backup()['locations'] if 'locations' in self.registry.read_backup() else {}, {})

    def test_downloads_supported_but_runtime_db_and_router_are_not(self):
        for name in ('runtime', 'router', 'database', 'hub'):
            with self.subTest(name=name), self.assertRaises(MigrationError):
                begin_content_migration(self.registry, name, self.destination,
                    expected_revision=registry_revision(self.registry.read()), quiescence_gate=lambda: True)
        downloads = resolve_location(self.registry.read(), 'downloads')
        (downloads / 'partial.bin').write_bytes(b'partial payload')
        result = begin_content_migration(self.registry, 'downloads', self.destination,
                    expected_revision=registry_revision(self.registry.read()), quiescence_gate=lambda: True)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual((downloads / 'partial.bin').read_bytes(), b'partial payload')

    def test_every_durable_boundary_can_be_recovered(self):
        boundaries = [('planned', 1), ('copying', 1), ('copying', 2), ('copying', 3),
                      ('verified', 1), ('switching', 1), ('registry-switched', 1), ('completed', 1)]
        for index, (boundary, occurrence) in enumerate(boundaries):
            with self.subTest(boundary=boundary, occurrence=occurrence):
                registry, source, destination = self.environment(self.root / str(index))
                before, revision = snapshot(source), registry_revision(registry.read())
                counts = Counter()
                def fault(phase, journal):
                    counts[phase] += 1
                    if phase == boundary and counts[phase] == occurrence:
                        raise Crash(phase)
                with self.assertRaises(Crash):
                    begin_content_migration(registry, 'models', destination,
                        expected_revision=revision, quiescence_gate=lambda: True, fault=fault)
                active = resolve_location(registry.read(), 'models')
                self.assertEqual(active, destination if boundary in {'registry-switched', 'completed'} else source)
                journal = next((registry.path.parent / 'migrations').glob('*.json'))
                recovered = resume_content_migration(registry, journal, quiescence_gate=lambda: True)
                self.assertEqual(recovered['phase'], 'completed')
                self.assertEqual(resolve_location(registry.read(), 'models'), destination)
                self.assertEqual(snapshot(source), before)

    def test_unknown_or_active_consumers_refuse_before_copy(self):
        before = self.registry.path.read_bytes()
        for gate in (None, lambda: False, lambda: None, lambda: 'idle'):
            with self.subTest(gate=gate), self.assertRaises(MigrationError):
                self.begin(quiescence_gate=gate)
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertFalse(self.destination.exists())

    def test_busy_gate_during_copy_requires_repair_without_switch(self):
        calls = Counter()
        def gate():
            calls['gate'] += 1
            return calls['gate'] < 3
        revision = registry_revision(self.registry.read())
        with self.assertRaises(MigrationError):
            self.begin(quiescence_gate=gate)
        self.assertEqual(registry_revision(self.registry.read()), revision)
        self.assertEqual(json.loads(self.journal_path().read_text())['phase'], 'repair-required')

    def test_revision_conflict_and_nonempty_destination_preserve_user_files(self):
        self.destination.mkdir()
        sentinel = self.destination / 'user.txt'
        sentinel.write_bytes(b'user data')
        before = self.registry.path.read_bytes()
        with self.assertRaises(MigrationError):
            self.begin(expected_revision='0' * 64)
        with self.assertRaises((MigrationError, PathValidationError)):
            self.begin()
        self.assertEqual(sentinel.read_bytes(), b'user data')
        self.assertEqual(self.registry.path.read_bytes(), before)

    def test_overlap_and_registered_component_source_are_rejected(self):
        for destination in (self.source, self.source / 'nested target', self.source.parent):
            with self.subTest(destination=destination), self.assertRaises((MigrationError, PathValidationError)):
                begin_content_migration(self.registry, 'models', destination,
                    expected_revision=registry_revision(self.registry.read()), quiescence_gate=lambda: True)
        record = self.registry.read()
        record['components'].append(component_record('hub', self.source.parent))
        self.registry.write(record)
        with self.assertRaisesRegex(MigrationError, 'unsupported'):
            self.begin()

    def test_disk_full_before_copy_and_during_copy_preserves_registry(self):
        before = self.registry.path.read_bytes()
        with patch('services.shared.installation_paths.shutil.disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaises((MigrationError, PathValidationError)):
                self.begin()
        self.assertFalse(self.destination.exists())
        with patch('services.shared.content_migration.shutil.copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(MigrationError):
                self.begin()
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertTrue(self.source.exists())

    def test_source_or_destination_mutation_after_verification_prevents_switch(self):
        for target in ('source', 'destination'):
            with self.subTest(target=target):
                registry, source, destination = self.environment(self.root / target)
                before = registry.path.read_bytes()
                def mutate(phase, journal):
                    if phase == 'verified':
                        path = source if target == 'source' else destination
                        (path / 'model ü.gguf').write_bytes(b'mutated content')
                with self.assertRaises(MigrationError):
                    begin_content_migration(registry, 'models', destination,
                        expected_revision=registry_revision(registry.read()), quiescence_gate=lambda: True, fault=mutate)
                self.assertEqual(registry.path.read_bytes(), before)
                self.assertEqual(resolve_location(registry.read(), 'models'), source)

    def test_registry_change_before_switch_is_not_overwritten(self):
        def change(phase, journal):
            if phase == 'verified':
                self.registry.update(lambda current: {**current, 'preferences':
                    {**current['preferences'], 'show_client': False}})
        with self.assertRaises(MigrationError):
            self.begin(fault=change)
        self.assertFalse(self.registry.read()['preferences']['show_client'])
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.source)

    def test_destination_collision_after_plan_preserved(self):
        def collide(phase, journal):
            if phase == 'planned':
                self.destination.mkdir()
                (self.destination / 'user-file').write_bytes(b'keep me')
        with self.assertRaises(MigrationError):
            self.begin(fault=collide)
        self.assertEqual((self.destination / 'user-file').read_bytes(), b'keep me')
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.source)

    def test_post_switch_write_does_not_roll_back_or_delete_old_source(self):
        def stop(phase, journal):
            if phase == 'registry-switched':
                raise Crash()
        with self.assertRaises(Crash):
            self.begin(fault=stop)
        (self.destination / 'model ü.gguf').write_bytes(b'new enabled content')
        with self.assertRaises(MigrationError):
            resume_content_migration(self.registry, self.journal_path(), quiescence_gate=lambda: True)
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.destination)
        self.assertTrue(self.source.exists())
        self.assertEqual((self.destination / 'model ü.gguf').read_bytes(), b'new enabled content')

    def test_interrupted_partial_file_requires_repair_without_truncating_source(self):
        before = snapshot(self.source)
        def interrupted(reader, writer, **kwargs):
            writer.write(reader.read(5))
            writer.flush()
            raise Crash('power loss inside file copy')
        with patch('services.shared.content_migration.shutil.copyfileobj', side_effect=interrupted):
            with self.assertRaises(Crash):
                self.begin()
        with self.assertRaises(MigrationError):
            resume_content_migration(self.registry, self.journal_path(), quiescence_gate=lambda: True)
        self.assertEqual(snapshot(self.source), before)
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.source)
        self.assertTrue(list(self.destination.rglob('.orion-partial-*')))

    def test_pending_journal_blocks_second_migration(self):
        def stop(phase, journal):
            if phase == 'planned':
                raise Crash()
        with self.assertRaises(Crash):
            self.begin(fault=stop)
        with self.assertRaisesRegex(MigrationError, 'unfinished'):
            self.begin()

    def test_recovery_rechecks_destination_ownership_marker(self):
        def stop(phase, journal):
            if phase == 'verified':
                raise Crash()
        with self.assertRaises(Crash):
            self.begin(fault=stop)
        marker = self.destination / '.orion-content-migration.json'
        marker.write_text('{"operation_id":"foreign"}')
        with self.assertRaises(MigrationError):
            resume_content_migration(self.registry, self.journal_path(), quiescence_gate=lambda: True)
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.source)

    def test_inaccessible_directory_never_appears_to_be_empty(self):
        import os
        original = os.walk
        def denied(root, **kwargs):
            kwargs['onerror'](PermissionError('fixture directory denial'))
            yield from original(root, **kwargs)
        with patch('services.shared.content_migration.os.walk', side_effect=denied):
            with self.assertRaises(MigrationError):
                self.begin()
        self.assertFalse(self.destination.exists())
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.source)

    def test_completed_journal_never_reverts_subsequent_target_writes(self):
        self.begin()
        (self.destination / 'new-user-file').write_bytes(b'new content')
        result = resume_content_migration(self.registry, self.journal_path(), quiescence_gate=lambda: True)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual((self.destination / 'new-user-file').read_bytes(), b'new content')
        self.assertEqual(resolve_location(self.registry.read(), 'models'), self.destination)

    def test_completed_content_can_be_migrated_again_without_deleting_history(self):
        self.begin()
        second = self.root / 'Second models location'
        before = snapshot(self.destination)
        result = begin_content_migration(self.registry, 'models', second,
            expected_revision=registry_revision(self.registry.read()), quiescence_gate=lambda: True)
        self.assertEqual(result['phase'], 'completed')
        self.assertEqual(resolve_location(self.registry.read(), 'models'), second)
        self.assertEqual(snapshot(self.destination), before)
        self.assertEqual(len(list((self.registry.path.parent / 'migrations').glob('*.json'))), 2)

    def test_symlink_tree_and_destination_rejected(self):
        linked = self.source / 'link'
        try:
            linked.symlink_to(self.source / 'model ü.gguf')
        except OSError as exc:
            self.skipTest('This Windows account cannot create symbolic links: ' + str(exc))
        with self.assertRaises((MigrationError, PathValidationError)):
            self.begin()
        linked.unlink()
        self.destination.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((MigrationError, PathValidationError)):
            self.begin()

    def test_untrusted_journal_path_and_unsafe_inventory_rejected(self):
        def stop(phase, journal):
            if phase == 'planned':
                raise Crash()
        with self.assertRaises(Crash):
            self.begin(fault=stop)
        path = self.journal_path()
        journal = json.loads(path.read_text())
        journal['inventory']['files']['../outside'] = {'size': 0, 'sha256': '0' * 64}
        path.write_text(json.dumps(journal))
        with self.assertRaises(MigrationError):
            resume_content_migration(self.registry, path, quiescence_gate=lambda: True)
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(journal))
        with self.assertRaises(MigrationError):
            resume_content_migration(self.registry, outside, quiescence_gate=lambda: True)


if __name__ == '__main__':
    unittest.main()
