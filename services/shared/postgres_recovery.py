"""Finish PostgreSQL's crash recovery before a component starts it (power loss, killed process tree).

Found by the interruption drill: the first start after a hard kill runs crash recovery, which on Windows
fsyncs every file of the data directory. The Hub and Router keep `server.log` inside the data directory
and pg_ctl holds it open, so that walk hits a sharing violation and retries for 30 s - as long as the
components' `pg_ctl start -t 30`. The start fails ("PostgreSQL failed readiness") and an update health
check or rollback ends in repair_required. A stale postmaster.pid whose PID now belongs to another process
(PIDs are reused after a reboot) is refused by older component code as an ownership mismatch.

This runs from the retained bootstrap, so it also covers already-installed component versions: a data
directory with a postmaster.pid and no live postmaster is started once with its log OUTSIDE the data
directory and a generous timeout, then stopped cleanly. The component's own start then sees a cleanly shut
down cluster. Nothing is touched while a postmaster of that data directory is alive.
"""
import os
from pathlib import Path
import socket
import subprocess

import psutil

START_TIMEOUT = 300
FLAGS = {'creationflags': 0x08000000} if os.name == 'nt' else {}


def _live_postmaster(data):
    """The postmaster recorded in data/postmaster.pid is still running (same PID and start time)."""
    try:
        lines = (data / 'postmaster.pid').read_text(encoding='utf-8', errors='replace').splitlines()
        pid, started = int(lines[0]), int(lines[2])
    except (OSError, ValueError, IndexError):
        return False  # Unreadable lock file: PostgreSQL itself decides on its next start.
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - started) <= 2 and process.name().lower().startswith('postgres')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def crashed_clusters(record):
    """(pg_ctl, data directory) of every component data directory left with a stale postmaster.pid."""
    found = []
    for item in record['components']:
        data_root = Path(item['data_path'])
        if not data_root.is_dir():
            continue
        for lock in [*data_root.glob('*/postmaster.pid'), *data_root.glob('*/*/*/postmaster.pid')]:
            data = lock.parent
            if not (data / 'PG_VERSION').is_file() or _live_postmaster(data):
                continue
            name = 'pg_ctl.exe' if os.name == 'nt' else 'pg_ctl'
            hub_ctl = Path(item['code_path']) / '.local_db/postgres/bin' / name
            ctl = hub_ctl if hub_ctl.is_file() else next(Path(item['code_path']).rglob(name), None)
            if ctl is not None:
                found.append((ctl, data))
    return found


def recover(record, *, log_dir):
    """Run crash recovery for every crashed cluster of `record`; returns the data directories recovered.
    Raises RuntimeError when PostgreSQL cannot complete recovery (a genuine repair case)."""
    recovered = []
    for ctl, data in crashed_clusters(record):
        log = Path(log_dir) / ('postgres-recovery-' + data.parent.name + '.log')
        options = f'-p {_free_port()} -c listen_addresses=127.0.0.1'
        # This process owns the recovery server (native_postgres.owned_start): if it ends early, so does the server.
        from .native_postgres import owned_start
        job, start = owned_start([str(ctl), 'start', '-D', str(data), '-l', str(log), '-o', options, '-w',
                                  '-t', str(START_TIMEOUT)], data, data.parent, timeout=START_TIMEOUT + 30)
        try:
            stop = subprocess.run([str(ctl), 'stop', '-D', str(data), '-m', 'fast', '-w', '-t', '60'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=90, **FLAGS)
        finally:
            if job is not None:
                job.close()
        if start.returncode or stop.returncode or (data / 'postmaster.pid').exists():
            raise RuntimeError('PostgreSQL crash recovery did not finish')
        recovered.append(str(data))
    return recovered
