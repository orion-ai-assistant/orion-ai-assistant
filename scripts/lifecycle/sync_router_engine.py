"""Check the independent Router's vendored engine; --write syncs canonical files."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    destination = ROOT.parent / 'orion-router/core'
    if not (destination / 'local_installation.py').is_file():
        raise SystemExit('Expected sibling Router checkout missing')
    failed = []
    names = ('process_lifecycle.py', 'process_child.py', 'process_child.cjs', 'native_postgres.py',
             'windows_job.py', 'process_host.py', 'windows_postgres.py', 'runtime_layout.py', 'release_packages.py',
             'installation_registry.py', 'installation_paths.py')
    for name in names:
        source = (ROOT / 'services/shared' / name).read_bytes()
        if name == 'native_postgres.py':
            source = source.replace(b'from services.shared.process_lifecycle import', b'from core.process_lifecycle import')
            source = source.replace(b'from services.shared.windows_postgres import', b'from core.windows_postgres import')
        target = destination / name
        if args.write:
            target.write_bytes(source)
        if not target.exists() or target.read_bytes() != source:
            failed.append(name)
    if failed:
        raise SystemExit('Router engine out of sync: ' + ', '.join(failed))
    print(f'Canonical and independently vendored Router engine match ({len(names)} files).')


if __name__ == '__main__':
    main()
