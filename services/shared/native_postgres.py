"""Portable PostgreSQL adapter: data identity + OS ownership + authenticated SQL.

The process that starts a cluster owns it (Windows): pg_ctl start runs inside a kill-on-close job named
after the data directory, and only the owner keeps a handle. A clean stop shuts the server down; if the
owner ends without one (error, Task Manager, Windows signing out) Windows ends the server with it, and the
next start runs PostgreSQL's normal crash recovery. A running server whose owner is gone is shut down and
restarted as one's own, never reused ownerless.
"""
import json
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


def fast_shutdown_signal(pid):
    """Ask a Windows postmaster for a fast shutdown, exactly as pg_ctl stop -m fast does (src/port/kill.c):
    the signal number travels through the server's own \\\\.\\pipe\\pgsignal_<pid> pipe. SIGINT (2) is a fast
    shutdown: new connections refused, open transactions rolled back, a shutdown checkpoint written.
    True when the server acknowledged the signal."""
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CallNamedPipeW.argtypes = [W.LPCWSTR, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD,
                                      ctypes.POINTER(W.DWORD), W.DWORD]
    signal, answer, received = ctypes.c_ubyte(2), ctypes.c_ubyte(0), W.DWORD(0)
    ok = kernel.CallNamedPipeW(f'\\\\.\\pipe\\pgsignal_{int(pid)}', ctypes.byref(signal), 1,
                               ctypes.byref(answer), 1, ctypes.byref(received), 1000)
    return bool(ok) and received.value == 1 and answer.value == 2


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


def owned_server(command, data, lock_directory, log):
    """Windows: run the postmaster `command` (postgres.exe itself) inside this process's job, with the stdlib host
    that joined the job staying alive as its parent.

    pg_ctl started the server through cmd.exe and exited: the server had no live parent, so Task Manager listed it
    on its own instead of under its owner (and so under the installation's Orion.exe). Returns (job, host process);
    the caller keeps `job` for as long as it owns the server."""
    job = job_adapter().Job(owner_job_record(data), create=True)
    try:
        cancel = Path(lock_directory) / ('cancel-' + str(uuid.uuid4()))  # Never created: the start is wanted.
        host = [sys._base_executable, '-I', '-S', str(Path(__file__).with_name('process_host.py')),
                job.record['name'], str(cancel), *command]
        with open(log, 'ab') as output:  # The server's own log, as with pg_ctl -l.
            process = subprocess.Popen(host, stdin=subprocess.DEVNULL, stdout=output, stderr=output, **NO_WINDOW)
    except BaseException:
        job.close()  # A half-started server ends with the job.
        raise
    return job, process


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
        self._server = None  # Windows: the stdlib host that started it, its live parent.

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

    def settings(self):
        """Everything readiness checks, read in one authenticated round trip (every psql start costs a process):
        the data directory, the durability settings and the server's start time. A role without
        pg_read_all_settings (older developer databases) does not see data_directory in pg_settings: None."""
        return json.loads(self.sql(
            "SELECT json_build_object("
            "'data_directory', (SELECT setting FROM pg_settings WHERE name = 'data_directory'), "
            "'fsync', current_setting('fsync'), 'full_page_writes', current_setting('full_page_writes'), "
            "'synchronous_commit', current_setting('synchronous_commit'), "
            "'started', extract(epoch FROM pg_postmaster_start_time())::bigint)"))

    def same_cluster(self, settings=None):
        """The connection reaches the postmaster that owner() verified for this data directory."""
        settings = settings or self.settings()
        if settings['data_directory'] is not None:
            return canonical(settings['data_directory']) == canonical(self.data)
        # Without data_directory: the server's start time is readable by every role and must equal the one in
        # this cluster's postmaster.pid.
        started = int((self.data / 'postmaster.pid').read_text(encoding='utf-8').splitlines()[2])
        return abs(int(settings['started']) - started) <= 1

    def ready(self):
        owner = self.owner()
        if not owner:
            return False
        with ProcessReference(owner) as reference:
            status = subprocess.run([str(self.ctl), 'status', '-D', str(self.data)], capture_output=True,
                                    timeout=5, **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
            if status.returncode:
                return False
            settings = self.settings()
            if not self.same_cluster(settings):
                raise LifecycleError('Connected to another PostgreSQL cluster')
            for setting in ('fsync', 'full_page_writes', 'synchronous_commit'):
                if settings[setting] != 'on':
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
            log = self.data.parent / 'server.log'
            if os.name == 'nt':
                command = [str(self.executable), '-D', str(self.data), '-p', str(self.port), '-c', 'fsync=on',
                           '-c', 'full_page_writes=on', '-c', 'synchronous_commit=on']
                self.job, self._server = owned_server(command, self.data, self.directory, log)
                if not self._ready_soon(40, server=self._server):  # pg_ctl's 30 s start wait + its 10 s readiness check.
                    raise LifecycleError('PostgreSQL failed readiness; dependent services not started')
                return
            command = [str(self.ctl), 'start', '-D', str(self.data), '-l', str(log),
                       '-o', f'-p {self.port} -c fsync=on -c full_page_writes=on -c synchronous_commit=on', '-w', '-t', '30']
            self.job, result = owned_start(command, self.data, self.directory, timeout=35)
            if result.returncode or not self._ready_soon():
                raise LifecycleError('PostgreSQL failed readiness; dependent services not started')

    def _ready_soon(self, timeout=10, server=None):
        # pg_ctl -w (Windows) accepts a postmaster.pid younger than 2 s as the new server's: right after a
        # server that had just started was killed, it reports ready while the new one is still recovering.
        deadline = time.monotonic() + timeout
        while True:
            if server is not None and server.poll() is not None:
                return False  # The server ended (port, configuration, data): its log says why.
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
            # Fast shutdown without starting a process: Windows refuses new processes while it signs out
            # (0xc0000142), and that is exactly when the server must still close cleanly.
            if os.name == 'nt' and fast_shutdown_signal(owner['pid']):
                try:
                    reference.wait(30)
                    return
                except subprocess.TimeoutExpired:
                    pass  # pg_ctl below reports the failure the usual way.
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
            if self._server is not None:
                try:
                    self._server.wait(5)  # Its host ends with the server; collect it.
                except subprocess.TimeoutExpired:
                    pass  # Ends with the job below at the latest.
                self._server = None
            if self.job is not None:
                self.job.close()
                self.job = None
            return True
