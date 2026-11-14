"""pgvector in the Hub's PostgreSQL: the pinned files reach the runtime, the Hub enables the extension as the
superuser, and missing files or a failing statement only warn (the Hub keeps starting)."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile

import pytest

from services.shared import postgres_vector


def fake_archive(tmp_path, names=postgres_vector.FILES):
    archive = tmp_path / 'pgvector.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        for name in names:
            output.writestr(name, 'bytes of ' + name)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def fake_pg(runtime):
    return Mock(ctl=runtime / 'bin' / 'pg_ctl.exe')


def test_install_places_the_files_once(tmp_path):
    archive, digest = fake_archive(tmp_path)
    runtime = tmp_path / 'postgres'
    assert postgres_vector.install(runtime, archive, digest) == list(postgres_vector.FILES)
    assert (runtime / 'lib/vector.dll').read_text() == 'bytes of lib/vector.dll'
    assert postgres_vector.present(runtime)
    assert postgres_vector.install(runtime, archive, digest) == []  # Same bytes: nothing rewritten.
    assert not list(runtime.rglob('*.orion-tmp'))


def test_install_refuses_a_changed_or_unexpected_archive(tmp_path):
    archive, digest = fake_archive(tmp_path)
    with pytest.raises(ValueError, match='SHA256'):
        postgres_vector.install(tmp_path / 'postgres', archive, '0' * 64)
    assert not (tmp_path / 'postgres').exists()
    (tmp_path / 'extra').mkdir()
    extra, extra_digest = fake_archive(tmp_path / 'extra', postgres_vector.FILES + ('lib/other.dll',))
    with pytest.raises(ValueError, match='contents'):
        postgres_vector.install(tmp_path / 'postgres', extra, extra_digest)
    assert not (tmp_path / 'postgres').exists()


def test_the_pinned_archive_is_the_one_in_git():
    """ARCHIVE_SHA256, pgvector.json and the zip kept in git agree, file by file."""
    archive = postgres_vector.ARCHIVE
    receipt = json.loads((archive.parent / 'pgvector.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == postgres_vector.ARCHIVE_SHA256 == receipt['archive_sha256']
    assert receipt['pgvector'] == postgres_vector.VERSION and receipt['archive'] == archive.name
    with zipfile.ZipFile(archive) as source:
        assert sorted(source.namelist()) == sorted(postgres_vector.FILES) == sorted(receipt['files'])
        for name, digest in receipt['files'].items():
            assert hashlib.sha256(source.read(name)).hexdigest() == digest
        control = source.read('share/extension/vector.control').decode()
    assert f"default_version = '{postgres_vector.VERSION}'" in control


def test_enable_creates_the_extension_as_the_superuser(tmp_path):
    archive, digest = fake_archive(tmp_path)
    postgres_vector.install(tmp_path / 'postgres', archive, digest)
    pg, report = fake_pg(tmp_path / 'postgres'), Mock()
    with patch('services.shared.postgres_auth._psql') as psql:
        assert postgres_vector.enable(pg, ('postgres', 'secret'), 'orion', report) is True
    psql.assert_called_once_with(pg, ('postgres', 'secret'), 'orion', postgres_vector.ENABLE)
    assert postgres_vector.ENABLE.startswith('CREATE EXTENSION IF NOT EXISTS vector;')
    assert 'ALTER EXTENSION vector UPDATE' in postgres_vector.ENABLE
    report.assert_not_called()


def test_missing_files_only_warn(tmp_path):
    report = Mock()
    with patch('services.shared.postgres_auth._psql') as psql:
        assert postgres_vector.enable(fake_pg(tmp_path / 'postgres'), ('postgres', 'x'), 'orion', report) is False
    psql.assert_not_called()
    assert 'pgvector files are missing' in report.call_args.args[0]


def test_a_failing_statement_only_warns(tmp_path):
    archive, digest = fake_archive(tmp_path)
    postgres_vector.install(tmp_path / 'postgres', archive, digest)
    report = Mock()
    with patch('services.shared.postgres_auth._psql',
               side_effect=RuntimeError('PostgreSQL setup failed: could not load library "vector.dll"')):
        assert postgres_vector.enable(fake_pg(tmp_path / 'postgres'), ('postgres', 'x'), 'orion', report) is False
    message = report.call_args.args[0]
    assert 'pgvector could not be enabled' in message and 'vector.dll' in message


def test_enable_never_raises():
    """Whatever the database object is (a supervisor test's Mock included), the Hub's start goes on."""
    report = Mock()
    assert postgres_vector.enable(Mock(), ('postgres', 'x'), 'orion', report) is False
    report.assert_called_once()


def test_the_hub_supervisor_enables_it_after_securing_the_database():
    source = (Path(__file__).resolve().parents[1] / 'run_local.py').read_text(encoding='utf-8')
    secure = source.index('postgres_auth.secure(database, admin, app, database.database)')
    enable = source.index('postgres_vector.enable(database, admin, database.database)')
    assert secure < enable < source.index('def start_redis')
