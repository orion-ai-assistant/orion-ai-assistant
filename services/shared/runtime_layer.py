"""The bootstrap's runtime layer: `runtime/` of a bootstrap tree as one deterministic ZIP.

build_bootstrap.py writes it with the bundle's OWN Python (so zlib and zipfile are exactly the ones an installed
bootstrap runs on). A bootstrap that was installed from the combined bundle (first install, or adopted by an
updater that predates split bootstraps) has these files but not the ZIP: it rebuilds the ZIP from its own
`runtime/` and seeds the download cache with it, so the next bootstrap with the same runtime downloads only its
code. The rebuilt bytes are used only when their hash is the signed one. Standard library only.
"""
from pathlib import Path
import zipfile

LEVEL = 6
DATE = (2026, 1, 1, 0, 0, 0)


def members(root):
    """{archive name: path} of runtime/ under a bootstrap tree. __pycache__ is never part of the layer (the
    runtime does not write bytecode, but a stray one must not change what is rebuilt)."""
    root = Path(root)
    found = {}
    for path in (root / 'runtime').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            found[path.relative_to(root).as_posix()] = path
    return found


def write(root, archive):
    """Same member order, ZipInfo and deflate level as scripts/release/zip_layers.py."""
    found = members(root)
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=LEVEL) as stream:
        for name in sorted(found, key=lambda n: tuple(part.lower() for part in n.split('/'))):
            info = zipfile.ZipInfo(name, DATE)
            info.external_attr = 0o100644 << 16
            stream.writestr(info, found[name].read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=LEVEL)
    return Path(archive)


if __name__ == '__main__':
    import sys
    write(sys.argv[1], sys.argv[2])
