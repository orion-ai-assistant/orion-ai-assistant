"""A PostgreSQL killed with its tree (power loss) must not turn the next start into a failure.

The real start/recovery is exercised by drill_interruptions.py (health_check scenario); this checks the
decisions without a database."""
import os
from pathlib import Path
import time

import psutil

from services.shared import postgres_recovery
from services.shared.native_postgres import Postgres


def cluster(tmp_path, pid, started, kind='hub'):
    code, data_root = tmp_path / kind / 'code', tmp_path / kind / 'data'
    data = data_root / '.local_db/postgres/data'
    data.mkdir(parents=True)
    (data / 'PG_VERSION').write_text('16')
    (data / 'postmaster.pid').write_text(f'{pid}\n{data}\n{started}\n5432\n')
    ctl = code / '.local_db/postgres/bin' / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl')
    ctl.parent.mkdir(parents=True)
    ctl.write_bytes(b'')
    return {'kind': kind, 'code_path': str(code), 'data_path': str(data_root)}, ctl, data


def test_a_stale_lock_is_recovered_and_a_live_postmaster_is_never_touched(tmp_path):
    me = psutil.Process()
    # This PID is alive, but it started at another time: a reused PID after a crash -> stale.
    stale, ctl, data = cluster(tmp_path, os.getpid(), int(me.create_time()) - 3600)
    assert postgres_recovery.crashed_clusters({'components': [stale]}) == [(ctl, data)]
    # Alive with the recorded start time but not a postgres process: also stale.
    reused, *_ = cluster(tmp_path, os.getpid(), int(me.create_time()), kind='router')
    assert len(postgres_recovery.crashed_clusters({'components': [reused]})) == 1
    # A cleanly stopped cluster (no postmaster.pid) needs nothing.
    (data / 'postmaster.pid').unlink()
    assert postgres_recovery.crashed_clusters({'components': [stale]}) == []


def test_component_code_treats_a_reused_pid_as_a_stale_lock(tmp_path):
    _, ctl, data = cluster(tmp_path, os.getpid(), int(time.time()) - 86400)
    database = Postgres.__new__(Postgres)
    database.ctl, database.data, database.executable = ctl, data, ctl.with_name('postgres.exe')
    assert database.owner() is None  # Was: LifecycleError('PostgreSQL PID ownership mismatch').
