"""Check the independent Router's vendored engine; --write syncs canonical files.

The Router is a product of its own (its own repository, its own package, it runs without the Hub), so it ships
its own copy of the engine files both share. They are written here only (services/shared); the Router's copies
are made from them: by release.py at the start of every release round, or by hand with --write.

A copy is replaced only when it is an older version of the canonical file. One that was edited in the Router's
repository (the Router is ahead) is never overwritten: the sync stops and names it, so the edit is brought into the
canonical file first (--force replaces it anyway).
"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('process_lifecycle.py', 'process_child.py', 'process_child.cjs', 'native_postgres.py',
         'windows_job.py', 'process_host.py', 'windows_postgres.py', 'runtime_layout.py', 'release_packages.py',
         'installation_registry.py', 'installation_paths.py', 'update_gate.py', 'postgres_auth.py')


def _for_router(name, source):
    if name == 'native_postgres.py':
        source = source.replace(b'from services.shared.process_lifecycle import', b'from core.process_lifecycle import')
        source = source.replace(b'from services.shared.windows_postgres import', b'from core.windows_postgres import')
    return source


def vendored(name, canonical=None):
    """The bytes the Router's copy of `name` must have: the canonical file, its imports of the Hub's package
    pointed at the Router's."""
    return _for_router(name, ((canonical or ROOT / 'services/shared') / name).read_bytes())


def edited_in_router(name, destination, repository=ROOT):
    """True when the Router's copy of `name` is no version the canonical file ever had (line endings aside):
    somebody changed it in the Router's repository, and replacing it would lose that change. An older copy, a
    missing one and one that matches are not."""
    target = Path(destination) / name
    if not target.exists():
        return False
    plain = lambda data: data.replace(b'\r\n', b'\n')
    copy = plain(target.read_bytes())
    if copy == plain(vendored(name)):
        return False
    path = f'services/shared/{name}'
    revisions = subprocess.run(['git', '-C', str(repository), 'log', '--format=%H', '--', path],
                               capture_output=True, text=True, check=True).stdout.split()
    for revision in revisions:
        old = subprocess.run(['git', '-C', str(repository), 'show', f'{revision}:{path}'], capture_output=True)
        if old.returncode == 0 and plain(_for_router(name, old.stdout)) == copy:
            return False
    return True


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


AHEAD = ('The Router is ahead: {} in orion-router/core was edited there and is no version of the file in '
         'services/shared. Bring that change into services/shared first (then the copies match), or replace the '
         "Router's copy with sync_router_engine.py --write --force.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--force', action='store_true', help='with --write: also replace a copy edited in the Router')
    args = parser.parse_args()
    destination = ROOT.parent / 'orion-router/core'
    if not (destination / 'local_installation.py').is_file():
        raise SystemExit('Expected sibling Router checkout missing')
    if args.write and not args.force:
        edited = [name for name in sync(destination) if edited_in_router(name, destination)]
        if edited:
            raise SystemExit(AHEAD.format(', '.join(edited)))
    different = sync(destination, write=args.write)
    if different and not args.write:
        raise SystemExit('Router engine out of sync: ' + ', '.join(different))
    if different:
        print('Synced into the Router: ' + ', '.join(different))
    print(f'Canonical and independently vendored Router engine match ({len(NAMES)} files).')


if __name__ == '__main__':
    main()
