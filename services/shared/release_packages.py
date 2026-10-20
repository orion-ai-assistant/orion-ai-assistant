"""Stage 05 package trust and preparation. Never activates a release or runs it.

TUF owns signatures, root rotation, metadata expiry and metadata rollback checks.
Orion custom metadata owns product compatibility; caller persists installed
sequences only after a successful Stage 06 transaction.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import time
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, urlsplit
import zipfile


class PackageError(ValueError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_many(paths):
    """sha256 of many files, in order. Threads overlap per-file open latency: a real-time virus
    scanner inspects every freshly extracted file on its first open, which made one sequential
    pass over a ~5000 file package take 30-40 s instead of ~5 s (the hashing itself is <1 s)."""
    paths = list(paths)
    if len(paths) < 64:
        return [sha256(p) for p in paths]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(min(16, 2 * (os.cpu_count() or 4))) as pool:
        return list(pool.map(sha256, paths))


def archive_name(name):
    """One Windows-safe relative POSIX file name, identical on all build hosts."""
    parts = name.split('/')
    if (not name or '\\' in name or name.startswith('/') or any(
            not p or p in {'.', '..'} or p.endswith((' ', '.'))
            or any(ord(c) < 32 or c in '<>:"|?*' for c in p)
            or p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                *('COM' + i for i in '¹²³'), *('LPT' + i for i in '¹²³'),
                *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}
            for p in parts)):
        raise PackageError('Unsafe archive path')
    return PurePosixPath(name)


def extract_zip(archive, destination, *, max_bytes=8 * 1024**3, max_files=100000):
    """Publish a fresh tree only after every member is checked and CRC verified.

    Caller must hold its installation/build operation lock. No links, device
    files, ADS, duplicate/case-colliding names, or existing targets are accepted.
    """
    return extract_zips([archive], destination, max_bytes=max_bytes, max_files=max_files)


def extract_zips(archives, destination, *, max_bytes=8 * 1024**3, max_files=100000):
    """extract_zip of several archives into ONE tree (a code package and its runtime layer).

    The archives must not share a path (case-insensitively): every check spans all of them."""
    destination = Path(destination).absolute()
    for parent in (destination, *destination.parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400):
            raise PackageError('Archive destination contains a link/reparse point')
    if destination.exists():
        raise PackageError('Package destination already exists')
    with ExitStack() as stack:
        sources = [stack.enter_context(zipfile.ZipFile(archive)) for archive in archives]
        entries, names, spellings, total = [], {}, {}, 0
        for source in sources:
            for item in source.infolist():
                if item.orig_filename != item.filename:
                    raise PackageError('Ambiguous archive member name')
                name = archive_name(item.filename[:-1] if item.is_dir() else item.filename)
                mode = item.external_attr >> 16
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or item.flag_bits & 1:
                    raise PackageError('Linked, special or encrypted archive member')
                key = str(name).casefold()
                if key in names:
                    raise PackageError('Duplicate archive path')
                # Reject different spellings of implicit parent directories too.
                for prefix in [name, *name.parents][:-1]:
                    spelling = str(prefix)
                    previous = spellings.setdefault(spelling.casefold(), spelling)
                    if previous != spelling:
                        raise PackageError('Case-colliding archive parent')
                names[key] = item.is_dir()
                total += item.file_size
                if total > max_bytes or len(names) > max_files:
                    raise PackageError('Archive expansion limit exceeded')
                entries.append((source, item, name))
        for key in names:
            for parent in PurePosixPath(key).parents:
                if str(parent) in names and not names[str(parent)]:
                    raise PackageError('Archive file/directory collision')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < total:
            raise PackageError('Insufficient package disk space')
        with tempfile.TemporaryDirectory(prefix='.orion-package-', dir=destination.parent) as temporary:
            tree = Path(temporary) / 'tree'
            tree.mkdir()
            files = []
            for source, item, name in entries:
                output = tree.joinpath(*name.parts)
                if item.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    files.append((source, item, output))

            def write(member):
                # Reading each member to its end makes zipfile verify its CRC.
                with member[0].open(member[1]) as src, member[2].open('xb') as dst:
                    shutil.copyfileobj(src, dst)
            # Threads overlap the per-file close latency of a real-time virus scanner (see sha256_many);
            # ZipFile allows concurrent member reads, and every name was validated above.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(min(8, len(files)) or 1) as pool:
                for _ in pool.map(write, files):
                    pass
            # rename (not replace): on Windows a concurrently created target fails.
            if destination.exists():
                raise PackageError('Package destination appeared during preparation')
            tree.rename(destination)
    return destination


def _validate_release(value):
    required = {'schema', 'component', 'package_id', 'version', 'sequence', 'platform', 'arch',
                'profile', 'api', 'requires', 'capabilities', 'data_schema', 'rollback', 'entrypoint'}
    if not isinstance(value, dict) or set(value) != required or type(value['schema']) is not int or value['schema'] != 1:
        raise PackageError('Unsupported release metadata')
    if value['component'] not in {'hub', 'router', 'client', 'bootstrap'}:
        raise PackageError('Unknown package component')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,159}', value['package_id']):
        raise PackageError('Invalid immutable package identity')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?', value['version']):
        raise PackageError('Expected complete release version')
    if type(value['sequence']) is not int or value['sequence'] < 1:
        raise PackageError('Invalid monotonic release sequence')
    if value['platform'] != 'windows' or value['arch'] != 'x86_64' or value['profile'] != 'cpu':
        raise PackageError('Unsupported platform, architecture or profile (CUDA is an independent engine package)')
    if type(value['api']) is not int or value['api'] < 1:
        raise PackageError('Invalid API generation')
    if not isinstance(value['requires'], dict) or value['requires'].keys() - {'hub', 'router', 'client', 'mobile'}:
        raise PackageError('Invalid API compatibility ranges')
    for bounds in value['requires'].values():
        if not isinstance(bounds, list) or len(bounds) != 2 or any(type(n) is not int or n < 1 for n in bounds) or bounds[0] > bounds[1]:
            raise PackageError('Invalid API compatibility range')
    if not isinstance(value['capabilities'], list) or any(not isinstance(c, str) for c in value['capabilities']):
        raise PackageError('Invalid capabilities')
    if type(value['data_schema']) is not int or value['data_schema'] < 1 or value['rollback'] != 'same-schema-only':
        raise PackageError('Unsupported data/rollback policy')
    archive_name(value['entrypoint'])
    return value


def validate_release(value):
    try:
        return _validate_release(value)
    except (TypeError, KeyError, AttributeError) as error:
        raise PackageError('Malformed release field') from error


# Split packages: the Hub/Router code package and the runtime layer it runs on (bundled Python + wheels,
# PostgreSQL, Redis/FFmpeg) are separate signed targets of the same feed. The layer's name carries its content
# hash, so an unchanged runtime is neither uploaded nor downloaded again; the code target's signed metadata
# pins it (`runtime`: target name, sha256, length) and prepare() extracts both into ONE tree, identical to the
# former combined package. Bootstraps that predate this never read the code targets (other stable names).
# The bootstrap is split the same way, but its combined bundle is still published under the old name next to the
# code target: get-orion.ps1 installs from it, and updaters that predate split bootstraps adopt it.
RUNTIME_REFERENCE = 'orion-runtime.json'  # the code package's own copy of its signed runtime reference
LAYER_NAME = re.compile(r'orion-(hub|router|bootstrap)-runtime-[0-9a-f]{16,64}\.zip')
# The unsigned installer download (get-orion.ps1) and the signed combined TUF target share one stable name.
BOOTSTRAP_TARGET = 'orion-bootstrap-windows-x64.zip'
BOOTSTRAP_CODE_TARGET = 'orion-bootstrap-code-windows-x64.zip'


def validate_layer(value):
    """custom of a runtime layer target: {'schema': 1, 'layer': 'runtime', 'for': kind, 'inputs': key}."""
    if (not isinstance(value, dict) or set(value) != {'schema', 'layer', 'for', 'inputs'} or value['schema'] != 1
            or value['layer'] != 'runtime' or value['for'] not in {'hub', 'router', 'bootstrap'}
            or not isinstance(value['inputs'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['inputs'])):
        raise PackageError('Unsupported runtime layer metadata')
    return value


def validate_runtime_reference(value, component):
    match = LAYER_NAME.fullmatch(value['target']) if isinstance(value, dict) and isinstance(value.get('target'), str) else None
    if (match is None or set(value) != {'target', 'sha256', 'length'} or match.group(1) != component
            or not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
            or type(value['length']) is not int or value['length'] < 1):
        raise PackageError('Invalid runtime layer reference')
    return value


def runtime_reference(target):
    """The runtime layer a signed code target needs, or None for a complete (combined) package."""
    value = target.unrecognized_fields.get('runtime')
    return None if value is None else validate_runtime_reference(value, target.custom['component'])


def is_layer_description(value):
    """A candidate/staged `.target.json` of a runtime layer (not a product package)."""
    return isinstance(value.get('custom'), dict) and 'layer' in value['custom']


def artifact_identity(value):
    """Acceptance binding key of a candidate `.target.json`: package id, or the target name of a layer and of a
    bootstrap (its combined bundle and its code package are one release, so they share the package id)."""
    if is_layer_description(value) or value['custom']['component'] == 'bootstrap':
        return value['path']
    return value['custom']['package_id']


def _seen_key(target):
    """Rollback memory is per component, except that a bootstrap release is published twice under one sequence
    (combined bundle and code package + layer): each of those two names is immutable on its own."""
    return target.path if target.path == BOOTSTRAP_CODE_TARGET else target.custom['component']


def compatible(releases, *, installed_sequences=None, mobile_api=None):
    """Validate the entire proposed set, including unchanged installed components."""
    installed_sequences = installed_sequences or {}
    if len({r['component'] for r in releases}) != len(releases):
        raise PackageError('Duplicate component in release set')
    versions = {r['component']: validate_release(r) for r in releases}
    for component, release in versions.items():
        if release['sequence'] < installed_sequences.get(component, 0):
            raise PackageError('Product downgrade rejected')
        for peer, (minimum, maximum) in release['requires'].items():
            api = mobile_api if peer == 'mobile' else versions.get(peer, {}).get('api')
            # Optional absent peers (standalone Router, headless Hub) do not block.
            if api is not None and not minimum <= api <= maximum:
                raise PackageError('Incompatible ' + component + '/' + peer + ' API')
    if 'hub' in versions and 'router' in versions:
        needed = {'hubs.accounts.get', 'hubs.account-status.get'}
        if not needed <= set(versions['router']['capabilities']):
            raise PackageError('Router account inventory/status endpoints required')
    return versions


class ReleaseClient:
    """Explicit check/download API. Does not schedule, activate or update registry."""
    def __init__(self, *, trusted_root, cache, metadata_url, targets_url, fetcher=None):
        from tuf.ngclient import Updater
        # Production transport is HTTPS. Tests inject a bounded local fetcher.
        for url in (metadata_url, targets_url):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise PackageError('Release repository requires credential-free HTTPS')
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.bootstrap_root = Path(trusted_root).read_bytes()
        self.updater = Updater(str(self.cache / 'metadata'), metadata_url,
            target_dir=str(self.cache / 'targets'), target_base_url=targets_url,
            bootstrap=self.bootstrap_root, fetcher=fetcher)
        self.status_path = self.cache / 'check.json'
        self.checked = False
        self._progress = None
        self._received = 0
        self._tick_at = 0.0
        self._count_downloaded_bytes()

    def _count_downloaded_bytes(self):
        """Count bytes of the download in flight (the app shows "85 / 198 MB, 1.4 MB/s").

        Wraps the updater's fetcher *instance* so injected test fetchers keep their own attributes."""
        fetcher = getattr(self.updater, '_fetcher', None)
        original = getattr(fetcher, 'fetch', None)
        if original is None:
            return
        client = self

        def counting(url):
            for chunk in original(url):
                client._received += len(chunk)
                client._tick()
                yield chunk
        fetcher.fetch = counting

    def _tick(self, *, force=False):
        callback = self._progress
        now = time.monotonic()
        if callback is not None and (force or now - self._tick_at >= .5):
            self._tick_at = now
            try:
                callback(self._received, self._total, now - self._started)
            except Exception:
                pass  # Progress is cosmetic; it must never fail a download.

    def check(self, targets, optional=()):
        """`optional`: names that may be missing from the feed (left out of the result instead of failing)."""
        from tuf.api.exceptions import RepositoryError, DownloadError
        previous = json.loads(self.status_path.read_text()) if self.status_path.exists() else {}
        seen = dict(previous.get('seen', {}))
        try:
            self.updater.refresh()
            result = {}
            for name in targets:
                archive_name(name)
                info = self.updater.get_targetinfo(name)
                if info is None and name in optional:
                    continue
                if info is None:
                    raise PackageError('Release target missing')
                validate_release(info.custom)
                identity = {'sequence': info.custom['sequence'], 'package_id': info.custom['package_id'],
                            'length': info.length, 'hashes': info.hashes}
                if self._layer(info) is not None:
                    identity['runtime'] = runtime_reference(info)
                key = _seen_key(info)
                prior = seen.get(key)
                if prior and (identity['sequence'] < prior['sequence']
                              or (identity['sequence'] == prior['sequence'] and identity != prior)):
                    raise PackageError('Product rollback or immutable target substitution rejected')
                seen[key] = identity
                result[name] = info
            status = {'status': 'checked', 'last_success': datetime.now(timezone.utc).isoformat(), 'seen': seen}
            self.checked = True
        except (RepositoryError, DownloadError, OSError, ValueError) as error:
            self.checked = False
            status = {'status': 'check-unavailable', 'last_success': previous.get('last_success'),
                      'reason': type(error).__name__, 'seen': previous.get('seen', {})}
            result = None
        self._status(status)
        return status, result

    def _status(self, status):
        # This cache is separate from install.json and never stores credentials.
        with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as stream:
            path = Path(stream.name)
            stream.write(json.dumps(status).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, self.status_path)

    def prepare(self, target, destination, progress=None):
        if not self.checked:
            raise PackageError('A fresh successful metadata check is required')
        # Re-resolve inside this trusted refresh; caller cannot inject TargetFile.
        current = self.updater.get_targetinfo(target.path)
        if current is None or current.to_dict() != target.to_dict():
            raise PackageError('Target changed since release selection')
        archive_name(current.path)
        seen = json.loads(self.status_path.read_text())['seen'][_seen_key(current)]
        if current.custom['sequence'] != seen['sequence'] or current.hashes != seen['hashes']:
            raise PackageError('Selected package is older than the last verified release')
        layer = self._layer(current)
        parts = [t for t in (layer, current) if t is not None]
        paths = {t.path: self.updater.find_cached_target(t) for t in parts}
        # Only what is not cached yet is downloaded: an unchanged runtime layer costs nothing.
        pending = [t for t in parts if paths[t.path] is None]
        self._progress, self._received = progress, 0
        self._total = sum(t.length for t in pending) or current.length
        self._started, self._tick_at = time.monotonic(), 0.0
        try:
            for part in pending:
                paths[part.path] = self.updater.download_target(part)
            if pending:
                self._tick(force=True)
        finally:
            self._progress = None
        from .installation_paths import canonical_path, validate_private_directory
        destination = canonical_path(destination)
        if destination.exists():
            raise PackageError('Package destination already exists')
        validate_private_directory(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Never reopen mutable download-cache bytes after checking their hash.
        # Copy to our private, exclusive snapshot and verify that exact stream.
        # Concurrent cache writes during copy then fail hash verification; later
        # cache replacement cannot alter the archive we actually extract.
        with ExitStack() as stack:
            snapshots = []
            for part in parts:
                snapshot = stack.enter_context(tempfile.TemporaryFile(prefix='.orion-verified-', dir=destination.parent))
                with Path(paths[part.path]).open('rb') as stream:
                    copied = 0
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        copied += len(block)
                        if copied > part.length:
                            raise PackageError('Download cache grew beyond the signed target length')
                        snapshot.write(block)
                snapshot.seek(0)
                part.verify_length_and_hashes(snapshot)
                snapshot.seek(0)
                snapshots.append(snapshot)
            destination = extract_zips(snapshots, destination)
        if layer is not None:
            self._prune_layers(current.custom['component'], keep=layer.path)
        try:
            manifest = json.loads((destination / 'orion-release.json').read_text(encoding='utf-8'))
            if validate_release(manifest) != current.custom:
                raise PackageError('Archive and signed release metadata disagree')
            if not destination.joinpath(*archive_name(manifest['entrypoint']).parts).is_file():
                raise PackageError('Package entrypoint missing')
            if layer is not None and json.loads((destination / RUNTIME_REFERENCE).read_text(encoding='utf-8')) \
                    != runtime_reference(current):
                raise PackageError('Package and signed runtime layer reference disagree')
        except Exception:
            # This exact tree was created above, never an active release/data tree.
            shutil.rmtree(destination)
            raise
        return destination

    def _layer(self, target):
        """Signed TargetFile of the runtime layer `target` needs (validated against its reference), else None."""
        reference = runtime_reference(target)
        if reference is None:
            return None
        layer = self.updater.get_targetinfo(reference['target'])
        if (layer is None or layer.length != reference['length'] or layer.hashes.get('sha256') != reference['sha256']
                or validate_layer(layer.custom)['for'] != target.custom['component']):
            raise PackageError('Runtime layer missing or not the one the package was built with')
        return layer

    def download_length(self, target):
        """Bytes prepare(target) still has to download (an estimate for progress: cached files are re-verified)."""
        total = 0
        for part in filter(None, (self._layer(target), target)):
            cached = self.cache / 'targets' / quote(part.path, '')
            if not (cached.is_file() and cached.stat().st_size == part.length):
                total += part.length
        return total

    def _prune_layers(self, component, *, keep):
        """Keep only the runtime layer just used in the download cache (each one is 120-170 MB); the combined
        package the split one replaced is not needed again either."""
        targets = self.cache / 'targets'
        for path in [*targets.glob(f'orion-{component}-runtime-*.zip'), *targets.glob(f'orion-{component}-windows-*.zip')]:
            if path.name != quote(keep, ''):
                try:
                    path.unlink()
                except OSError:
                    pass  # Only disk space: the next prepare tries again.


class FeedSet:
    """Several ReleaseClients (one per feed, each with its own TUF cache) behind the check/prepare
    interface of one, so an installer or the update helper handles Hub, Client and Router in one pass."""
    def __init__(self, routes):
        # routes: [(client, names owned by that client)]
        self.routes = [(client, set(names)) for client, names in routes]
        self.bootstrap_root = self.routes[0][0].bootstrap_root
        self._owner = {}

    def check(self, names):
        merged, status = {}, None
        for client, owned in self.routes:
            wanted = [n for n in names if n in owned]
            if not wanted:
                continue
            result, targets = client.check(wanted)
            if targets is None:
                return result, None
            status = status or result
            merged.update(targets)
            self._owner.update({target.path: client for target in targets.values()})
        unknown = set(names) - set(merged)
        if unknown:
            raise PackageError('Release target is not offered by any configured feed')
        return status, merged

    def prepare(self, target, destination, progress=None):
        client = self._owner.get(target.path)
        if client is None:
            raise PackageError('A fresh successful metadata check is required')
        return client.prepare(target, destination, progress=progress)

    def download_length(self, target):
        client = self._owner.get(target.path)
        return target.length if client is None else client.download_length(target)


def record_prepared_package(tree, state, target, *, cache=None):
    """Local installed-package provenance, made only after TUF prepare succeeds.

    The private registry pins this inventory. It is not a vendor signature and
    never authorizes an arbitrary unregistered package folder by itself.
    `cache` is the release's own state folder, which also gets the verified cache.
    """
    tree, state = Path(tree), Path(state)
    signature = _scan(tree)  # before hashing: a change during the hash then misses the cache
    paths = [p for p in sorted(tree.rglob('*')) if p.is_file()]
    files = {p.relative_to(tree).as_posix(): digest for p, digest in zip(paths, sha256_many(paths))}
    inventory = state / 'installed-package.json'
    inventory.write_text(json.dumps({'schema': 1, 'manifest': target.custom, 'files': files}, sort_keys=True), encoding='utf-8')
    provenance = {'id': target.custom['package_id'], 'sha256': target.hashes['sha256'],
                  'length': target.length, 'inventory_sha256': sha256(inventory)}
    for folder in filter(None, (state, cache)):
        _write_verified_cache(folder, provenance['inventory_sha256'], signature)
    return provenance


VERIFIED_CACHE = 'verified-package.json'


def _scan(root):
    """One pass over the tree: file name -> [size, mtime_ns]; refuses links/reparse points."""
    root, files, stack = str(root), {}, [str(root)]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise PackageError('Link appeared inside installed package')
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    files[os.path.relpath(entry.path, root).replace(os.sep, '/')] = [info.st_size, info.st_mtime_ns]
    return files


def _write_verified_cache(state, inventory_sha256, signature):
    """Remember each file's size/mtime taken BEFORE a full hash verification.

    Quick launch checks compare these instead of re-hashing ~1 GB each time. Any
    mismatch falls back to the full hash check, which stays authoritative for
    install, update, recovery and repair. A same-user attacker who can forge
    mtimes could also rewrite the private registry, which is outside this check.
    """
    try:
        value = {'schema': 1, 'inventory_sha256': inventory_sha256, 'files': signature}
        temporary = Path(state) / (VERIFIED_CACHE + '.tmp')
        temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
        os.replace(temporary, Path(state) / VERIFIED_CACHE)
    except OSError:
        pass  # Only a speed-up: the next check simply hashes again.


def _cache_matches(state, inventory_sha256, signature):
    try:
        value = json.loads((Path(state) / VERIFIED_CACHE).read_text(encoding='utf-8'))
        return (value['schema'] == 1 and value['inventory_sha256'] == inventory_sha256
                and value['files'] == signature)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_registered_package(root):
    from .installation_registry import Registry
    from .installation_paths import canonical_path
    root = canonical_path(root, must_exist=True)
    record = Registry().read()
    selected = [c for c in (record or {}).get('components', [])
                if c['distribution'] == 'packaged' and Path(c['code_path']) == root and c.get('package')]
    if len(selected) != 1:
        raise PackageError('Signed package must be installed through the trusted package installer')
    item = selected[0]
    # Launch, preference and identity checks use the quick path; an update trusts it only
    # right after staging (see UpdateHelper.resume), recovery always hashes everything.
    return verify_package_record(item, quick=True)


def verify_package_record(item, *, inventory=None, quick=False):
    """Verify an explicit registry snapshot, including a pending/previous release.

    `quick` trusts unchanged size+mtime recorded in the release's own state folder by
    the last full hash of exactly this inventory (the installer, the launcher or the
    staging of an update); any mismatch falls back to hashing every file."""
    from .installation_paths import canonical_path
    root = canonical_path(item['code_path'], must_exist=True)
    state = Path(item['state_path'])
    inventory = canonical_path(inventory or state / 'installed-package.json', must_exist=True)
    if sha256(inventory) != item['package']['inventory_sha256']:
        raise PackageError('Installed package inventory changed')
    value = json.loads(inventory.read_text(encoding='utf-8'))
    manifest = validate_release(value['manifest'])
    if value['schema'] != 1 or manifest['component'] != item['kind'] or manifest['package_id'] != item['package']['id']:
        raise PackageError('Installed package provenance mismatch')
    signature = _scan(root)
    if set(signature) != set(value['files']):
        raise PackageError('Installed package file set changed')
    if quick and _cache_matches(state, item['package']['inventory_sha256'], signature):
        return manifest
    paths = []
    for name in value['files']:
        path = canonical_path(root.joinpath(*archive_name(name).parts), must_exist=True)
        if not path.is_relative_to(root):
            raise PackageError('Installed package file hash mismatch')
        paths.append(path)
    if sha256_many(paths) != list(value['files'].values()):
        raise PackageError('Installed package file hash mismatch')
    _write_verified_cache(state, item['package']['inventory_sha256'], signature)
    return manifest
