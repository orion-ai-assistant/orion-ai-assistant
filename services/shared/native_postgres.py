"""Portable PostgreSQL adapter: data identity + OS ownership + authenticated SQL.

The process that starts a cluster owns it (Windows): pg_ctl start runs inside a kill-on-close job named
after the data directory, and only the owner keeps a handle. A clean stop shuts the server down; if the
owner ends without one (error, Task Manager, Windows signing out) Windows ends the server with it, and the
next start runs PostgreSQL's normal crash recovery. A running server whose owner is gone is shut down and
restarted as one's own, never reused ownerless.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import psutil

from services.shared.process_lifecycle import (
    InstanceLock, LifecycleError, ProcessReference, assert_port_free, canonical, job_adapter,
)

NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}


def owner_job_record(data):
    """The one job a cluster's owner holds: named after the data directory, so anyone can see whether a
    running server still has a live owner (the job exists only while its owner holds it)."""
    scope = {'kind': 'postgres', 'data': canonical(data)}
    return dict(v=1, scope=scope, name=job_adapter().job_name(scope))


def owned_start(command, data, lock_directory, *, timeout):
    """Run the pg_ctl start `command` so that the postmaster is born inside this process's job.

    Returns (job, result); the caller keeps `job` for as long as it owns the server. Off Windows: (None, result)."""
    if os.name != 'nt':
        return None, subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    job = job_adapter().Job(owner_job_record(data), create=True)
    try:
        cancel = Path(lock_directory) / ('cancel-' + str(uuid.uuid4()))  # Never created: the start is wanted.
        host = [sys._base_executable, '-I', '-S', str(Path(__file__).with_name('process_host.py')),
                job.record['name'], str(cancel), *command]
        # Windows pg_ctl may hand inherited pipe handles to its daemon.
        # A logfile + DEVNULL avoids communicate() waiting on that daemon.
        result = subprocess.run(host, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, **NO_WINDOW)
    except BaseException:
        job.close()  # A half-started server ends with the job.
        raise
    return job, result


class SettingDenied(LifecycleError):
    """The role may not read this server setting (it lacks pg_read_all_settings)."""


class NotAccepting(LifecycleError):
    """The server does not accept this authenticated connection (yet): starting, recovering or stopped."""


class Postgres:
    def __init__(self, ctl, data, port, user, database, password=None):
        self.ctl, self.data = Path(ctl).resolve(), Path(data).resolve()
        if os.name == 'nt' and not (str(self.ctl) + str(self.data)).isascii():
            from services.shared.windows_postgres import verify_unicode_runtime
            verify_unicode_runtime(self.ctl.parent.parent)
        self.port, self.user, self.database, self.password = int(port), user, database, password
        self.executable = self.ctl.with_name('postgres.exe' if os.name == 'nt' else 'postgres')
        self.psql = self.ctl.with_name('psql.exe' if os.name == 'nt' else 'psql')
        self.directory = self.data.parent / '.orion-lifecycle-postgres'
        self.job = None  # Held while this process owns the running server.

    def owner(self):
        pid_path = self.data / 'postmaster.pid'
        try:
            lines = pid_path.read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            return None
        try:
            pid, started = int(lines[0]), int(lines[2])
            if canonical(lines[1]) != canonical(self.data):
                raise LifecycleError('PostgreSQL data identity mismatch')
            process = psutil.Process(pid)
            if abs(process.create_time() - started) > 2:
                return None  # Stale lock file of a crashed postmaster; its PID now belongs to another process.
            if canonical(process.exe()) != canonical(self.executable):
                raise LifecycleError('PostgreSQL PID ownership mismatch; record retained')
            command = process.cmdline()
            if not any(canonical(arg) == canonical(self.data) for arg in command[1:] if not arg.startswith('-')):
                raise LifecycleError('PostgreSQL command does not identify this data directory')
            return ProcessReference.capture(pid)
        except psutil.NoSuchProcess:
            return None  # PostgreSQL itself decides whether its stale PID file can be removed.
        except (psutil.AccessDenied, OSError, ValueError, IndexError) as exc:
            raise LifecycleError('PostgreSQL ownership unavailable') from exc

    def sql(self, query):
        env = dict(os.environ, PGCONNECT_TIMEOUT='3', PGCLIENTENCODING='UTF8')
        if self.password is not None:
            env['PGPASSWORD'] = self.password
        result = subprocess.run([str(self.psql), '-h', '127.0.0.1', '-p', str(self.port),
                                 '-U', self.user, '-d', self.database, '-w', '-X', '-At', '-c', query],
                                capture_output=True, text=True, encoding='utf-8', env=env, timeout=5,
                                **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        if result.returncode:
            if 'pg_read_all_settings' in result.stderr:
                raise SettingDenied(query)
            raise NotAccepting('PostgreSQL authenticated readiness failed')
        return result.stdout.strip()

    def same_cluster(self):
        """The connection reaches the postmaster that owner() verified for this data directory."""
        try:
            return canonical(self.sql('SHOW data_directory')) == canonical(self.data)
        except SettingDenied:
            # A role without pg_read_all_settings (older developer databases) cannot read data_directory; the
            # server's start time is readable by every role and must equal the one in this cluster's postmaster.pid.
            started = int((self.data / 'postmaster.pid').read_text(encoding='utf-8').splitlines()[2])
            return abs(int(self.sql('SELECT extract(epoch FROM pg_postmaster_start_time())::bigint')) - started) <= 1

    def ready(self):
        owner = self.owner()
        if not owner:
            return False
        with ProcessReference(owner) as reference:
            status = subprocess.run([str(self.ctl), 'status', '-D', str(self.data)], capture_output=True,
                                    timeout=5, **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
            if status.returncode:
                return False
            if not self.same_cluster():
                raise LifecycleError('Connected to another PostgreSQL cluster')
            for setting in ('fsync', 'full_page_writes', 'synchronous_commit'):
                if self.sql('SHOW ' + setting) != 'on':
                    raise LifecycleError('PostgreSQL durability disabled: ' + setting)
            return reference.alive()

    def owned(self, owner=None):
        """The running server belongs to a live owner (this process or another): it is inside the cluster's
        owner job. Always False off Windows, where servers are not job-owned."""
        owner = owner or self.owner()
        if os.name != 'nt' or owner is None:
            return False
        with job_adapter().Job(owner_job_record(self.data)) as job:
            if not job.handle:
                return False
            try:
                with ProcessReference(owner) as reference:
                    return job.contains(reference.handle)
            except psutil.NoSuchProcess:
                return False

    def ensure(self):
        with InstanceLock(self.directory, 'operation'):
            owner = self.owner()
            if owner and (os.name != 'nt' or self.owned(owner)):
                # Ours, or a live owner's (it decides when the server stops): use it, never take it over.
                if not self.ready():
                    raise LifecycleError('Existing PostgreSQL is not ready')
                return
            if owner:
                # Its owner is gone (it ended before Windows could end the server, or an older Orion started it
                # outside any job): shut it down cleanly and start it again as this process's own.
                self._shutdown(owner)
            assert_port_free(self.port)
            if not (self.data / 'PG_VERSION').is_file() or not self.ctl.is_file():
                raise LifecycleError('PostgreSQL requires setup before start')
            # The log lives beside the data directory: crash recovery fsyncs every file inside it, and on Windows
            # the log that pg_ctl holds open made that walk retry for 30 s (a failed first start after power loss).
            command = [str(self.ctl), 'start', '-D', str(self.data), '-l', str(self.data.parent / 'server.log'),
                       '-o', f'-p {self.port} -c fsync=on -c full_page_writes=on -c synchronous_commit=on', '-w', '-t', '30']
            self.job, result = owned_start(command, self.data, self.directory, timeout=35)
            if result.returncode or not self._ready_soon():
                raise LifecycleError('PostgreSQL failed readiness; dependent services not started')

    def _ready_soon(self, timeout=10):
        # pg_ctl -w (Windows) accepts a postmaster.pid younger than 2 s as the new server's: right after a
        # server that had just started was killed, it reports ready while the new one is still recovering.
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self.ready():
                    return True
            except NotAccepting:
                if time.monotonic() >= deadline:
                    raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(.2)

    def _shutdown(self, owner):
        with ProcessReference(owner) as reference:
            result = subprocess.run([str(self.ctl), 'stop', '-D', str(self.data), '-m', 'fast', '-w', '-t', '30'],
                                    capture_output=True, timeout=35, **NO_WINDOW)
            if result.returncode:
                raise LifecycleError('PostgreSQL shutdown failed; data/PID retained')
            reference.wait(5)

    def stop(self):
        with InstanceLock(self.directory, 'operation'):
            owner = self.owner()
            if owner is not None:
                # A controlled stop first. If it fails, the error is raised and the server ends with its
                # owner at the latest (the owner job), never left running ownerless.
                self._shutdown(owner)
            if self.job is not None:
                self.job.close()
                self.job = None
            return True
