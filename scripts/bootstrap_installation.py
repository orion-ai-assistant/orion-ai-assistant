"""Standalone source installer: validate destinations before downloading code.

This interim installer prepares a legacy source tree, not a packaged release.
The wrapper generator bundles the public registry validator with this module.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

run_command = subprocess.run

REPOSITORY = 'https://github.com/orion-ai-assistant/orion-ai-assistant.git'
MIN_FREE_BYTES = 512 * 1024 * 1024


class BootstrapError(ValueError):
    pass


def paths_api():
    bundled = globals().get('_BUNDLED_PATHS')
    if bundled is not None:
        return bundled
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    return vars(importlib.import_module('services.shared.installation_paths'))


def checked_path(value):
    path = paths_api()['canonical_path'](value)
    if not path.is_absolute() or '\x00' in str(value):
        raise BootstrapError('Choose an absolute local path')
    path = Path(os.path.normpath(str(path)))
    if os.name == 'nt':
        if str(path).startswith('\\\\') or ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) != 3:
            raise BootstrapError('Only available local fixed disks are supported')
        filesystem = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeInformationW(str(path.anchor), None, 0, None, None, None, filesystem, len(filesystem)) or filesystem.value.upper() not in {'NTFS', 'REFS'}:
            raise BootstrapError('Only NTFS/ReFS installation disks are supported')
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BootstrapError('Symlink/junction/reparse paths are not supported')
        if not item.is_dir():
            raise BootstrapError('Destination or its ancestor is not a directory')
    return path


def probe(path, minimum=MIN_FREE_BYTES):
    ancestor = path
    while not ancestor.exists():
        if ancestor == ancestor.parent:
            raise BootstrapError('Destination disk is unavailable')
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < minimum:
        raise BootstrapError('Insufficient space: source bootstrap needs at least 512 MiB; runtime/models need additional space')
    with tempfile.TemporaryFile(dir=ancestor) as stream:
        stream.write(b'orion destination write probe')
        stream.flush()
        os.fsync(stream.fileno())


def verify_project(path):
    for relative in ('manager.py', 'installer/pyproject.toml', 'services/hub/pyproject.toml', 'services/shared/installation_registry.py'):
        candidate = path / relative
        checked_path(str(candidate.parent))
        if not candidate.is_file() or candidate.is_symlink():
            raise BootstrapError('Registered Hub source is missing project files; repair required')
    try:
        import tomllib
        metadata = tomllib.loads((path / 'services/hub/pyproject.toml').read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise BootstrapError('Hub project metadata is invalid') from exc
    if metadata.get('project', {}).get('name') != 'orion-hub':
        raise BootstrapError('Directory is not an Orion Hub source installation')


def plan_installation(root=None, models_path=None, registry_path=None, registry_api=None, *, native=False):
    if registry_api is None:
        # Used by local development; standalone wrappers inject the bundled API.
        import runpy
        registry_api = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'services/shared/installation_registry.py'))
    registry_file = Path(registry_path) if registry_path else registry_api['default_registry_path']()
    if not registry_file.is_absolute():
        raise BootstrapError('Registry path must be absolute')
    checked_path(registry_file.parent)
    if registry_file.exists() and (registry_file.is_symlink() or getattr(registry_file.lstat(), 'st_file_attributes', 0) & 0x400):
        raise BootstrapError('Registry must not be a symlink')
    if not registry_file.exists() and registry_file.with_name(registry_file.name + '.bak').exists():
        raise BootstrapError('Registry primary is missing but a backup exists; explicit repair required')
    record = registry_api['Registry'](registry_file).read()
    if record is None and any((registry_file.parent / name).exists()
                              for name in ('hub-installation.json', 'router-installation.json')):
        raise BootstrapError('Legacy installation detected; explicitly import its manifest before installing. Existing files are preserved.')
    hubs = [item for item in (record or {}).get('components', []) if item['kind'] == 'hub']
    if len(hubs) > 1:
        raise BootstrapError('Multiple registered Hubs require explicit repair/selection')
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Orion' if os.name == 'nt' else Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'Orion'
    selected = checked_path(root or (record['root'] if record else str(base)))
    if record and root and os.path.normcase(str(selected)) != os.path.normcase(record['root']):
        raise BootstrapError('Changing a registered root requires controlled migration')
    existing = bool(hubs)
    if existing:
        hub = hubs[0]
        if hub['distribution'] not in {'legacy-source', 'development'}:
            raise BootstrapError('This source installer cannot modify packaged/container/remote Hubs')
        code = checked_path(hub['code_path'])
        if not code.is_dir():
            raise BootstrapError('Registered Hub disk/path is missing; repair required')
        verify_project(code)
    else:
        code = checked_path(str(selected / 'Hub'))
        if code.exists() and any(code.iterdir()):
            raise BootstrapError('Unregistered Hub target is occupied; no files will be overwritten')
    if native and os.name == 'nt':
        if sys.getwindowsversion().build < 18362:
            raise BootstrapError('Native installation requires Windows 10 1903 or newer')
        runtime = code / '.local_db/postgres'
        if existing and runtime.exists() and not str(code).isascii():
            api = globals().get('_BUNDLED_POSTGRES')
            if api is None:
                from services.shared import windows_postgres
                api = vars(windows_postgres)
            api['verify_unicode_runtime'](runtime)
    registered_models = (record or {}).get('locations', {}).get('models', str(selected / 'Models'))
    models = checked_path(models_path or registered_models)
    if record and models_path and os.path.normcase(str(models)) != os.path.normcase(registered_models):
        raise BootstrapError('Changing registered models requires controlled migration')
    if models == code or code in models.parents or models in code.parents:
        raise BootstrapError('Models must be separate from the Hub source tree')
    paths_api()['validate_private_directory'](code)
    for destination in (code, models):
        probe(destination)
    return dict(root=str(selected), code_path=str(code), models_path=str(models), registry_path=str(registry_file), reuse=existing,
                distribution='legacy-source', minimum_bootstrap_bytes=MIN_FREE_BYTES)


def main(argv=None, registry_api=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--models-path')
    parser.add_argument('--registry-path')
    parser.add_argument('--mode', choices=('local', 'docker'), default='docker')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required by this source installer')
    if not args.non_interactive and not args.plan_only and not args.root:
        args.root = input('Installation root (blank uses registered/default location): ').strip() or None
    try:
        plan = plan_installation(args.root, args.models_path, args.registry_path, registry_api, native=args.mode == 'local')
        print(json.dumps(plan, ensure_ascii=True))
        if args.plan_only:
            return 0
        if not args.non_interactive:
            if input('Proceed with this source installation? [y/N] ').strip().lower() != 'y':
                return 1
        code = Path(plan['code_path'])
        if not plan['reuse']:
            if not shutil.which('git'):
                raise BootstrapError('Git is required for this source bootstrap')
            code.parent.mkdir(parents=True, exist_ok=True)
            run_command(['git', 'clone', '--', REPOSITORY, str(code)], check=True)
            verify_project(code)
        environment = dict(os.environ, ORION_INSTALL_ROOT=plan['root'], ORION_MODELS_PATH=plan['models_path'], ORION_INSTALLATION_REGISTRY=plan['registry_path'], ORION_BOOTSTRAP_REGISTER='1')
        run_command([sys.executable, str(code / 'manager.py'), 'setup', args.mode], cwd=code, env=environment, check=True)
        run_command([sys.executable, str(code / 'manager.py'), 'installer', args.mode], cwd=code, env=environment, check=True)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print('Installation blocked: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
