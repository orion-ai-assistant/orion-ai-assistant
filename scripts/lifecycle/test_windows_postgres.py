"""Unicode runtime preparation boundaries; no live cluster or network."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

import pytest

from services.shared import windows_postgres as pg


def test_manifest_preserves_execution_level_and_dependencies():
    source = b'''<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
      <dependency><dependentAssembly><assemblyIdentity name="vendor"/></dependentAssembly></dependency>
      <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
      <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges></security></trustInfo></assembly>'''
    result = pg.utf8_manifest(source)
    root = ET.fromstring(result)
    assert root.find('.//' + pg.CODE_PAGE).text == 'UTF-8'
    assert root.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel').attrib == dict(level='asInvoker', uiAccess='false')
    assert root.find('.//{urn:schemas-microsoft-com:asm.v1}assemblyIdentity').get('name') == 'vendor'
    assert pg.utf8_manifest(result) == result


def test_occupied_runtime_is_never_replaced(tmp_path):
    target = tmp_path / 'postgres'
    target.mkdir()
    sentinel = target / 'user-data'
    sentinel.write_bytes(b'preserve')
    with patch.object(pg, 'require_supported_windows'), pytest.raises(ValueError, match='occupied'):
        pg.provision_runtime(tmp_path / 'missing.zip', target)
    assert sentinel.read_bytes() == b'preserve'


def test_wrong_archive_hash_has_no_extraction_side_effect(tmp_path):
    archive = tmp_path / 'wrong.zip'
    archive.write_bytes(b'not a vendor archive')
    target = tmp_path / 'parent/postgres'
    with patch.object(pg, 'require_supported_windows'), pytest.raises(ValueError, match='SHA256'):
        pg.provision_runtime(archive, target)
    assert not target.parent.exists()


@pytest.mark.parametrize('name', ['pgsql/../../escape', '/absolute', 'C:/drive', 'pgsql/bin/..\\escape'])
def test_archive_escape_is_rejected_and_unpublished_staging_removed(tmp_path, name):
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(name, b'untrusted')
    with patch.object(pg, 'require_supported_windows'), patch.object(pg, 'ARCHIVE_SHA256', pg.sha256(archive)):
        with pytest.raises(ValueError, match='Unsafe'):
            pg.provision_runtime(archive, tmp_path / 'postgres')
    assert list(tmp_path.iterdir()) == [archive]


def test_executable_transform_failure_cannot_publish_partial_runtime(tmp_path):
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('pgsql/bin/postgres.exe', b'fixture')
    with patch.object(pg, 'require_supported_windows'), patch.object(pg, 'ARCHIVE_SHA256', pg.sha256(archive)):
        with patch.object(pg, '_add_utf8_manifest', side_effect=OSError('fixture failure')):
            with pytest.raises(OSError, match='fixture failure'):
                pg.provision_runtime(archive, tmp_path / 'postgres')
    assert list(tmp_path.iterdir()) == [archive]


def test_prepared_runtime_tampering_is_rejected(tmp_path):
    files = {}
    (tmp_path / 'bin').mkdir()
    for name in ('postgres.exe', 'initdb.exe', 'pg_ctl.exe', 'psql.exe'):
        path = tmp_path / 'bin' / name
        path.write_bytes(name.encode())
        files[name] = {'sha256': pg.sha256(path)}
    receipt = dict(schema=1, version=pg.VERSION, archive_sha256=pg.ARCHIVE_SHA256,
                   active_code_page='UTF-8', executables=files)
    (tmp_path / pg.RECEIPT).write_text(json.dumps(receipt))
    with patch.object(pg, 'require_supported_windows'):
        assert pg.verify_unicode_runtime(tmp_path) == receipt
        (tmp_path / 'bin/psql.exe').write_bytes(b'changed')
        with pytest.raises(ValueError, match='hash mismatch'):
            pg.verify_unicode_runtime(tmp_path)
