"""Build pgvector for the Hub's Windows PostgreSQL (MSVC) into the zip that services/shared/postgres_vector.py pins.

pgvector publishes no Windows binaries, so they are built here from the pinned source tarball against the
pinned PostgreSQL archive's own headers and postgres.lib (services/shared/windows_postgres.py). Run only when
pgvector or the PostgreSQL major version changes, on a PC with Visual Studio 2022 (C++ desktop workload):

    .release/tools/Scripts/python.exe scripts/release/build_pgvector.py --postgres .release/inputs/<pg zip>

It writes scripts/release/pgvector/pgvector-<version>-pg16-windows-x64.zip plus pgvector.json (sources, compiler,
file hashes) and prints the zip's SHA-256 for postgres_vector.ARCHIVE_SHA256. /Brepro keeps the DLL free of
build timestamps and the zip entries carry a fixed date, so a rebuild with the same compiler gives the same bytes.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared import postgres_vector
from services.shared.windows_postgres import ARCHIVE_SHA256 as POSTGRES_SHA256, VERSION as POSTGRES_VERSION

SOURCE_URL = f'https://github.com/pgvector/pgvector/archive/refs/tags/v{postgres_vector.VERSION}.tar.gz'
SOURCE_SHA256 = 'cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85'
SOURCE_COMMIT = 'f37c13f68b57d2c3472b2214fbcff699d6d34876'  # refs/tags/v0.8.7
OUTPUT = postgres_vector.ARCHIVE.parent
VSWHERE = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def vcvars():
    found = subprocess.run([str(VSWHERE), '-latest', '-products', '*', '-requires',
                            'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                           capture_output=True, text=True, check=True).stdout.strip()
    path = Path(found) / 'VC/Auxiliary/Build/vcvars64.bat'
    if not path.is_file():
        raise SystemExit('Visual Studio 2022 C++ build tools (vcvars64.bat) not found')
    return path


def postgres_headers(archive, root):
    """pgsql/include and pgsql/lib/postgres.lib of the pinned archive: what Makefile.win reads under PGROOT."""
    if sha256(archive) != POSTGRES_SHA256:
        raise SystemExit('PostgreSQL archive SHA256 mismatch')
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            name = PurePosixPath(member.filename)
            if '..' in name.parts or name.is_absolute():
                raise SystemExit('Unsafe PostgreSQL archive member')
            if name.parts[:2] == ('pgsql', 'include') or member.filename == 'pgsql/lib/postgres.lib':
                source.extract(member, root)
    return root / 'pgsql'


def source_tree(scratch, tarball=None):
    if tarball is None:
        tarball = scratch / 'pgvector.tar.gz'
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, tarball.open('wb') as output:
            shutil.copyfileobj(response, output)
    if sha256(tarball) != SOURCE_SHA256:
        raise SystemExit('pgvector source SHA256 mismatch')
    with tarfile.open(tarball) as source:
        source.extractall(scratch / 'src', filter='data')
    return scratch / 'src' / f'pgvector-{postgres_vector.VERSION}'


def build(source, pgroot, tools, scratch):
    # Header paths reach the DLL through __FILE__ (elog): /d1trimfile drops the random scratch folder (and with it
    # the user name), /Brepro the build timestamps. vcvars64 looks for vswhere on PATH, which only a Developer
    # Prompt has.
    # The prefix ends in a backslash; before the closing quote it is doubled, or cl reads \" as a quote.
    trim = '"/d1trimfile:' + str(scratch.resolve()) + '\\\\"'
    env = dict(os.environ, CL='/Brepro ' + trim, LINK='/Brepro',
               PATH=str(VSWHERE.parent) + os.pathsep + os.environ['PATH'])
    # One string: list2cmdline would escape the inner quotes, which cmd does not understand. /s strips the outer pair.
    # vcvars sets INCLUDE/LIB/PATH for this cmd only.
    subprocess.run(f'cmd /d /s /c "call "{tools}" >nul && nmake /nologo /F Makefile.win PGROOT="{pgroot}""',
                   cwd=source, env=env, check=True)
    banner = subprocess.run(f'cmd /d /s /c "call "{tools}" >nul && cl"', env=env, capture_output=True,
                            text=True, encoding='oem', errors='replace').stderr
    # The banner is translated ("Version", "Sürüm"): keep only its version number.
    return 'MSVC cl ' + re.search(r'\d+\.\d+\.\d+(\.\d+)?', banner).group(0) + ' x64'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--postgres', type=Path, required=True, help='the pinned PostgreSQL binaries zip')
    parser.add_argument('--source', type=Path, help='pgvector tarball already downloaded (hash-checked)')
    args = parser.parse_args()
    tools = vcvars()
    with tempfile.TemporaryDirectory(prefix='orion-pgvector-') as scratch:
        scratch = Path(scratch)
        pgroot = postgres_headers(args.postgres, scratch / 'pg')
        source = source_tree(scratch, args.source)
        compiler = build(source, pgroot, tools, scratch)
        files = {
            'lib/vector.dll': source / 'vector.dll',
            'share/extension/vector.control': source / 'vector.control',
            f'share/extension/vector--{postgres_vector.VERSION}.sql': source / f'sql/vector--{postgres_vector.VERSION}.sql',
            'pgvector-license.txt': source / 'LICENSE',
        }
        assert tuple(files) == postgres_vector.FILES
        OUTPUT.mkdir(parents=True, exist_ok=True)
        archive = postgres_vector.ARCHIVE
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for name, path in files.items():
                entry = zipfile.ZipInfo(name, ZIP_DATE)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                output.writestr(entry, path.read_bytes())
        receipt = {
            'pgvector': postgres_vector.VERSION, 'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
            'source_commit': SOURCE_COMMIT, 'postgres': POSTGRES_VERSION, 'postgres_archive_sha256': POSTGRES_SHA256,
            'compiler': compiler, 'flags': 'Makefile.win defaults (/O2 /fp:fast, x64 baseline) + /Brepro /d1trimfile',
            'archive': archive.name, 'archive_sha256': sha256(archive),
            'files': {name: sha256(path) for name, path in files.items()},
        }
        (OUTPUT / 'pgvector.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(archive)
    print('ARCHIVE_SHA256 =', receipt['archive_sha256'])


if __name__ == '__main__':
    main()
