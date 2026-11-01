"""A real portable PostgreSQL moves from trust to password logins, keeps its data and never locks Orion out."""
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from services.shared import postgres_auth
from services.shared.native_postgres import Postgres
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BINARIES = ROOT / '.local_db/postgres/bin'
real = pytest.mark.skipif(os.name != 'nt' or not (BINARIES / 'initdb.exe').exists(),
                            reason="Real Windows PostgreSQL binaries unavailable")


def psql(db, user, password, sql, database='appdb'):
    env = dict(os.environ, PGPASSWORD=password or '', PGCONNECT_TIMEOUT='3')
    if password is None:
        env.pop('PGPASSWORD')
    return subprocess.run([str(db.psql), '-h', '127.0.0.1', '-p', str(db.port), '-U', user, '-d', database,
                           '-w', '-X', '-At', '-c', sql], capture_output=True, text=True, env=env, timeout=30)


@pytest.fixture
def legacy(tmp_path):
    """An installation as initdb --auth=trust left it: the app's tables owned by the superuser."""
    data = tmp_path / 'db' / 'data'
    data.parent.mkdir()
    subprocess.run([str(BINARIES / 'initdb.exe'), '-D', str(data), '-U', 'owner', '--auth=trust', '--locale=C',
                    '-E', 'UTF8'], capture_output=True, timeout=90, check=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    db = Postgres(BINARIES / 'pg_ctl.exe', data, port, 'owner', 'postgres')
    db.ensure()
    assert psql(db, 'owner', None, 'CREATE DATABASE appdb;', 'postgres').returncode == 0
    assert psql(db, 'owner', None, 'CREATE TABLE chats (id serial PRIMARY KEY, body text); '
                                   "INSERT INTO chats (body) VALUES ('kept');").returncode == 0
    try:
        yield db
    finally:
        db.stop()


@real
def test_trust_installation_moves_to_passwords_and_keeps_its_data(legacy):
    admin, app = ('owner', 'admin-secret'), ('app', 'app-secret')
    assert postgres_auth.secure(legacy, admin, app, 'appdb')
    assert postgres_auth.secured(legacy, admin, app, 'appdb')
    assert (legacy.data / 'pg_hba.conf').read_text(encoding='utf-8') == postgres_auth.SECURE_HBA
    assert psql(legacy, 'app', 'app-secret', 'SELECT body FROM chats').stdout.strip() == 'kept'
    # Without the password, or with a wrong one, nobody gets in: not as the app, not as the superuser.
    for user, password in (('app', None), ('app', 'orion'), ('owner', None), ('owner', 'app-secret')):
        assert psql(legacy, user, password, 'SELECT 1').returncode != 0, (user, password)
    # The app owns its data but is no superuser: no programs, no server files.
    assert psql(legacy, 'app', 'app-secret', "SELECT rolsuper FROM pg_roles WHERE rolname = 'app'").stdout.strip() == 'f'
    assert psql(legacy, 'app', 'app-secret', "SELECT tableowner FROM pg_tables WHERE tablename = 'chats'").stdout.strip() == 'app'
    assert psql(legacy, 'app', 'app-secret', "INSERT INTO chats (body) VALUES ('new')").returncode == 0
    for attack in ("COPY chats (body) FROM PROGRAM 'whoami'", "SELECT pg_read_file('postgresql.conf')",
                   'CREATE ROLE intruder SUPERUSER', 'ALTER ROLE app SUPERUSER'):
        assert psql(legacy, 'app', 'app-secret', attack).returncode != 0, attack
    assert psql(legacy, 'owner', 'admin-secret', 'SHOW listen_addresses', 'postgres').stdout.strip() == 'localhost'


@real
def test_next_start_needs_nothing_and_still_works(legacy):
    admin, app = ('owner', 'admin-secret'), ('app', 'app-secret')
    assert postgres_auth.secure(legacy, admin, app, 'appdb')
    legacy.stop()
    postgres_auth.prepare(legacy, admin, app, 'appdb')
    assert (legacy.data / 'pg_hba.conf').read_text(encoding='utf-8') == postgres_auth.SECURE_HBA
    legacy.password, legacy.user = 'admin-secret', 'owner'
    legacy.ensure()
    assert postgres_auth.secure(legacy, admin, app, 'appdb')
    assert psql(legacy, 'app', 'app-secret', 'SELECT count(*) FROM chats').stdout.strip() == '1'


@real
def test_lost_password_file_never_locks_the_installation_out(legacy):
    assert postgres_auth.secure(legacy, ('owner', 'admin-secret'), ('app', 'app-secret'), 'appdb')
    legacy.stop()
    # The file with the passwords is gone: the installation creates new ones and applies them.
    admin, app = ('owner', postgres_auth.new_password()), ('app', postgres_auth.new_password())
    postgres_auth.prepare(legacy, admin, app, 'appdb')
    legacy.password = admin[1]
    legacy.ensure()
    assert postgres_auth.secure(legacy, admin, app, 'appdb')
    assert psql(legacy, 'app', app[1], 'SELECT body FROM chats').stdout.strip() == 'kept'
    assert psql(legacy, 'app', 'app-secret', 'SELECT 1').returncode != 0
    marker = json.loads((legacy.data.parent / postgres_auth.MARKER).read_text(encoding='utf-8'))
    assert app[1] not in json.dumps(marker) and admin[1] not in json.dumps(marker)


@real
def test_first_start_creates_the_database_for_the_app(legacy):
    admin, app = ('owner', 'admin-secret'), ('router_app', 'app-secret')
    assert postgres_auth.secure(legacy, admin, app, 'fresh')
    assert psql(legacy, 'router_app', 'app-secret', 'CREATE TABLE t (x int); SELECT 1', 'fresh').returncode == 0


@real
def test_identifiers_are_checked_before_any_sql(legacy):
    with pytest.raises(ValueError):
        postgres_auth.secure(legacy, ('owner', 'x'), ('app"; DROP', 'y'), 'appdb')


def test_hub_replaces_the_shipped_password_once_and_every_caller_uses_it(tmp_path, monkeypatch):
    from services.shared import native_services
    (tmp_path / 'services').mkdir()
    local = tmp_path / 'services/.env.global.local'
    local.write_text('JWT_SECRET_KEY=x\nPOSTGRES_PASSWORD=orion\n', encoding='utf-8')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'orion')  # As a source checkout's .env.global hands it down.
    with patch('services.shared.runtime_layout.data_root', return_value=tmp_path):
        first = native_services.hub_database_passwords(tmp_path)
        assert first == native_services.hub_database_passwords(tmp_path)  # Created once, then kept.
        assert 'orion' not in first.values() and first['POSTGRES_PASSWORD'] != first['POSTGRES_ADMIN_PASSWORD']
        env = {'POSTGRES_PORT': '5445', 'POSTGRES_USER': 'orion', 'POSTGRES_DB': 'orion', 'POSTGRES_PASSWORD': 'orion'}
        db = native_services.hub_database(tmp_path, lambda key, default=None, **kw: env.get(key, default))
        assert db.password == first['POSTGRES_PASSWORD']
    assert local.read_text(encoding='utf-8').startswith('JWT_SECRET_KEY=x')


def test_a_configured_hub_password_is_kept(tmp_path, monkeypatch):
    from services.shared import native_services
    monkeypatch.setenv('POSTGRES_PASSWORD', 'fixture-password')
    with patch('services.shared.runtime_layout.data_root', return_value=tmp_path):
        assert native_services.hub_database_passwords(tmp_path)['POSTGRES_PASSWORD'] == 'fixture-password'
