"""Build the out-of-band trusted installer with a bundled TUF trust root.

The resulting ZIP is an installer distribution, NOT a TUF trust bootstrap over
the same untrusted channel. Production delivery requires an authenticated channel
and Windows signing approval; no production root/key is invented by this tool.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tuf.api.metadata import Metadata, Root
from services.shared.release_packages import archive_name, extract_zip, sha256, validate_release
from python_runtime import install_site_hook
from build_launcher import build_launcher
from build_package import drop_entry_point_launchers


def build(trusted_root, inputs, output, *, test_only=False, release_source=None, single_key=False, release=None):
    """Also writes <output>.target.json: the bundle is a signed TUF target (component `bootstrap`)
    so installed bootstraps can update themselves through the ordinary ReleaseClient."""
    release = validate_release(json.loads(Path(release or Path(__file__).with_name('bootstrap.json')).read_text(encoding='utf-8')))
    if release['component'] != 'bootstrap' or release['package_id'] != f"orion-bootstrap-{release['version']}-windows-x86_64-cpu":
        raise ValueError('bootstrap.json must describe the bootstrap component and bind its version in the package id')
    root = Metadata.from_file(str(trusted_root))
    if (not isinstance(root.signed, Root) or root.signed.expires <= datetime.now(timezone.utc)
            or not root.signed.consistent_snapshot):
        raise ValueError('Unexpired TUF trust root required')
    root.verify_delegate('root', root)
    if single_key:
        from release_keys import single_key_root
        if not single_key_root(root):
            raise ValueError('--single-key requires the one-key root created by release_keys.py init')
    elif not test_only and (root.signed.roles['root'].threshold < 2 or len(root.signed.roles['root'].keyids) < 3):
        raise ValueError('Production bootstrap requires 2-of-3 offline root policy')
    output = Path(output)
    if output.exists():
        raise ValueError('Bootstrap destination already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.orion-bootstrap-') as temporary:
        tree = Path(temporary) / 'tree'
        pin = json.loads(Path(__file__).with_name('inputs.json').read_text())['python']
        archive = Path(inputs) / pin['filename']
        if sha256(archive) != pin['sha256']:
            raise ValueError('Python bootstrap input hash mismatch')
        extract_zip(archive, tree / 'runtime/python')
        python = tree / 'runtime/python'
        (python / 'python311._pth').write_text('python311.zip\n.\nsite-packages\n..\\..\nimport site\n')
        subprocess.run(['uv', 'pip', 'install', '--python', str(python / 'python.exe'),
            '--target', str(python / 'site-packages'), '--no-compile-bytecode', '--require-hashes', '--only-binary', ':all:',
            '-r', str(Path(__file__).with_name('requirements.txt'))], check=True)
        # Console-script launchers embed this temporary folder's path: without this, two builds of the same
        # inputs differ and release.cmd could never recognise an unchanged bootstrap.
        drop_entry_point_launchers(python / 'site-packages')
        install_site_hook(python)
        # Recovery runs from this independently retained bootstrap, even if the
        # candidate Hub cannot import or its release tree is unavailable. Never
        # replace the executing bootstrap; prepare a new installer alongside it.
        for source in (ROOT / 'services/shared').glob('*.py'):
            name = source.name
            destination = tree / 'services/shared' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'services/shared' / name, destination)
        # The helper installs this stable guard before its first transaction.
        # It must also be present when recovery runs without a working Hub.
        for script in ('launcher_entry.ps1', 'tray.ps1', 'updating.ps1', 'uninstall.ps1', 'orion_ui.ps1'):
            shutil.copyfile(ROOT / 'services/shared' / script, tree / 'services/shared' / script)
        for name in ('install_new.py', 'update_local.py', 'update_automatic.py', 'manage_installation.py'):
            destination = tree / 'scripts/release' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).with_name(name), destination)
        # Product identity: Orion.exe (name + icon in Startup apps/desktop) compiled from source with the
        # .NET Framework csc.exe that ships with Windows. install_entry copies it from here, never downloads it.
        launcher = build_launcher(tree / 'Orion.exe', release['version'])
        # Router-only installations carry their own product name in the exe's version resource.
        router_launcher = build_launcher(tree / 'OrionRouter.exe', release['version'], 'Orion Router')
        shutil.copyfile(trusted_root, tree / 'trusted-root.json')
        if release_source is not None:
            from services.shared.automatic_updates import validate_source
            value = validate_source(json.loads(Path(release_source).read_text(encoding='utf-8')))
            (tree / 'release-source.json').write_text(json.dumps(value), encoding='utf-8')
        (tree / 'bootstrap.json').write_text(json.dumps({'schema': 1, 'test_only': test_only,
            'root_sha256': sha256(trusted_root), 'sequence': release['sequence'],
            'package_id': release['package_id'], 'version': release['version'],
            'launcher_sha256': sha256(launcher),
            'router_launcher_sha256': sha256(router_launcher)}))
        (tree / 'orion-release.json').write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
        (tree / 'Install-Orion.ps1').write_text(
            'param([string]$MetadataUrl, [string]$TargetsUrl, [string[]]$Targets, [string[]]$Components, '
            '[switch]$AddMissing, '
            '[string]$Root=(Join-Path $env:LOCALAPPDATA "Orion"), [string]$Registry, '
            '[switch]$NoStart, [switch]$Background, [switch]$NoStartWithWindows, [switch]$NoAutoUpdate, '
            '[switch]$NoFirewall, [switch]$NoWindow)\n'
            '$ErrorActionPreference="Stop"\n'
            'if (-not $Registry) { $Registry=Join-Path $env:LOCALAPPDATA "Orion/install.json" }\n'
            '$cache=Join-Path $env:LOCALAPPDATA "Orion/release-cache"\n'
            '$launch=@(); if (-not $NoStart) { $launch += "--launch" }; '
            'if ($Background) { $launch += "--background" }; '
            'if ($NoStartWithWindows) { $launch += "--no-start-with-windows" }; '
            'if ($NoAutoUpdate) { $launch += "--no-auto-update" }; '
            'if ($NoFirewall) { $launch += "--no-firewall" }; '
            'if ($NoWindow) { $launch += "--no-window" }\n'
            '# Components: install from the feeds listed in this bundle (release-source.json), one TUF cache per feed.\n'
            '# AddMissing: an installation exists; add only what is not registered yet and leave the rest untouched.\n'
            'if ($Components) {\n'
            '  $feeds=@("--release-source", "$PSScriptRoot/release-source.json", "--components") + $Components\n'
            '  if ($AddMissing) { $feeds += "--add-missing" }\n'
            '  & "$PSScriptRoot/runtime/python/python.exe" -B -m scripts.release.install_new '
            '--trusted-root "$PSScriptRoot/trusted-root.json" --root $Root --registry $Registry @launch @feeds\n'
            '} else {\n'
            '  & "$PSScriptRoot/runtime/python/python.exe" -B -m scripts.release.install_new '
            '--trusted-root "$PSScriptRoot/trusted-root.json" --cache $cache --metadata-url $MetadataUrl '
            '--targets-url $TargetsUrl --root $Root --registry $Registry @launch @Targets\n'
            '}\nexit $LASTEXITCODE\n')
        # The standard cache/registry is inside the default root. install_new
        # validates occupied component directories instead of requiring that this
        # bootstrap metadata parent be empty.
        # Fixed timestamps and order: the same inputs give the same bytes, so a republished
        # feed keeps the signed hash of an unchanged bundle.
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as stream:
            for path in sorted(tree.rglob('*')):
                if path.is_file():
                    name = path.relative_to(tree).as_posix()
                    archive_name(name)
                    info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100644 << 16
                    stream.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    description = {'path': output.name, 'length': output.stat().st_size,
                   'hashes': {'sha256': sha256(output)}, 'custom': release}
    output.with_suffix('.target.json').write_text(json.dumps(description, indent=2) + '\n', encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-root', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-source', type=Path)
    parser.add_argument('--release', type=Path, help='Bootstrap release json (default: bootstrap.json)')
    parser.add_argument('--single-key', action='store_true', help='Accept the one-key root from release_keys.py')
    args = parser.parse_args()
    print(build(args.trusted_root, args.inputs, args.output, release_source=args.release_source,
                single_key=args.single_key, release=args.release))
