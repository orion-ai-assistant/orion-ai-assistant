"""Development checkout only: rebuild the Flutter web build and OrionUi.exe when their sources changed.

The same idea as Router's dashboard (orion-router/bin/dashboard_build.py): a start from source never shows an
old build. Each output records the fingerprint of the sources it was built from (paths, sizes, modification
times) in build/; a start compares and rebuilds only what changed. A packaged Client has no sources (no
pubspec.yaml / lib) and is never touched. A failed build leaves the previous one in place and is only logged.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

SKIP_DIRS = {'.dart_tool', '.git', 'build', '__pycache__', 'node_modules'}
WEB_MANIFEST = '.orion-web-build.json'
HOST_MANIFEST = '.orion-ui-host-build.json'


def is_checkout(app):
    return (Path(app) / 'pubspec.yaml').is_file() and (Path(app) / 'lib').is_dir()


def web_inputs(app):
    """The app's Dart code, web files and assets, and those of the local packages it builds with."""
    app = Path(app)
    roots = [app / 'lib', app / 'web', app / 'assets', app / 'pubspec.yaml', app / 'pubspec.lock']
    packages = app.parent.parent / 'packages'
    if packages.is_dir():
        for package in sorted(packages.iterdir()):
            if (package / 'pubspec.yaml').is_file():
                roots += [package / 'lib', package / 'assets', package / 'web', package / 'pubspec.yaml']
    return roots


def host_inputs(app):
    return [Path(app) / 'windows_host']


def fingerprint(roots):
    digest = hashlib.sha256()
    for root in roots:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = []
            for directory, dirs, names in os.walk(root):
                dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith('.')]
                files.extend(Path(directory) / name for name in names)
        else:
            continue
        for path in sorted(files):
            stat = path.stat()
            digest.update(f'{path.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n'.encode('utf-8'))
    return digest.hexdigest()


def _recorded(app, manifest):
    try:
        return json.loads((Path(app) / 'build' / manifest).read_text(encoding='utf-8')).get('fingerprint')
    except (OSError, ValueError, AttributeError):
        return None


def needs_build(app, manifest, inputs, output):
    return not Path(output).is_file() or _recorded(app, manifest) != fingerprint(inputs)


def _build(app, label, manifest, inputs, output, argv, log):
    if not needs_build(app, manifest, inputs, output):
        return False
    before = fingerprint(inputs)
    command = ' '.join([Path(argv[0]).name] + [Path(a).name if Path(a).is_file() else a for a in argv[1:]])
    log(f'[*] {label}: sources changed, rebuilding ({command})')
    try:
        result = subprocess.run(argv, cwd=str(app), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=900,
                                creationflags=0x08000000 if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        log(f'[!] {label}: build failed ({error}); the previous build stays')
        return False
    if result.returncode:
        tail = result.stdout.decode('utf-8', 'replace').strip().splitlines()[-15:]
        log(f'[!] {label}: build failed (exit {result.returncode}); the previous build stays\n' + '\n'.join(tail))
        return False
    if fingerprint(inputs) != before:
        # Edited while building: not recorded, so the next start builds again.
        log(f'[!] {label}: sources changed during the build; it is rebuilt on the next start')
        return True
    (Path(app) / 'build' / manifest).write_text(json.dumps({'fingerprint': before}), encoding='utf-8')
    log(f'[*] {label}: rebuilt')
    return True


def refresh(app, python, log):
    """Rebuild what is out of date in a development checkout; returns the labels rebuilt."""
    app = Path(app)
    if not is_checkout(app):
        return []
    rebuilt = []
    if os.name == 'nt' and (app / 'windows_host' / 'build_ui_host.py').is_file():
        if _build(app, 'OrionUi.exe', HOST_MANIFEST, host_inputs(app), app / 'build' / 'ui_host' / 'OrionUi.exe',
                  [python, str(app / 'windows_host' / 'build_ui_host.py')], log):
            rebuilt.append('OrionUi.exe')
    flutter = shutil.which('flutter')
    if flutter is None:
        if needs_build(app, WEB_MANIFEST, web_inputs(app), app / 'build' / 'web' / 'index.html'):
            log('[!] Flutter web: sources changed but flutter is not on PATH; the previous build is served')
    elif _build(app, 'Flutter web', WEB_MANIFEST, web_inputs(app), app / 'build' / 'web' / 'index.html',
                [flutter, 'build', 'web'], log):
        rebuilt.append('Flutter web')
    return rebuilt
