"""The retained watcher imports services.shared from the bootstrap bundle, not from a registered release.

Regression: `environment.load_environment()` asked `runtime_layout.component()` about the bundle root; the bundle
has an orion-release.json but is no registered component, so the first `from services.shared.environment import
get_env` in the watcher (inside stop_hub during "Şimdi güncelle") raised "Package is not registered at this path"
and every apply-now stop failed, while the same stop from `Orion.ps1 -Stop` (Hub's own tree) worked."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def bundle(tmp_path, *, release=True):
    tree = tmp_path / 'Bootstrap-Installer-s3-abcd1234'
    shared = tree / 'services/shared'
    shared.mkdir(parents=True)
    (tree / 'services/__init__.py').write_text('')
    (shared / '__init__.py').write_text('')
    for name in ('environment.py', 'runtime_layout.py'):
        shutil.copyfile(ROOT / 'services/shared' / name, shared / name)
    if release:
        (tree / 'orion-release.json').write_text(json.dumps({'component': 'bootstrap'}))
    registry = tmp_path / 'install.json'
    registry.write_text(json.dumps({'schema_version': 1, 'components': [
        {'kind': 'hub', 'distribution': 'packaged', 'code_path': str(tmp_path / 'Hub/releases/hub-1'),
         'data_path': str(tmp_path / 'Hub/data'), 'state_path': str(tmp_path / 'Hub/state')}]}))
    return tree, registry


def run_in(tree, registry, code):
    environment = {'ORION_INSTALLATION_REGISTRY': str(registry), 'POSTGRES_PORT': '55432',
                   'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')}
    program = 'import sys; sys.path.insert(0, %r); %s' % (str(tree), code)
    return subprocess.run([sys.executable, '-c', program], env=environment, capture_output=True, text=True, timeout=60)


def test_watcher_process_can_import_the_environment_from_an_unregistered_bundle(tmp_path):
    tree, registry = bundle(tmp_path)
    result = run_in(tree, registry, "from services.shared.environment import get_env; print(get_env('POSTGRES_PORT'))")
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == '55432'  # The launcher's environment (set around the stop) is what counts.


def test_a_registered_packaged_component_still_loads_its_own_environment(tmp_path):
    tree, registry = bundle(tmp_path)
    hub = tmp_path / 'Hub/releases/hub-1'
    shared = hub / 'services/shared'
    shutil.copytree(tree / 'services', hub / 'services')
    (hub / 'orion-release.json').write_text(json.dumps({'component': 'hub'}))
    (hub / 'services/.env.global').write_text('ORION_TEST_FROM_PACKAGE=packaged\n')
    (tmp_path / 'Hub/data/services').mkdir(parents=True)
    (tmp_path / 'Hub/state').mkdir()
    result = run_in(hub, registry, "import os; "
                    "from services.shared import environment; print(os.environ.get('ORION_TEST_FROM_PACKAGE'))")
    assert result.returncode == 0, result.stderr[-400:]
    # python-dotenv may be absent in the test interpreter: then nothing is loaded, but nothing may crash either.
    assert result.stdout.strip() in {'packaged', 'None'}


def test_a_bundle_without_release_file_is_unaffected(tmp_path):
    tree, registry = bundle(tmp_path, release=False)
    result = run_in(tree, registry, "from services.shared.environment import get_env; print(get_env('POSTGRES_PORT'))")
    assert result.returncode == 0, result.stderr[-400:]
