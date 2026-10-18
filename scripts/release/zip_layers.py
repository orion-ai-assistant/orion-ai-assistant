"""Deterministic package ZIPs assembled from a precompressed runtime layer plus fresh sources.

The runtime (embedded Python + site-packages, PostgreSQL, Redis, FFmpeg) is about 90 % of a
package and identical between releases. It is deflated once into `layer.zip`; each package
then copies those already-compressed members verbatim and deflates only the changed sources.
The result is byte-identical to compressing the whole tree in one pass (same member order,
same ZipInfo, same deflate level), so layering changes build time, never the artifact.
"""
from pathlib import Path
import struct
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.release_packages import archive_name

LEVEL = 6
DATE = (2026, 1, 1, 0, 0, 0)


def member_info(name):
    info = zipfile.ZipInfo(name, DATE)
    info.external_attr = 0o100644 << 16
    return info


def order_key(name):
    """Same order `sorted(Path.rglob)` gave on Windows: case-insensitive path components."""
    return tuple(part.lower() for part in name.split('/'))


def reject_links(path):
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Link in final package tree')


def tree_members(directory):
    directory = Path(directory)
    members = {}
    for path in directory.rglob('*'):
        if path.is_file():
            reject_links(path)
            name = path.relative_to(directory).as_posix()
            archive_name(name)
            members[name] = path
    return members


def write_layer(directory, archive):
    """Compress a whole tree into `archive` (the reusable layer)."""
    members = tree_members(directory)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=LEVEL) as stream:
        for name in sorted(members, key=order_key):
            stream.writestr(member_info(name), members[name].read_bytes(),
                            compress_type=zipfile.ZIP_DEFLATED, compresslevel=LEVEL)


def _copy_raw(out, source, info):
    source.fp.seek(info.header_offset)
    header = source.fp.read(30)
    name_length, extra_length = struct.unpack('<HH', header[26:30])
    source.fp.seek(info.header_offset + 30 + name_length + extra_length)
    data = source.fp.read(info.compress_size)
    if len(data) != info.compress_size:
        raise ValueError('Truncated runtime layer')
    info.header_offset = out.fp.tell()
    out.fp.write(info.FileHeader())
    out.fp.write(data)
    out.filelist.append(info)
    out.NameToInfo[info.filename] = info
    out.start_dir = out.fp.tell()


def assemble(archive, sources, layers=()):
    """Write `archive` from {name: path} sources and already-compressed layer ZIPs."""
    sources = dict(sources)
    opened = []
    try:
        entries = {}
        for name, path in sources.items():
            reject_links(path)
            archive_name(name)
            entries[name] = ('source', path)
        for layer in layers:
            stream = zipfile.ZipFile(layer)
            opened.append(stream)
            # The layer was verified when it was built; CRCs are re-checked by the installer.
            for info in stream.infolist():
                if info.filename in entries:
                    raise ValueError('Layer and sources both provide ' + info.filename)
                entries[info.filename] = ('layer', stream, info)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=LEVEL) as out:
            for name in sorted(entries, key=order_key):
                entry = entries[name]
                if entry[0] == 'source':
                    out.writestr(member_info(name), entry[1].read_bytes(),
                                 compress_type=zipfile.ZIP_DEFLATED, compresslevel=LEVEL)
                else:
                    _copy_raw(out, entry[1], entry[2])
    finally:
        for stream in opened:
            stream.close()
