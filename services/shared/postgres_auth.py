"""PostgreSQL accepts only Orion's own programs: password (SCRAM) logins over this PC's loopback.

initdb ran with --auth=trust: every program and every Windows account on the PC could log in as the
superuser without a password, read every chat and run commands as the Orion user (COPY ... FROM PROGRAM).
Each installation now has its own random passwords for two roles: the superuser (`admin`, used only to set
things up) and the application's role (`app`). The app role owns its database but is no superuser, so even
through it nobody can run programs or read server files.

Whoever owns the data folder can always get back in, so an installation is never locked out: when its
passwords were never applied, or the file that keeps them was lost or edited, prepare() lets the server
start with setup access once more and secure() applies them again.

Shared by Hub and Router (scripts/lifecycle/sync_router_engine.py); `pg` is a native_postgres.Postgres."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import time

NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}
SECURE_HBA = ('# Orion: password (SCRAM) logins from this PC only. Written by postgres_auth.py.\n'
              'local all all scram-sha-256\n'
              'host all all 127.0.0.1/32 scram-sha-256\n'
              'host all all ::1/128 scram-sha-256\n')
# Only while postgres_auth applies passwords: the server listens on this PC alone either way.
SETUP_HBA = SECURE_HBA.replace('password (SCRAM) logins', 'setup access without passwords').replace(
    'scram-sha-256', 'trust')
MARKER = '.orion-postgres-auth.json'

# Every table, view and sequence of the app's database belongs to the app role.
OWNERSHIP = """ALTER SCHEMA public OWNER TO "{app}";
DO $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.oid::regclass AS name, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = '{app}')
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO %I', CASE item.relkind WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END, item.name, '{app}');
  END LOOP;
  -- A sequence that belongs to a column moved with its table above.
  FOR item IN
    SELECT c.oid::regclass AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'S'
      AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = '{app}')
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i'))
  LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO %I', item.name, '{app}');
  END LOOP;
END $$;
"""


def new_password():
    return secrets.token_urlsafe(32)


def _fingerprint(admin, app, database):
    return hashlib.sha256(json.dumps([*admin, *app, database]).encode()).hexdigest()


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None


def _write(path, text):
    temporary = path.with_name(path.name + '.orion-tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def _psql(pg, role, database, sql, check=True):
    env = dict(os.environ, PGPASSWORD=role[1], PGCONNECT_TIMEOUT='3', PGCLIENTENCODING='UTF8')
    # Passwords travel in the environment and on stdin, never in the command line.
    result = subprocess.run([str(pg.psql), '-h', '127.0.0.1', '-p', str(pg.port), '-U', role[0], '-d', database,
                             '-w', '-X', '-At', '-v', 'ON_ERROR_STOP=1'], input=sql, text=True,
                            encoding='utf-8', capture_output=True, env=env, timeout=60, **NO_WINDOW)
    if check and result.returncode:
        raise RuntimeError('PostgreSQL setup failed: ' + result.stderr.strip()[-300:])
    return result.returncode == 0


def _login(pg, role, database):
    return _psql(pg, role, database, 'SELECT 1;', check=False)


def _signal_reload(pg):
    result = subprocess.run([str(pg.ctl), 'reload', '-D', str(pg.data)], capture_output=True, timeout=15,
                            **NO_WINDOW)
    if result.returncode:
        raise RuntimeError('PostgreSQL configuration reload failed')


def secured(pg, admin, app, database):
    """Passwords are required and they are the ones in `admin`/`app` (checked without the server)."""
    try:
        applied = json.loads(_read(pg.data.parent / MARKER) or '{}').get('applied')
    except ValueError:
        applied = None
    return _read(pg.data / 'pg_hba.conf') == SECURE_HBA and applied == _fingerprint(admin, app, database)


def prepare(pg, admin, app, database):
    """Before the server starts: passwords it does not know yet need setup access until secure() runs."""
    hba = pg.data / 'pg_hba.conf'
    if _read(hba) == SECURE_HBA and not secured(pg, admin, app, database):
        _write(hba, SETUP_HBA)
        if pg.owner() is not None:
            _signal_reload(pg)


def secure(pg, admin, app, database):
    """After the server started: apply passwords and ownership while setup access is on, then require them.

    True when only password logins work. On a failed switch the setup access stays, as before this change,
    and False is returned for the caller to report: the application keeps its database."""
    for name in (admin[0], app[0], database):
        if not re.fullmatch(r'[a-z_][a-z0-9_]*', name):
            raise ValueError('Invalid PostgreSQL identifier')
    if secured(pg, admin, app, database) and _login(pg, app, database):
        return True  # The usual start: one login.
    hba = pg.data / 'pg_hba.conf'
    if _read(hba) != SETUP_HBA:
        # Legacy trust (older installations) or passwords that no longer match: setup access once more.
        _write(hba, SETUP_HBA)
        _signal_reload(pg)
        _wait(lambda: _login(pg, admin, 'postgres'))
    literal = lambda value: "'" + value.replace("'", "''") + "'"
    _psql(pg, admin, 'postgres', (
        f'ALTER ROLE "{admin[0]}" WITH PASSWORD {literal(admin[1])};\n'
        f"SELECT 'CREATE ROLE \"{app[0]}\" LOGIN' WHERE NOT EXISTS "
        f"(SELECT 1 FROM pg_roles WHERE rolname = '{app[0]}') \\gexec\n"
        f'ALTER ROLE "{app[0]}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS '
        f'PASSWORD {literal(app[1])};\n'
        f"SELECT 'CREATE DATABASE \"{database}\" OWNER \"{app[0]}\"' WHERE NOT EXISTS "
        f"(SELECT 1 FROM pg_database WHERE datname = '{database}') \\gexec\n"
        f'ALTER DATABASE "{database}" OWNER TO "{app[0]}";\n'))
    _psql(pg, admin, database, OWNERSHIP.format(app=app[0]))
    _write(hba, SECURE_HBA)
    wrong = (app[0], new_password())
    try:
        _psql(pg, admin, 'postgres', 'SELECT pg_reload_conf();')
        # Both roles log in with their passwords, and a wrong password is refused.
        switched = _wait(lambda: _login(pg, app, database) and _login(pg, admin, 'postgres')
                         and not _login(pg, wrong, database))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        switched = False
    if switched:
        _write(pg.data.parent / MARKER, json.dumps({'v': 1, 'applied': _fingerprint(admin, app, database)}))
        return True
    _write(hba, SETUP_HBA)
    _signal_reload(pg)
    return False


def _wait(check, timeout=10):
    """A reload reaches new connections a moment later."""
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            return False
        time.sleep(.2)
    return True
