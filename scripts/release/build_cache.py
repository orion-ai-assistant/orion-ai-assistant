"""Content-addressed cache for the release build (nothing here weakens a release).

A cached result is only ever reused when *every* input that can change its bytes is
unchanged: the tracked source files (by content), the release json, the pinned inputs, the
requirements lock and the build tooling itself. Package ZIPs are immutable, so a reused ZIP
keeps its exact bytes (hence sequence/hash), which the updater requires.

Layout under the cache root (default `<repo>/.tmp/build-cache`):
  packages/<kind>/<key>/  zip + .target.json   - finished package for exactly that input set
  runtime/<kind>-<key>/   tree/ + layer.zip    - python/redis/postgres/ffmpeg runtime layer
  web/<name>.json         source key -> built output hash (Flutter / Router dashboard)
  acceptance.json         accepted package set + tooling hash
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

KEY_VERSION = 1  # bump when the key recipe itself changes: invalidates every entry


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def text_digest(path):
    """sha256 of a text input with CRLF read as LF: a checkout with core.autocrlf (CRLF) and one without
    (LF) hold the same git content, and neither line ending changes what a lock file or recipe builds."""
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def hash_files(files, *, workers=8, text=()):
    """Order-independent digest of {relative name: absolute path} contents.

    Names and content both count: renames, additions, deletions and edits all change it. Names in
    `text` are hashed with CRLF read as LF (`text_digest`): equal to `file_digest` for an LF file.
    """
    names = sorted(files)
    text = set(text)

    def one(name):
        return (text_digest if name in text else file_digest)(files[name])
    with ThreadPoolExecutor(workers) as pool:
        digests = list(pool.map(one, names))
    outer = hashlib.sha256()
    outer.update(f'files-v{KEY_VERSION}\0'.encode())
    for name, digest in zip(names, digests):
        outer.update(name.encode('utf-8') + b'\0' + digest.encode() + b'\n')
    return outer.hexdigest()


def tree_files(directory):
    directory = Path(directory)
    return {p.relative_to(directory).as_posix(): p for p in directory.rglob('*') if p.is_file()}


def hash_tree(directory):
    return hash_files(tree_files(directory))


def git_files(root, prefixes, *, untracked=False):
    """Relative names under the given path prefixes, as git lists them.

    `untracked=True` also lists new unignored files (web builds read the working tree; the
    package builder itself only ever copies tracked files).
    """
    argv = ['git', '-C', str(root), 'ls-files', '-z']
    if untracked:
        argv += ['--cached', '--others', '--exclude-standard']
    names = subprocess.check_output(argv).decode('utf-8').split('\0')
    result = []
    for name in filter(None, names):
        if any(name == prefix or name.startswith(prefix + '/') for prefix in prefixes):
            if (Path(root) / name).is_file():  # tracked but deleted/moved in the working tree
                result.append(name)
    return sorted(set(result))


def combine(*parts):
    """One key from labelled parts (strings or json-able values)."""
    outer = hashlib.sha256()
    outer.update(f'combine-v{KEY_VERSION}\0'.encode())
    for label, value in parts:
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))
        outer.update(label.encode() + b'=' + text.encode('utf-8') + b'\0')
    return outer.hexdigest()


def stat_signature(path):
    info = Path(path).stat()
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns}


class Cache:
    def __init__(self, root):
        self.root = Path(root)

    # --- finished packages -------------------------------------------------------------
    def package_dir(self, kind, key):
        return self.root / 'packages' / kind / key

    def lookup_package(self, kind, key):
        """(zip, target.json) of a stored package for exactly this key, else None."""
        directory = self.package_dir(kind, key)
        meta = directory / 'entry.json'
        try:
            entry = json.loads(meta.read_text(encoding='utf-8'))
            archive = directory / entry['archive']
            target = directory / entry['target']
            if (entry.get('key') != key or stat_signature(archive) != entry['signature']
                    or not target.is_file()):
                return None
            return archive, target
        except (OSError, ValueError, KeyError):
            return None

    def store_package(self, kind, key, archive, target):
        directory = self.package_dir(kind, key)
        if self.lookup_package(kind, key) is not None:
            # Write-once: the bytes of a key never change (they may already be published under that version).
            return
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        # Hard link when possible (same volume): the cache entry and the candidate directory
        # share bytes without a second copy of 100-200 MB.
        stored = directory / Path(archive).name
        link_or_copy(archive, stored)
        shutil.copyfile(target, directory / Path(target).name)
        (directory / 'entry.json').write_text(json.dumps({
            'key': key, 'archive': stored.name, 'target': Path(target).name,
            'signature': stat_signature(stored)}), encoding='utf-8')
        # Keep this entry and the previous one (reverting a change then hits the cache); older
        # keys are dropped so the cache cannot grow without bound.
        parent = directory.parent
        entries = sorted((p for p in parent.iterdir() if p.is_dir() and p != directory),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in entries[KEEP_PACKAGES - 1:]:
            shutil.rmtree(old, ignore_errors=True)

    # --- small json records --------------------------------------------------------------
    def read_record(self, name):
        try:
            return json.loads((self.root / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def write_record(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        os.replace(temporary, path)


# Entries kept per component besides the newest: enough that parallel worktrees or a revert do not evict the
# entry of a version that is already published (its rebuilt ZIP would not be byte-identical).
KEEP_PACKAGES = 8


def link_or_copy(source, destination):
    """Same bytes at a second path; a hard link when the volume allows it, else a copy."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)
