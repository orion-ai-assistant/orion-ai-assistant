"""Orion.exe product identity: trusted retained launcher, product shortcuts, migration of existing links."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from services.shared import launcher
from services.shared.installation_registry import Registry, component_record, new_registry

ROOT = Path(__file__).resolve().parents[2]
EXE = b'MZ fake Orion.exe'


def read_link(path):
    command = ('$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:ORION_TEST_LINK); '
               '@($l.TargetPath,$l.Arguments,$l.Description,$l.IconLocation)|ConvertTo-Json')
    out = subprocess.check_output(['powershell', '-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ' + command],
                                  env=dict(os.environ, ORION_TEST_LINK=str(path)), encoding='utf-8-sig')
    return json.loads(out)


class ProductIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='identity-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        code = self.root / 'Hub'
        (code / 'services/hub').mkdir(parents=True)
        (code / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        self.store = Registry(self.root / 'Orion/install.json')
        self.store.write(new_registry(self.root, [component_record('hub', code, ownership='owned', management='local')]))
        self.bootstrap = self.store.path.parent / 'bootstrap'
        self.desktop = self.root / 'Desktop'
        self.startup = self.root / 'Startup'
        self.desktop.mkdir()
        self.startup.mkdir()
        folders = patch('services.shared.windows_autostart.known_folder',
                        side_effect=lambda name: {'desktop': self.desktop, 'startup': self.startup}[name])
        folders.start()
        self.addCleanup(folders.stop)

    def retained(self, exe=EXE, recorded=None):
        """A fake retained bootstrap that the registry's pointer selects."""
        tree = self.root / 'Bootstrap-Installer-s2'
        tree.mkdir(exist_ok=True)
        (tree / 'Orion.exe').write_bytes(exe)
        (tree / 'bootstrap.json').write_text(json.dumps({'launcher_sha256': recorded or hashlib.sha256(EXE).hexdigest()}))
        self.bootstrap.mkdir(exist_ok=True)
        (self.bootstrap / 'automatic-runtime.json').write_text(json.dumps({'schema': 1, 'root': str(tree)}))
        return tree

    def test_source_checkout_has_no_launcher_and_keeps_the_powershell_entry(self):
        self.assertIsNone(launcher.retained_launcher(self.store))
        entry = launcher.install_entry(self.store, desktop=False)
        self.assertFalse(entry.with_name('Orion.exe').exists())

    def test_only_a_launcher_matching_its_own_bootstrap_hash_is_trusted(self):
        self.retained(exe=b'tampered')
        self.assertIsNone(launcher.retained_launcher(self.store))
        self.retained(recorded='0' * 64)
        self.assertIsNone(launcher.retained_launcher(self.store))
        tree = self.retained()
        self.assertEqual(tree / 'Orion.exe', launcher.retained_launcher(self.store))

    def test_install_entry_places_orion_exe_next_to_the_entry_without_downloading(self):
        self.retained()
        with patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
            entry = launcher.install_entry(self.store, desktop=False)
        self.assertEqual(EXE, entry.with_name('Orion.exe').read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_desktop_and_startup_links_carry_name_exe_and_icon(self):
        self.retained()
        entry = launcher.install_entry(self.store)
        exe = str(entry.with_name('Orion.exe'))
        target, args, description, icon = read_link(self.desktop / 'Orion.lnk')
        self.assertEqual((exe.lower(), '', 'Orion AI Assistant'), (target.lower(), args, description))
        self.assertTrue(icon.lower().startswith(exe.lower()))
        launcher.preferences(self.store, start_with_windows=True, show_client=False, startup_directory=self.startup)
        target, args, description, icon = read_link(self.startup / 'Orion AI Assistant.lnk')
        self.assertEqual((exe.lower(), '-Startup', 'Orion AI Assistant'), (target.lower(), args, description))
        self.assertFalse((self.startup / 'Orion.lnk').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_existing_powershell_links_are_upgraded_and_startup_stays_off_when_disabled(self):
        from services.shared.windows_autostart import shortcut
        entry = launcher.install_entry(self.store, desktop=False)  # Pre-Orion.exe state.
        identity = self.store.read()['installation_id']
        shortcut(self.desktop / 'Orion.lnk', entry, identity)
        shortcut(self.startup / 'Orion.lnk', entry, identity, startup=True)
        self.assertTrue(read_link(self.desktop / 'Orion.lnk')[0].lower().endswith('powershell.exe'))
        self.retained()
        self.assertTrue(launcher.refresh_links(self.store))  # Preference is off by default.
        self.assertEqual(str(entry.with_name('Orion.exe')).lower(), read_link(self.desktop / 'Orion.lnk')[0].lower())
        self.assertEqual([], list(self.startup.iterdir()))  # Old link removed, none created for a user who turned it off.
        with patch('services.shared.windows_autostart.shortcut') as touched:
            self.assertFalse(launcher.refresh_links(self.store))  # Once per Orion.exe.
        touched.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_enabled_startup_moves_to_the_product_named_link_and_foreign_links_survive(self):
        from services.shared.windows_autostart import shortcut
        entry = launcher.install_entry(self.store, desktop=False)
        shortcut(self.startup / 'Orion.lnk', entry, self.store.read()['installation_id'], startup=True)
        self.store.update(lambda r: {**r, 'preferences': {**r['preferences'], 'start_with_windows': True}})
        self.retained()
        launcher.refresh_links(self.store)
        self.assertEqual(['Orion AI Assistant.lnk'], [p.name for p in self.startup.iterdir()])
        self.assertEqual(str(entry.with_name('Orion.exe')).lower(), read_link(self.startup / 'Orion AI Assistant.lnk')[0].lower())
        self.assertFalse((self.desktop / 'Orion.lnk').exists())  # A removed desktop link is not recreated.
        (self.startup / 'Orion.lnk').write_bytes(b'foreign')
        launcher.preferences(self.store, start_with_windows=False, show_client=True, startup_directory=self.startup)
        self.assertEqual(b'foreign', (self.startup / 'Orion.lnk').read_bytes())
        self.assertFalse((self.startup / 'Orion AI Assistant.lnk').exists())


if __name__ == '__main__':
    unittest.main()
