"""Build immutable SDK-free Windows x64 CPU packages from tracked source.

Run on Windows. Python/Redis inputs are pinned in inputs.json; PostgreSQL is
prepared with the canonical UTF-8 transformer before the archive is finalized.
No keys, DBs, model weights, worktree-only files or build SDKs are copied.
"""
import argparse
import hashlib
import inspect
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from services.shared.release_packages import RUNTIME_REFERENCE, archive_name, extract_zip, sha256, validate_release
from services.shared.windows_postgres import provision_runtime, verify_unicode_runtime, ARCHIVE_SHA256
from python_runtime import install_site_hook
import build_cache
import zip_layers
from timing import Timings


EXCLUDED_PARTS = {'data', 'persistent', 'models', '.venv', 'node_modules', '.git', '__pycache__'}


def release_names(root, prefixes):
    """Tracked files that belong in a package (relative POSIX names), links rejected."""
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode('utf-8').split('\0')
    selected = []
    for name in filter(None, names):
        if not any(name == prefix or name.startswith(prefix + '/') for prefix in prefixes):
            continue
        path = archive_name(name)
        # Tracked defaults/examples are allowed; actual mutable installation data
        # is never a release input, even if accidentally added to Git.
        if (any(part in EXCLUDED_PARTS for part in path.parts)
                or name.endswith(('.log', '.pid', '.manual', '.pyc', '.key'))
                or path.name in {'.env', '.env.global.local'} or 'files/security' in name):
            continue
        source = root / name
        if source.is_symlink() or getattr(source.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked release source rejected')
        selected.append(name)
    return selected


def lf_in_git(root):
    """Tracked names whose committed form is LF text (`git ls-files --eol`: i/lf)."""
    listing = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--eol', '-z']).decode('utf-8')
    return {entry.split('\t', 1)[1] for entry in filter(None, listing.split('\0'))
            if entry.split()[0] == 'i/lf'}


def copy_source(source, target, lf):
    """A source file as git stores it: the checkout's line endings (core.autocrlf, editors) never reach
    the package, so the same commit gives the same bytes on every PC."""
    data = Path(source).read_bytes()
    Path(target).write_bytes(data.replace(b'\r\n', b'\n') if lf else data)


def tracked(root, prefixes, stage):
    lf = lf_in_git(root)
    for name in release_names(root, prefixes):
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_source(root / name, target, name in lf)


def drop_entry_point_launchers(site_packages):
    """uv writes console-script launchers (bin/*.exe) that embed the absolute build path, a random
    temporary folder included: machine-specific, never used (Orion runs `python -m`), and the reason two
    builds of the same inputs differed. Removed, and their lines dropped from each RECORD."""
    site_packages = Path(site_packages)
    shutil.rmtree(site_packages / 'bin', ignore_errors=True)
    for record in sorted(site_packages.glob('*.dist-info/RECORD')):
        lines = record.read_bytes().split(b'\n')
        kept = [line for line in lines if not line.startswith((b'bin/', b'../../bin/'))]
        if kept != lines:
            record.write_bytes(b'\n'.join(kept))


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


# Bump when the runtime layer recipe changes in a way the function source alone would not show.
RUNTIME_RECIPE = 1
SOURCE_PREFIXES = {
    'hub': ['services/shared', 'services/hub/src', 'services/hub/run_local.py',
            'services/hub/pyproject.toml', 'services/.env.global', 'local_db_setup.py', 'installer/app'],
    'router': ['core', 'bin', 'api', 'database', 'providers', 'main.py', 'cli.py',
               'dynamic_router.py', 'pyproject.toml', '.env.example'],
}
HERE = Path(__file__).resolve().parent


def pins():
    return json.loads((HERE / 'inputs.json').read_text())


def runtime_pins(kind):
    names = ('python', 'redis') if kind == 'hub' else ('python', 'ffmpeg')
    return {name: pins()[name] for name in names}


def lock_file(kind, requirements=None):
    return requirements or HERE / (kind + '-requirements.txt')


def build_runtime(kind, args, destination):
    """The runtime layer: bundled Python + locked wheels, PostgreSQL, Redis (hub) / FFmpeg (router).

    Everything here depends only on pinned inputs, the requirements lock and this recipe, never
    on the package's own sources; that is what lets it be built once and reused."""
    destination.mkdir(parents=True)
    pin = pins()
    for name in ('python', 'redis') if kind == 'hub' else ('python',):
        item = pin[name]
        path = args.inputs / item['filename']
        if sha256(path) != item['sha256']:
            raise ValueError('Build input hash mismatch: ' + name)
        extract_zip(path, destination / ('runtime/python' if name == 'python' else '.local_db/redis'))
    python = destination / 'runtime/python'
    (python / 'python311._pth').write_text(
        'python311.zip\n.\nsite-packages\n..\\..\n..\\..\\services\\hub\\src\n..\\..\\installer\nimport site\n', encoding='utf-8')
    # Frozen, hashed wheel-only dependencies; no source compiler needed
    # on build or end-user machine and no venv absolute paths shipped.
    subprocess.run(['uv', 'pip', 'install', '--python', str(python / 'python.exe'),
        '--target', str(python / 'site-packages'), '--no-compile-bytecode', '--link-mode=copy',
        '--only-binary', ':all:', '--require-hashes', '-r', str(lock_file(kind, args.requirements))], check=True)
    drop_entry_point_launchers(python / 'site-packages')
    install_site_hook(python)
    pg = destination / ('.local_db/postgres' if kind == 'hub' else 'tools/pgsql')
    provision_runtime(args.postgres, pg)
    verify_unicode_runtime(pg)
    if kind == 'router':
        (destination / 'tools/pgsql.ready').touch()
        media = pin['ffmpeg']
        media_archive = args.inputs / media['filename']
        if sha256(media_archive) != media['sha256']:
            raise ValueError('FFmpeg input hash mismatch')
        with tempfile.TemporaryDirectory(prefix='.orion-media-', dir=destination.parent) as scratch:
            expanded = extract_zip(media_archive, Path(scratch) / 'media')
            roots = list(expanded.iterdir())
            if len(roots) != 1 or not (roots[0] / 'bin/ffmpeg.exe').is_file() or not (roots[0] / 'bin/ffprobe.exe').is_file():
                raise ValueError('Unexpected FFmpeg runtime layout')
            shutil.copytree(roots[0], destination / 'tools/ffmpeg')
        # Disabled legacy self-updater cannot Git/pip-update immutable code.
        (destination / 'Run-Router.ps1').write_text(
            'param([Parameter(Mandatory=$true)][string]$Registry, [switch]$Stop)\n'
            '$ErrorActionPreference="Stop"\n$env:ORION_INSTALLATION_REGISTRY=$Registry\n'
            '$action=if($Stop){"stop"}else{"start"}\n'
            '& "$PSScriptRoot/runtime/python/python.exe" -m core.lifecycle_cli $action\nexit $LASTEXITCODE\n')
    (destination / 'runtime-inputs.json').write_text(json.dumps(pin, indent=2) + '\n')


RUNTIME_KEEP = 3  # runtime layers kept per kind (most recently used first)


def runtime_key(kind, args):
    lock = lock_file(kind, args.requirements)
    return build_cache.combine(
        ('kind', kind), ('pins', runtime_pins(kind)), ('postgres', ARCHIVE_SHA256),
        ('lock', build_cache.text_digest(lock)), ('recipe', RUNTIME_RECIPE),
        ('recipe-code', hashlib.sha256(inspect.getsource(build_runtime).encode()).hexdigest()),
        ('python-runtime', build_cache.text_digest(HERE / 'python_runtime.py')),
        ('postgres-code', build_cache.text_digest(ROOT / 'services/shared/windows_postgres.py')))


def prepare_runtime(kind, args, timings, scratch_parent):
    """Return the prepared runtime layer directory (tree/ + layer.zip), building it once."""
    key = runtime_key(kind, args)
    parent = (args.cache / 'runtime') if args.cache else scratch_parent
    parent.mkdir(parents=True, exist_ok=True)
    base = parent / f'{kind}-{key[:20]}'
    if (base / 'complete').is_file():
        os.utime(base / 'complete')  # most recently used: kept by the pruning below
        timings.skipped('runtime layer', 'cached ' + key[:12])
        return base
    with timings.step('runtime layer (python+wheels, postgres, ...)', 'built once per input set'):
        scratch = Path(tempfile.mkdtemp(prefix='.layer-', dir=parent))
        try:
            build_runtime(kind, args, scratch / 'tree')
            zip_layers.write_layer(scratch / 'tree', scratch / 'layer.zip')
            (scratch / 'complete').write_text(key)
            if base.exists():
                shutil.rmtree(base)
            os.replace(scratch, base)
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
    if args.cache:
        # Keep a few recent layers per kind: worktrees/branches with different locks share one cache and
        # must not evict each other's layer (a rebuild costs 1-1.5 minutes).
        layers = sorted((p for p in parent.glob(kind + '-*') if p != base and (p / 'complete').is_file()),
                        key=lambda p: (p / 'complete').stat().st_mtime, reverse=True)
        for old in layers[RUNTIME_KEEP - 1:]:
            shutil.rmtree(old, ignore_errors=True)
    return base


def layer_description(kind, base):
    """Target description of the prepared runtime layer; its name carries its content hash.

    The same layer bytes give the same name in every release, so the publisher skips an online copy and an
    installed PC that already has it downloads only the code package."""
    archive, record = base / 'layer.zip', base / 'layer.json'
    signature = build_cache.stat_signature(archive)
    try:
        value = json.loads(record.read_text(encoding='utf-8'))
        if value['signature'] != signature:
            raise ValueError
        digest = value['sha256']
    except (OSError, ValueError, KeyError):
        digest = sha256(archive)
        record.write_text(json.dumps({'signature': signature, 'sha256': digest}), encoding='utf-8')
    return {'path': f'orion-{kind}-runtime-{digest[:16]}.zip', 'length': signature['size'],
            'hashes': {'sha256': digest},
            'custom': {'schema': 1, 'layer': 'runtime', 'for': kind, 'inputs': (base / 'complete').read_text().strip()}}


def write_layer(output, base, description):
    """Put the layer candidate (zip + .target.json) next to its code package."""
    archive = output / description['path']
    if not archive.exists():
        build_cache.link_or_copy(base / 'layer.zip', archive)
    archive.with_suffix('.target.json').write_text(json.dumps(description, indent=2) + '\n', encoding='utf-8')


def reference(description):
    return {'target': description['path'], 'sha256': description['hashes']['sha256'], 'length': description['length']}


def package_key(kind, release, args, root):
    """(key, parts): everything that can change this package's bytes."""
    tooling = build_cache.hash_files({name: (ROOT / name) for name in (
        'scripts/release/build_package.py', 'scripts/release/zip_layers.py', 'scripts/release/python_runtime.py',
        'scripts/release/inputs.json', 'services/shared/windows_postgres.py',
        'services/shared/release_packages.py')})
    parts = [('kind', kind), ('release', release), ('tooling', tooling)]
    if kind == 'client':
        web = root / 'main_app/app/build/web'
        parts += [('web', build_cache.hash_tree(web)),
                  ('serve_web', (build_cache.text_digest if 'main_app/app/tool/serve_web.py' in lf_in_git(root)
                                 else build_cache.file_digest)(root / 'main_app/app/tool/serve_web.py'))]
    else:
        names = release_names(root, SOURCE_PREFIXES[kind])
        # The bytes the package gets (see copy_source): an autocrlf (CRLF) and an LF checkout of one commit
        # have the same key, and the after-staging check below compares like with like.
        parts += [('sources', build_cache.hash_files({n: root / n for n in names}, text=lf_in_git(root))),
                  ('runtime', runtime_key(kind, args))]
        if kind == 'router':
            parts.append(('dashboard', build_cache.hash_tree(root / 'dashboard/out')))
    return build_cache.combine(*parts), dict(parts)


def build(args, timings=None):
    if os.name != 'nt':
        raise ValueError('Windows x64 build host required')
    timings = timings or Timings('package')
    root, output = args.source.resolve(), args.output.resolve()
    args.output = output
    args.cache = args.cache.resolve() if args.cache else None
    release = validate_release(json.loads(args.release.read_text(encoding='utf-8')))
    kind = release['component']
    if release['package_id'] != f"orion-{kind}-{release['version']}-windows-x86_64-cpu":
        raise ValueError('Package id must bind component, version and build profile')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (release['package_id'] + '.zip')
    if archive.exists():
        raise ValueError('Immutable artifact already exists; assign a new version')
    cache = build_cache.Cache(args.cache) if args.cache else None
    if kind == 'client':
        # Build is an explicit earlier CI step; never copy a stale source tree.
        web = root / 'main_app/app/build/web'
        if not (web / 'index.html').is_file():
            raise ValueError('flutter build web must succeed first')
        verify_no_service_worker(web)
    elif kind == 'router' and not (root / 'dashboard/out/index.html').is_file():
        raise ValueError('Static Router dashboard must be built first')
    key = parts = None
    if cache is not None:
        with timings.step('cache key (hash sources, release json, inputs, tooling)'):
            key, parts = package_key(kind, release, args, root)
        hit = cache.lookup_package(kind, key)
        layer = None
        if hit is not None and kind != 'client':
            # The code package pins its runtime layer: reuse it only together with exactly that layer.
            base = prepare_runtime(kind, args, timings, output / '.runtime')
            layer = layer_description(kind, base)
            if json.loads(hit[1].read_text(encoding='utf-8')).get('runtime') != reference(layer):
                hit = None
        if hit is not None:
            build_cache.link_or_copy(hit[0], archive)
            shutil.copyfile(hit[1], archive.with_suffix('.target.json'))
            if layer is not None:
                write_layer(output, base, layer)
            timings.skipped('package build', f'{kind} inputs unchanged ({key[:12]}); reused identical ZIP')
            print(archive.with_suffix('.target.json').read_text(encoding='utf-8').replace('\n', ' '))
            return archive
    with tempfile.TemporaryDirectory(prefix='.orion-build-', dir=output) as temporary:
        stage = Path(temporary) / 'tree'
        stage.mkdir()
        with timings.step('stage sources'):
            if kind == 'client':
                shutil.copytree(web, stage / 'build/web')
                (stage / 'tool').mkdir()
                copy_source(root / 'main_app/app/tool/serve_web.py', stage / 'tool/serve_web.py',
                            'main_app/app/tool/serve_web.py' in lf_in_git(root))
            else:
                # New sources are intentionally required to be tracked by CI. Local
                # builds must use a source commit (or explicitly stage task files).
                tracked(root, SOURCE_PREFIXES[kind], stage)
                if kind == 'router':
                    shutil.copytree(root / 'dashboard/out', stage / 'dashboard/out')
        if parts is not None and kind != 'client':
            # Hash what was actually copied: catches an edit that raced the key computation.
            names = release_names(root, SOURCE_PREFIXES[kind])
            if build_cache.hash_files({n: stage / n for n in names}, text=lf_in_git(root)) != parts['sources']:
                raise ValueError('Sources changed while the package was being built; retry')
        (stage / 'orion-release.json').write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
        if kind != 'client':
            base = prepare_runtime(kind, args, timings, Path(temporary) / 'runtime')
            # Split package: the runtime is its own signed target (see layer_description), not inside this ZIP.
            layer = layer_description(kind, base)
            write_layer(output, base, layer)
            # Inside the code ZIP too: its hash then changes with the runtime (a new Python library alone).
            (stage / RUNTIME_REFERENCE).write_text(json.dumps(reference(layer), indent=2) + '\n', encoding='utf-8')
            with timings.step('runtime smoke test (bundled Python imports the package)'):
                package_import = 'services.shared.process_lifecycle' if kind == 'hub' else 'core.process_lifecycle'
                subprocess.run([str(base / 'tree/runtime/python/python.exe'), '-c',
                    'import sys; sys.path[:0] = sys.argv[1:]; '
                    'import struct,fastapi,asyncpg,cryptography,tuf,psutil; '
                    'assert sys.version_info[:2] == (3,11) and struct.calcsize("P") == 8; '
                    'assert sys.dont_write_bytecode and sys.stdout.encoding.lower().replace("-", "") == "utf8"; '
                    'print("Türkçe 中文"); import ' + package_import,
                    str(stage), str(stage / 'services/hub/src'), str(stage / 'installer')], cwd=stage, check=True)
        # TUF signs these final bytes only after UTF-8 preparation and all builds.
        unfinished = Path(temporary) / archive.name
        with timings.step('write package ZIP', 'code only; the runtime layer is a separate target' if kind != 'client' else ''):
            sources = {p.relative_to(stage).as_posix(): p for p in stage.rglob('*') if p.is_file()}
            zip_layers.assemble(unfinished, sources)
        unfinished.rename(archive)
    with timings.step('hash package'):
        description = {'path': archive.name, 'length': archive.stat().st_size,
                       'hashes': {'sha256': sha256(archive)}, 'custom': release}
        if kind != 'client':
            description['runtime'] = reference(layer)
    target = archive.with_suffix('.target.json')
    target.write_text(json.dumps(description, indent=2) + '\n', encoding='utf-8')
    if cache is not None:
        cache.store_package(kind, key, archive, target)
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
    parser.add_argument('--cache', type=Path,
                        help='Build cache root (runtime layer + finished packages); omit to build everything fresh')
    arguments = parser.parse_args()
    timer = Timings('build ' + arguments.release.parent.name, stream=sys.stderr)
    try:
        build(arguments, timer)
    finally:
        timer.summary()
