"""User-facing uninstall: scopes, data/model choices, safety boundaries, firewall tolerance, Apps entry."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from test_multi_feed import feeds  # noqa: F401  (fixture)
from test_add_components import setup, items  # noqa: F401  (fixtures/helpers)
from services.shared import uninstall as U
from services.shared.component_setup import KEPT_MARKER

REAL_REGISTER, REAL_UNREGISTER = U.register_entry, U.unregister_entry  # the fixture replaces the module attributes


class Firewall:
    """Stand-in for services.shared.windows_firewall: no UAC, no netsh."""
    def __init__(self, answer='removed'):
        self.answer, self.calls = answer, []

    def tcp_ports(self, record):
        return [9443] if any(c['kind'] == 'router' for c in record['components']) else []

    def ensure_rules(self, record):
        self.calls.append(('ensure', sorted(c['kind'] for c in record['components'])))
        return self.answer

    def remove_rules(self):
        self.calls.append(('remove',))
        return self.answer


@pytest.fixture
def world(setup, monkeypatch):
    monkeypatch.setattr('services.shared.launcher.install_entry', lambda registry, desktop=True: None)
    monkeypatch.setattr(U, 'register_entry', lambda *a, **k: True)
    monkeypatch.setattr(U, 'unregister_entry', lambda **k: True)
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(setup.registry.path))
    setup.links = []
    setup.firewall = Firewall()
    root = setup.root
    # What must never be touched: the developer registrations and the lifecycle test area.
    (root / 'hub-installation.json').write_text('{"dev": "hub"}')
    (root / 'router-installation.json').write_text('{"dev": "router"}')
    (root / 'lifecycle-tests').mkdir()
    (root / 'lifecycle-tests/keep.txt').write_text('test area')

    def run(scope, **options):
        options.setdefault('stop', lambda registry: None)
        options.setdefault('links', lambda registry, installation: setup.links.append(installation) or [])
        options.setdefault('firewall_api', setup.firewall)
        options.setdefault('background', False)
        return U.uninstall_orion(setup.registry, scope, **options)
    setup.run = run
    return setup


def tree(root):
    return sorted(str(p.relative_to(root)).replace(os.sep, '/') for p in root.rglob('*') if p.is_file())


def dev_files_intact(root):
    assert (root / 'hub-installation.json').read_text() == '{"dev": "hub"}'
    assert (root / 'router-installation.json').read_text() == '{"dev": "router"}'
    assert (root / 'lifecycle-tests/keep.txt').read_text() == 'test area'


def full(world):
    record = world.fresh(['hub', 'client', 'router'])
    got = items(record)
    (Path(got['hub']['data_path']) / 'chat.manual').write_bytes(b'chats')
    (Path(got['router']['data_path']) / 'keys.manual').write_bytes(b'router keys')
    models = Path(record['locations']['models'])
    (models / 'weights.gguf').write_bytes(b'model')
    return record


def test_everything_keeping_data_removes_code_links_registry_and_background_state_but_not_data(world):
    record = full(world)
    secret = (world.root / 'Hub/data/services/.env.global.local').read_text()
    result = world.run('all')
    assert result['status'] == 'uninstalled' and result['data'] == 'kept' and result['models'] == 'kept'
    assert sorted(result['removed']) == ['client', 'hub', 'router']
    left = tree(world.root)
    assert 'Hub/data/chat.manual' in left and 'Router/data/keys.manual' in left and 'Models/weights.gguf' in left
    assert f'Hub/data/{KEPT_MARKER}' in left and f'Router/data/{KEPT_MARKER}' in left
    assert not any(n.startswith(('Hub/releases', 'Hub/state', 'Router/releases', 'Router/state', 'Client',
                                 'bootstrap', 'updates', 'release-cache', 'install.json')) for n in left)
    assert world.links == [record['installation_id']]
    assert world.firewall.calls == [('remove',)]   # Nothing needs the port rule any more.
    dev_files_intact(world.root)
    assert (world.root / 'Hub/data/services/.env.global.local').read_text() == secret


def test_a_later_install_adopts_the_kept_data_and_its_secrets(world):
    full(world)
    secret = (world.root / 'Hub/data/services/.env.global.local').read_text()
    world.run('all')
    record = world.fresh(['hub', 'client', 'router'])
    assert (world.root / 'Hub/data/services/.env.global.local').read_text() == secret
    assert (world.root / 'Hub/data/chat.manual').read_bytes() == b'chats'
    assert (world.root / 'Router/data/keys.manual').read_bytes() == b'router keys'
    assert not (world.root / f'Hub/data/{KEPT_MARKER}').exists()
    assert set(items(record)) == {'hub', 'client', 'router'}


def test_deleting_data_and_models_is_explicit_and_bounded_to_the_installation_root(world):
    record = full(world)
    outside = world.root.parent / 'outside-models'
    outside.mkdir(exist_ok=True)
    (outside / 'big.gguf').write_bytes(b'x')
    record_models = Path(record['locations']['models'])
    world.registry.update(lambda r: {**r, 'locations': {**r['locations'], 'downloads': str(outside)}})
    result = world.run('all', keep_data=False, keep_models=False)
    assert result['data'] == 'deleted' and result['models'] == 'deleted'
    left = tree(world.root)
    assert not any(n.startswith(('Hub/', 'Router/', 'Models/')) for n in left)
    assert not record_models.exists()
    assert (outside / 'big.gguf').exists()                      # Outside the root: never deleted.
    assert any('dışındaki' in note for note in result['notes'])
    dev_files_intact(world.root)


def test_hub_only_keeps_the_router_running_install_and_shrinks_the_firewall_rule(world):
    record = full(world)
    router_before = items(record)['router']
    result = world.run('hub')
    assert sorted(result['removed']) == ['client', 'hub']
    after = world.registry.read()
    assert set(items(after)) == {'router'} and items(after)['router'] == router_before
    assert not any(n.startswith(('Hub/releases', 'Hub/state', 'Client')) for n in tree(world.root))
    assert (world.root / 'Hub/data/chat.manual').exists()       # Kept.
    assert Path(router_before['code_path']).is_dir() and (world.root / 'Router/data/keys.manual').exists()
    assert world.firewall.calls == [('ensure', ['router'])]
    assert (world.registry.path.parent / 'bootstrap/trusted-root.json').exists()   # Router still updates itself.


def test_router_only_removal_leaves_hub_and_client_untouched(world):
    record = full(world)
    before = {k: v for k, v in items(record).items() if k != 'router'}
    world.run('router')
    after = items(world.registry.read())
    assert {k: after[k] for k in ('hub', 'client')} == before
    assert world.firewall.calls == [('ensure', ['client', 'hub'])] or world.firewall.calls == [('remove',)]


def test_declined_or_broken_firewall_change_never_fails_the_uninstall(world):
    full(world)
    world.firewall.answer = 'declined'
    result = world.run('all')
    assert result['status'] == 'uninstalled' and result['firewall'] == 'declined'
    world2 = world
    full(world2)

    class Broken(Firewall):
        def remove_rules(self):
            raise OSError('no powershell')
    result = world.run('all', firewall_api=Broken())
    assert result['status'] == 'uninstalled' and result['firewall'] == 'failed'


def test_refusals_change_nothing(world):
    full(world)
    snapshot = world.registry.path.read_bytes()
    with pytest.raises(U.UninstallError, match='Bilinmeyen'):
        world.run('everything-else')
    with pytest.raises(U.UninstallError, match='kapatılamadı'):
        world.run('all', stop=lambda registry: (_ for _ in ()).throw(RuntimeError('busy')))
    assert world.registry.path.read_bytes() == snapshot
    world.run('all')
    with pytest.raises(U.UninstallError, match='bulunamadı'):
        world.run('all')


def test_scope_must_exist_in_this_installation(world):
    world.fresh(['router'])
    with pytest.raises(U.UninstallError, match='yok'):
        world.run('hub')


def test_source_or_development_installations_are_preserved(world):
    world.fresh(['router'])
    world.registry.update(lambda r: {**r, 'components': [{**{k: v for k, v in r['components'][0].items() if k != 'package'},
                                                         'distribution': 'development', 'ownership': 'external',
                                                         'management': 'connect-only'}]})
    with pytest.raises(U.UninstallError, match='dokunulmaz'):
        world.run('all')


def test_settings_apps_entry_names_the_product_and_runs_the_safe_dialog(world, monkeypatch):
    record = world.fresh(['hub', 'client', 'router'])
    values = U.entry_values(world.registry, record)
    assert values['DisplayName'] == 'Orion AI Assistant'
    assert 'Orion-Uninstall.ps1' in values['UninstallString'] and str(world.registry.path) in values['UninstallString']
    assert values['DisplayIcon'].endswith('Orion.exe,0') and values['DisplayVersion'] == '1.0.0'
    router_only = {**record, 'components': [c for c in record['components'] if c['kind'] == 'router']}
    assert U.entry_values(world.registry, router_only)['DisplayName'] == 'Orion Router'


@pytest.mark.skipif(os.name != 'nt', reason='Windows registry')
def test_entry_is_written_to_and_removed_from_the_registry_under_a_disposable_key(world, monkeypatch):
    import winreg
    world.fresh(['router'])
    key = r'Software\OrionTest\Uninstall-' + os.urandom(4).hex()
    try:
        assert REAL_REGISTER(world.registry, key=key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            assert winreg.QueryValueEx(handle, 'DisplayName')[0] == 'Orion Router'
            assert winreg.QueryValueEx(handle, 'NoModify')[0] == 1
        assert (world.registry.path.parent / 'bootstrap/Orion-Uninstall.ps1').is_file()
        assert REAL_UNREGISTER(key=key)
        assert not REAL_UNREGISTER(key=key)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


@pytest.mark.skipif(os.name != 'nt', reason='Windows file locking and PowerShell')
def test_everything_with_data_deleted_leaves_no_orion_folder_after_the_deferred_cleanup(world, monkeypatch):
    import stat
    import subprocess
    full(world)
    root = world.root
    # The real-PC layout: the retained installer running the uninstall, read-only release files, state markers.
    running = root / 'Bootstrap-Installer-s9-467062f2'
    (running / 'runtime').mkdir(parents=True)
    (root / 'release-cache-router').mkdir()
    readonly = next(p for p in (root / 'Hub/releases').rglob('*') if p.is_file())
    readonly.chmod(stat.S_IREAD)
    (root / 'Hub/state/cancel-1.marker').write_text('x')
    held = open(running / 'runtime/python.exe', 'w')  # Python's open() does not share delete: the folder is held.
    scheduled, run_once = [], []
    real = U.schedule_removal
    monkeypatch.setattr(U, 'schedule_removal', lambda paths, root: scheduled.append(
        real(paths, root=root, wait=[], later=[], folder=world.root.parent / 'temp', spawn=False, run_once=run_once.append))
        or scheduled[-1])
    try:
        result = world.run('all', keep_data=False, keep_models=False)
    finally:
        held.close()
    assert result['leftovers'] == ['Bootstrap-Installer-s9-467062f2'] and result['scheduled'] is True
    assert result['notes'] == []    # The removed releases go with their folders: no purge, no scary note.
    assert run_once and 'orion-cleanup-' in run_once[0]     # Next sign-in repeats it if the run below is killed.
    assert sorted(p.name for p in root.iterdir()) == ['Bootstrap-Installer-s9-467062f2', 'hub-installation.json',
                                                      'lifecycle-tests', 'router-installation.json']
    subprocess.run([U._powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                    str(scheduled[0])], check=True, timeout=60)
    assert sorted(p.name for p in root.iterdir()) == ['hub-installation.json', 'lifecycle-tests', 'router-installation.json']
    dev_files_intact(root)
    assert not scheduled[0].exists() and not scheduled[0].with_suffix('.json').exists()


def test_a_fresh_install_over_the_leftovers_of_a_removed_install_succeeds(world):
    record = full(world)
    root = world.root
    old_package = Path(items(record)['hub']['code_path']).name
    # What the user's PC kept after an uninstall without registry: code, state, old installer, caches, no data.
    world.registry.path.unlink()
    if world.registry.backup_path.exists():
        world.registry.backup_path.unlink()
    import shutil
    for kind in ('Hub', 'Router', 'Client'):
        shutil.rmtree(root / kind / 'data')
    (root / 'Bootstrap-Installer-s9-467062f2/runtime').mkdir(parents=True)
    (root / 'Bootstrap-Installer').mkdir()
    (root / 'release-cache-router').mkdir()
    (root / 'Hub/state/child-1.json').write_text('{}')
    record = world.fresh(['hub', 'client', 'router'])
    assert set(items(record)) == {'hub', 'client', 'router'}
    assert [p.name for p in (root / 'Hub/releases').iterdir()] == [old_package]
    assert not (root / 'Hub/state/child-1.json').exists()
    assert not any(p.name.startswith('Bootstrap-Installer') for p in root.iterdir())
    assert (root / 'release-cache-router').is_dir()                 # TUF cache: reused.
    dev_files_intact(root)
    # Something Orion does not own in a component folder is still refused, nothing deleted.
    world.registry.path.unlink()
    if world.registry.backup_path.exists():
        world.registry.backup_path.unlink()
    shutil.rmtree(root / 'Router/data')
    (root / 'Router/notes.txt').write_text('mine')
    with pytest.raises(ValueError):
        world.fresh(['router'])
    assert (root / 'Router/notes.txt').read_text() == 'mine' and any((root / 'Router/releases').iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell')
def test_the_cleanup_starts_when_the_uninstaller_exits_not_when_the_result_window_closes(tmp_path, monkeypatch):
    import subprocess
    import sys
    spawned = []
    monkeypatch.setattr(U.subprocess, 'Popen', lambda arguments, **kw: spawned.append(arguments))
    root = tmp_path / 'Orion'
    (root / 'Bootstrap-Installer-s10-abc/runtime').mkdir(parents=True)
    script = U.schedule_removal([root / 'Bootstrap-Installer-s10-abc'], root=root, folder=tmp_path, run_once=lambda c: None)
    monkeypatch.undo()
    arguments = spawned[0]
    assert arguments[arguments.index('-Pids') + 1] == str(os.getpid())
    assert arguments[arguments.index('-Later') + 1] == str(os.getppid())
    uninstaller = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1)'])
    dialog = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        subprocess.run([U._powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                        str(script), '-Pids', str(uninstaller.pid), '-Later', str(dialog.pid)], check=True, timeout=60)
        assert not root.exists() and dialog.poll() is None   # Removed while the result window is still open.
    finally:
        dialog.kill()
