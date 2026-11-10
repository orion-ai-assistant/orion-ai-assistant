"""Check the independent Router's vendored engine; --write syncs canonical files.

The Router is a product of its own (its own repository, its own package, it runs without the Hub), so it ships
its own copy of the engine files both share. They are written here only (services/shared); the Router's copies
are made from them: by release.py at the start of every release round, or by hand with --write.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('process_lifecycle.py', 'process_child.py', 'process_child.cjs', 'native_postgres.py',
         'windows_job.py', 'process_host.py', 'windows_postgres.py', 'runtime_layout.py', 'release_packages.py',
         'installation_registry.py', 'installation_paths.py', 'update_gate.py', 'postgres_auth.py')


def vendored(name, canonical=None):
    """The bytes the Router's copy of `name` must have: the canonical file, its imports of the Hub's package
    pointed at the Router's."""
    source = ((canonical or ROOT / 'services/shared') / name).read_bytes()
    if name == 'native_postgres.py':
        source = source.replace(b'from services.shared.process_lifecycle import', b'from core.process_lifecycle import')
        source = source.replace(b'from services.shared.windows_postgres import', b'from core.windows_postgres import')
    return source


def sync(destination, *, write=False, canonical=None):
    """The names whose Router copy differs from the canonical file; with `write` they are replaced first (and the
    names that were replaced are returned)."""
    different = []
    for name in NAMES:
        source, target = vendored(name, canonical), Path(destination) / name
        if not target.exists() or target.read_bytes() != source:
            different.append(name)
            if write:
                target.write_bytes(source)
    return different


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    destination = ROOT.parent / 'orion-router/core'
    if not (destination / 'local_installation.py').is_file():
        raise SystemExit('Expected sibling Router checkout missing')
    different = sync(destination, write=args.write)
    if different and not args.write:
        raise SystemExit('Router engine out of sync: ' + ', '.join(different))
    if different:
        print('Synced into the Router: ' + ', '.join(different))
    print(f'Canonical and independently vendored Router engine match ({len(NAMES)} files).')


if __name__ == '__main__':
    main()
