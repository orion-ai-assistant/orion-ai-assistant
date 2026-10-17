"""Build immutable SDK-free Windows x64 CPU packages from tracked source.

Run on Windows. Python/Redis inputs are pinned in inputs.json; PostgreSQL is
prepared with the canonical UTF-8 transformer before the archive is finalized.
No keys, DBs, model weights, worktree-only files or build SDKs are copied.
"""
import argparse
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.release_packages import archive_name, extract_zip, sha256, validate_release
from services.shared.windows_postgres import provision_runtime, verify_unicode_runtime, ARCHIVE_SHA256
from python_runtime import install_site_hook


def tracked(root, prefixes, stage):
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode('utf-8').split('\0')
    for name in filter(None, names):
        if not any(name == prefix or name.startswith(prefix + '/') for prefix in prefixes):
            continue
        path = archive_name(name)
        # Tracked defaults/examples are allowed; actual mutable installation data
        # is never a release input, even if accidentally added to Git.
        if (any(part in {'data', 'persistent', 'models', '.venv', 'node_modules', '.git', '__pycache__'} for part in path.parts)
                or name.endswith(('.log', '.pid', '.manual', '.pyc', '.key'))
                or path.name in {'.env', '.env.global.local'} or 'files/security' in name):
            continue
        source, target = root / name, stage / name
        if source.is_symlink() or getattr(source.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked release source rejected')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def verify_no_service_worker(web):
    """The client must be built with `--pwa-strategy=none`.

    A Flutter service worker keeps serving its cached old bundle in the app window after an
    update; the packaged client therefore may ship neither an active worker nor the code
    that registers one."""
    web = Path(web)
    worker = web / 'flutter_service_worker.js'
    if worker.exists() and worker.read_text(encoding='utf-8', errors='replace').strip():
        raise ValueError('Client was built with a service worker; use flutter build web --pwa-strategy=none')
    bootstrap = (web / 'flutter_bootstrap.js').read_text(encoding='utf-8', errors='replace')
    if not re.search(r'_flutter\.loader\.load\(\s*\)', bootstrap):
        raise ValueError('Client bootstrap registers a service worker; use flutter build web --pwa-strategy=none')


def build(args):
    if os.name != 'nt':
        raise ValueError('Windows x64 build host required')
    root, output = args.source.resolve(), args.output.resolve()
    release = validate_release(json.loads(args.release.read_text(encoding='utf-8')))
    kind = release['component']
    if release['package_id'] != f"orion-{kind}-{release['version']}-windows-x86_64-cpu":
        raise ValueError('Package id must bind component, version and build profile')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (release['package_id'] + '.zip')
    if archive.exists():
        raise ValueError('Immutable artifact already exists; assign a new version')
    with tempfile.TemporaryDirectory(prefix='.orion-build-', dir=output) as temporary:
        stage = Path(temporary) / 'tree'
        stage.mkdir()
        if kind == 'client':
            # Build is an explicit earlier CI step; never copy a stale source tree.
            web = root / 'main_app/app/build/web'
            if not (web / 'index.html').is_file():
                raise ValueError('flutter build web must succeed first')
            verify_no_service_worker(web)
            shutil.copytree(web, stage / 'build/web')
            (stage / 'tool').mkdir()
            shutil.copyfile(root / 'main_app/app/tool/serve_web.py', stage / 'tool/serve_web.py')
        else:
            prefixes = (['services/shared', 'services/hub/src', 'services/hub/run_local.py',
                         'services/hub/pyproject.toml', 'services/.env.global', 'local_db_setup.py', 'installer/app']
                        if kind == 'hub' else ['core', 'bin', 'api', 'database', 'providers', 'main.py', 'cli.py',
                                              'dynamic_router.py', 'pyproject.toml', '.env.example'])
            tracked(root, prefixes, stage)
            # New sources are intentionally required to be tracked by CI. Local
            # builds must use a source commit (or explicitly stage task files).
            pins = json.loads((Path(__file__).with_name('inputs.json')).read_text())
            for name in ('python', 'redis') if kind == 'hub' else ('python',):
                item = pins[name]
                path = args.inputs / item['filename']
                if sha256(path) != item['sha256']:
                    raise ValueError('Build input hash mismatch: ' + name)
                destination = stage / ('runtime/python' if name == 'python' else '.local_db/redis')
                extract_zip(path, destination)
            python = stage / 'runtime/python'
            (python / 'python311._pth').write_text(
                'python311.zip\n.\nsite-packages\n..\\..\n..\\..\\services\\hub\\src\n..\\..\\installer\nimport site\n', encoding='utf-8')
            # Frozen, hashed wheel-only dependencies; no source compiler needed
            # on build or end-user machine and no venv absolute paths shipped.
            lock = args.requirements or Path(__file__).with_name(kind + '-requirements.txt')
            subprocess.run(['uv', 'pip', 'install', '--python', str(python / 'python.exe'),
                '--target', str(python / 'site-packages'), '--no-compile-bytecode',
                '--only-binary', ':all:', '--require-hashes', '-r', str(lock)], check=True)
            install_site_hook(python)
            pg = stage / ('.local_db/postgres' if kind == 'hub' else 'tools/pgsql')
            provision_runtime(args.postgres, pg)
            verify_unicode_runtime(pg)
            if kind == 'router':
                (stage / 'tools/pgsql.ready').touch()
                media = pins['ffmpeg']
                media_archive = args.inputs / media['filename']
                if sha256(media_archive) != media['sha256']:
                    raise ValueError('FFmpeg input hash mismatch')
                expanded = extract_zip(media_archive, Path(temporary) / 'media')
                roots = list(expanded.iterdir())
                if len(roots) != 1 or not (roots[0] / 'bin/ffmpeg.exe').is_file() or not (roots[0] / 'bin/ffprobe.exe').is_file():
                    raise ValueError('Unexpected FFmpeg runtime layout')
                shutil.copytree(roots[0], stage / 'tools/ffmpeg')
                dashboard = root / 'dashboard/out'
                if not (dashboard / 'index.html').is_file():
                    raise ValueError('Static Router dashboard must be built first')
                shutil.copytree(dashboard, stage / 'dashboard/out')
                # Disabled legacy self-updater cannot Git/pip-update immutable code.
                (stage / 'Run-Router.ps1').write_text(
                    'param([Parameter(Mandatory=$true)][string]$Registry, [switch]$Stop)\n'
                    '$ErrorActionPreference="Stop"\n$env:ORION_INSTALLATION_REGISTRY=$Registry\n'
                    '$action=if($Stop){"stop"}else{"start"}\n'
                    '& "$PSScriptRoot/runtime/python/python.exe" -m core.lifecycle_cli $action\nexit $LASTEXITCODE\n')
        (stage / 'orion-release.json').write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
        if kind != 'client':
            (stage / 'runtime-inputs.json').write_text(json.dumps(pins, indent=2) + '\n')
            package_import = 'services.shared.process_lifecycle' if kind == 'hub' else 'core.process_lifecycle'
            subprocess.run([str(stage / 'runtime/python/python.exe'), '-c',
                'import sys,struct,fastapi,asyncpg,cryptography,tuf,psutil; '
                'assert sys.version_info[:2] == (3,11) and struct.calcsize("P") == 8; '
                'assert sys.dont_write_bytecode and sys.stdout.encoding.lower().replace("-", "") == "utf8"; '
                'print("Türkçe 中文"); import ' + package_import], cwd=stage, check=True)
        # TUF signs these final bytes only after UTF-8 preparation and all builds.
        unfinished = Path(temporary) / archive.name
        with zipfile.ZipFile(unfinished, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                        raise ValueError('Link in final package tree')
                    name = path.relative_to(stage).as_posix()
                    archive_name(name)
                    info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100644 << 16
                    stream.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        unfinished.rename(archive)
    description = {'path': archive.name, 'length': archive.stat().st_size,
                   'hashes': {'sha256': sha256(archive)}, 'custom': release}
    archive.with_suffix('.target.json').write_text(json.dumps(description, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(description))
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--postgres', type=Path, required=True)
    parser.add_argument('--requirements', type=Path)
    build(parser.parse_args())
