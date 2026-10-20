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
        self.programs = self.root / 'Programs'
        self.desktop.mkdir()
        self.startup.mkdir()
        self.programs.mkdir()
        folders = patch('services.shared.windows_autostart.known_folder',
                        side_effect=lambda name: {'desktop': self.desktop, 'startup': self.startup,
                                                  'programs': self.programs}[name])
        folders.start()
        self.addCleanup(folders.stop)
        # Never touch the real HKCU StartupApproved values from a unit test.
        self.approved = patch('services.shared.windows_autostart.clean_startup_approved', return_value=[])
        self.cleaned = self.approved.start()
        self.addCleanup(self.approved.stop)
        self.arp = patch('services.shared.uninstall.register_entry', return_value=True)
        self.arp.start()
        self.addCleanup(self.arp.stop)

    def retained(self, exe=EXE, recorded=None, router_exe=None):
        """A fake retained bootstrap that the registry's pointer selects."""
        tree = self.root / 'Bootstrap-Installer-s2'
        tree.mkdir(exist_ok=True)
        (tree / 'Orion.exe').write_bytes(exe)
        info = {'launcher_sha256': recorded or hashlib.sha256(EXE).hexdigest()}
        if router_exe is not None:
            (tree / 'OrionRouter.exe').write_bytes(router_exe)
            info['router_launcher_sha256'] = hashlib.sha256(router_exe).hexdigest()
        (tree / 'bootstrap.json').write_text(json.dumps(info))
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
        # An existing install gets its Start menu entry (search, "Recent", pin to Start) on the next launch.
        self.assertEqual(['Orion AI Assistant.lnk'], [p.name for p in self.programs.iterdir()])
        self.assertEqual(str(entry.with_name('Orion.exe')).lower(), read_link(self.programs / 'Orion AI Assistant.lnk')[0].lower())
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

    def router_only(self):
        code = self.root / 'Router'
        code.mkdir(exist_ok=True)
        (code / 'pyproject.toml').write_text('[project]\nname="orion-router"', encoding='utf-8')
        self.store.write(new_registry(self.root, [component_record('router', code, ownership='owned', management='local')]))

    def test_router_only_installation_has_its_own_identity_and_launcher(self):
        self.router_only()
        self.assertEqual('Orion Router', launcher.identity(self.store.read())['product'])
        self.assertEqual('Orion Router.lnk', launcher.identity(self.store.read())['desktop'])
        self.retained(router_exe=b'MZ router launcher')
        self.assertEqual(b'MZ router launcher', launcher.retained_launcher(self.store).read_bytes())
        entry = launcher.install_entry(self.store, desktop=False)
        self.assertEqual(b'MZ router launcher', entry.with_name('Orion.exe').read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows COM link')
    def test_router_only_links_are_named_orion_router_and_hub_addition_swaps_them(self):
        self.router_only()
        self.retained(router_exe=b'MZ router launcher')
        launcher.install_entry(self.store)
        self.assertEqual('Orion Router', read_link(self.desktop / 'Orion Router.lnk')[2])
        self.assertEqual(['Orion Router.lnk'], [p.name for p in self.programs.iterdir()])
        self.assertFalse((self.desktop / 'Orion.lnk').exists())
        launcher.preferences(self.store, start_with_windows=True, show_client=True, startup_directory=self.startup)
        self.assertEqual(['Orion Router.lnk'], [p.name for p in self.startup.iterdir()])
        # Hub is added later: the same links become "Orion AI Assistant" (no stale Router link, no duplicate).
        hub = self.root / 'Hub'
        (hub / 'services/hub').mkdir(parents=True, exist_ok=True)
        (hub / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"', encoding='utf-8')
        self.store.update(lambda r: {**r, 'components': [*r['components'],
                                     component_record('hub', hub, ownership='owned', management='local')]})
        self.retained(router_exe=b'MZ router launcher')
        launcher.install_entry(self.store)
        launcher.refresh_links(self.store)
        self.assertEqual(['Orion.lnk'], [p.name for p in self.desktop.iterdir()])
        self.assertEqual(['Orion AI Assistant.lnk'], [p.name for p in self.startup.iterdir()])
        self.assertEqual('Orion AI Assistant', read_link(self.startup / 'Orion AI Assistant.lnk')[2])
        self.assertEqual(['Orion AI Assistant.lnk'], [p.name for p in self.programs.iterdir()])  # Swapped, not added.

    def test_stale_startup_approved_values_of_vanished_links_are_cleaned_on_refresh(self):
        self.retained()
        launcher.refresh_links(self.store)
        keep, stale = self.cleaned.call_args.args
        self.assertEqual(set(), set(keep))
        self.assertEqual({'Orion.lnk', 'Orion AI Assistant.lnk', 'Orion Router.lnk'}, set(stale))


@unittest.skipUnless(os.name == 'nt', 'Windows registry')
class StartupApprovedTests(unittest.TestCase):
    """A disposable HKCU subkey stands in for Explorer's StartupApproved StartupFolder key."""
    def setUp(self):
        import winreg
        self.winreg = winreg
        self.key = 'Software\\OrionTest\\StartupApproved-' + os.urandom(4).hex()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key) as handle:
            for name in ('Orion.lnk', 'Orion AI Assistant.lnk', 'Other App.lnk'):
                winreg.SetValueEx(handle, name, 0, winreg.REG_BINARY, bytes([2] + [0] * 11))
        self.addCleanup(lambda: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key))

    def names(self):
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key) as handle:
            return sorted(self.winreg.EnumValue(handle, i)[0] for i in range(self.winreg.QueryInfoKey(handle)[1]))

    def test_only_orions_vanished_links_lose_their_state_foreign_and_present_stay(self):
        from services.shared.windows_autostart import clean_startup_approved
        removed = clean_startup_approved({'Orion AI Assistant.lnk'},
                                         ['Orion.lnk', 'Orion AI Assistant.lnk', 'Orion Router.lnk'], key=self.key)
        self.assertEqual(['Orion.lnk'], removed)
        self.assertEqual(['Orion AI Assistant.lnk', 'Other App.lnk'], self.names())
        self.assertEqual([], clean_startup_approved(set(), ['Orion.lnk'], key=self.key))  # idempotent

    def test_missing_key_is_not_an_error(self):
        from services.shared.windows_autostart import clean_startup_approved
        self.assertEqual([], clean_startup_approved(set(), ['Orion.lnk'], key=self.key + '-none'))


if __name__ == '__main__':
    unittest.main()
