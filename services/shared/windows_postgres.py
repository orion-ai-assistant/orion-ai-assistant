"""Prepare a new PostgreSQL runtime with process-local UTF-8 on Windows.

Never patch an installed runtime. The pinned vendor archive is extracted into
an unpublished sibling, manifests are augmented, and the directory is renamed.
The receipt records both vendor and derived executable hashes for packaging.
This does not replace the signed release metadata required by lifecycle stage 05.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

VERSION = '16.15-1'
ARCHIVE_NAME = f'postgresql-{VERSION}-windows-x64-binaries.zip'
ARCHIVE_URL = 'https://get.enterprisedb.com/postgresql/' + ARCHIVE_NAME
ARCHIVE_SHA256 = '25e6fcdfb8caec38691bf461125e7564508760666f7b8e5dc6a5f0818f58f81e'
RECEIPT = '.orion-postgres-runtime.json'
CODE_PAGE = '{http://schemas.microsoft.com/SMI/2019/WindowsSettings}activeCodePage'


def require_supported_windows():
    if os.name != 'nt' or sys.getwindowsversion().build < 18362:
        raise ValueError('The Unicode PostgreSQL runtime requires Windows 10 1903 or newer')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def utf8_manifest(source):
    root = ET.fromstring(source)
    ns = '{urn:schemas-microsoft-com:asm.v3}'
    settings = root.find(ns + 'application/' + ns + 'windowsSettings')
    if settings is None:
        application = root.find(ns + 'application')
        if application is None:
            application = ET.SubElement(root, ns + 'application')
        settings = ET.SubElement(application, ns + 'windowsSettings')
    page = settings.find(CODE_PAGE)
    if page is None:
        page = ET.SubElement(settings, CODE_PAGE)
    page.text = 'UTF-8'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _add_utf8_manifest(executable):
    # Only unpublished copies from the pinned archive reach this function.
    # Authenticode-signed input must be repackaged/signed by a release pipeline;
    # never silently invalidate a vendor certificate.
    with executable.open('rb') as stream:
        stream.seek(0x3c)
        pe = struct.unpack('<I', stream.read(4))[0]
        stream.seek(pe + 24)
        magic = struct.unpack('<H', stream.read(2))[0]
        if magic not in (0x10b, 0x20b):
            raise ValueError('Unsupported PostgreSQL executable format')
        stream.seek(pe + 24 + (112 if magic == 0x20b else 96) + 8 * 4)
        if any(struct.unpack('<II', stream.read(8))):
            raise ValueError('Signed PostgreSQL executable requires a signed packaging workflow')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint]
    kernel.LoadLibraryExW.restype = ctypes.c_void_p
    kernel.FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.FindResourceW.restype = ctypes.c_void_p
    kernel.SizeofResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.LoadResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.LoadResource.restype = ctypes.c_void_p
    kernel.LockResource.argtypes = [ctypes.c_void_p]
    kernel.LockResource.restype = ctypes.c_void_p
    kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
    handle = kernel.LoadLibraryExW(str(executable), None, 2)  # data only, no DLL initialization
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        resource = kernel.FindResourceW(handle, 1, 24)
        if not resource:
            raise ValueError('Vendor PostgreSQL application manifest missing')
        pointer = kernel.LockResource(kernel.LoadResource(handle, resource))
        manifest = utf8_manifest(ctypes.string_at(pointer, kernel.SizeofResource(handle, resource)))
    finally:
        kernel.FreeLibrary(handle)
    kernel.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    kernel.BeginUpdateResourceW.restype = ctypes.c_void_p
    kernel.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_ushort, ctypes.c_void_p, ctypes.c_ulong]
    kernel.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    update = kernel.BeginUpdateResourceW(str(executable), False)
    if not update:
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.UpdateResourceW(update, 24, 1, 1033, manifest, len(manifest)):
        error = ctypes.get_last_error()
        kernel.EndUpdateResourceW(update, True)
        raise ctypes.WinError(error)
    if not kernel.EndUpdateResourceW(update, False):
        raise ctypes.WinError(ctypes.get_last_error())


def provision_runtime(archive, destination):
    require_supported_windows()
    archive, destination = Path(archive), Path(destination).absolute()
    if destination.exists():
        raise ValueError('PostgreSQL runtime target is occupied; existing files preserved')
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError('PostgreSQL archive SHA256 mismatch; nothing installed')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.postgres-staging-', dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                name = PurePosixPath(member.filename)
                if (name.is_absolute() or '\\' in member.filename or ':' in member.filename
                        or '..' in name.parts or (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError('Unsafe PostgreSQL archive member')
                selected = (len(name.parts) > 2 and name.parts[:2] in
                            [('pgsql', 'bin'), ('pgsql', 'lib'), ('pgsql', 'share')])
                selected |= member.filename in ('pgsql/server_license.txt', 'pgsql/commandlinetools_3rd_party_licenses.txt')
                # StackBuilder is a separately signed GUI installer, not part
                # of the embedded server runtime. Preserve its vendor signature
                # in the archive; neither ship nor transform it here.
                selected &= member.filename != 'pgsql/bin/stackbuilder.exe'
                if selected:
                    source.extract(member, staging)
        runtime = staging / 'pgsql'
        files = {}
        for executable in sorted((runtime / 'bin').glob('*.exe')):
            vendor_hash = sha256(executable)
            _add_utf8_manifest(executable)
            files[executable.name] = {'vendor_sha256': vendor_hash, 'sha256': sha256(executable)}
        if not {'postgres.exe', 'initdb.exe', 'pg_ctl.exe', 'psql.exe'} <= files.keys():
            raise ValueError('Incomplete PostgreSQL runtime')
        receipt = dict(schema=1, version=VERSION, archive_sha256=ARCHIVE_SHA256,
                       active_code_page='UTF-8', executables=files)
        (runtime / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        # Windows rename fails if another installer published the target first.
        runtime.rename(destination)
        return receipt
    finally:
        # Only the unique unpublished staging folder created above is removed.
        if staging.resolve().parent != destination.parent.resolve() or not staging.name.startswith('.postgres-staging-'):
            raise ValueError('PostgreSQL staging cleanup escaped its parent')
        shutil.rmtree(staging)


def verify_unicode_runtime(runtime):
    require_supported_windows()
    runtime = Path(runtime)
    try:
        receipt = json.loads((runtime / RECEIPT).read_text(encoding='utf-8'))
        if (receipt['schema'] != 1 or receipt['version'] != VERSION
                or receipt['archive_sha256'] != ARCHIVE_SHA256 or receipt['active_code_page'] != 'UTF-8'):
            raise ValueError('Invalid PostgreSQL Unicode runtime receipt')
        for name in ('postgres.exe', 'initdb.exe', 'pg_ctl.exe', 'psql.exe'):
            if sha256(runtime / 'bin' / name) != receipt['executables'][name]['sha256']:
                raise ValueError('PostgreSQL Unicode executable hash mismatch')
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('This Unicode path requires a prepared PostgreSQL runtime; existing files preserved') from exc
    return receipt
