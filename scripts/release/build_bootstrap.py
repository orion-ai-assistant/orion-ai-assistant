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
from services.shared.release_packages import extract_zip, sha256
from python_runtime import install_site_hook


def build(trusted_root, inputs, output, *, test_only=False):
    root = Metadata.from_file(str(trusted_root))
    if (not isinstance(root.signed, Root) or root.signed.expires <= datetime.now(timezone.utc)
            or not root.signed.consistent_snapshot):
        raise ValueError('Unexpired TUF trust root required')
    root.verify_delegate('root', root)
    if not test_only and (root.signed.roles['root'].threshold < 2 or len(root.signed.roles['root'].keyids) < 3):
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
            '--target', str(python / 'site-packages'), '--require-hashes', '--only-binary', ':all:',
            '-r', str(Path(__file__).with_name('requirements.txt'))], check=True)
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
        shutil.copyfile(ROOT / 'services/shared/launcher_entry.ps1',
                        tree / 'services/shared/launcher_entry.ps1')
        for name in ('install_new.py', 'update_local.py'):
            destination = tree / 'scripts/release' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).with_name(name), destination)
        shutil.copyfile(trusted_root, tree / 'trusted-root.json')
        (tree / 'bootstrap.json').write_text(json.dumps({'schema': 1, 'test_only': test_only,
            'root_sha256': sha256(trusted_root)}))
        (tree / 'Install-Orion.ps1').write_text(
            'param([Parameter(Mandatory=$true)][string]$MetadataUrl, '
            '[Parameter(Mandatory=$true)][string]$TargetsUrl, '
            '[Parameter(Mandatory=$true)][string[]]$Targets, '
            '[string]$Root=(Join-Path $env:LOCALAPPDATA "Orion"), [string]$Registry)\n'
            '$ErrorActionPreference="Stop"\n'
            'if (-not $Registry) { $Registry=Join-Path $env:LOCALAPPDATA "Orion/install.json" }\n'
            '$cache=Join-Path $env:LOCALAPPDATA "Orion/release-cache"\n'
            '& "$PSScriptRoot/runtime/python/python.exe" -B -m scripts.release.install_new '
            '--trusted-root "$PSScriptRoot/trusted-root.json" --cache $cache --metadata-url $MetadataUrl '
            '--targets-url $TargetsUrl --root $Root --registry $Registry @Targets\nexit $LASTEXITCODE\n')
        # The standard cache/registry is inside the default root. install_new
        # validates occupied component directories instead of requiring that this
        # bootstrap metadata parent be empty.
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as stream:
            for path in sorted(tree.rglob('*')):
                if path.is_file():
                    stream.write(path, path.relative_to(tree).as_posix())
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-root', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.trusted_root, args.inputs, args.output))
