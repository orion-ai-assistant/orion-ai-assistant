"""Portable PostgreSQL adapter: data identity + OS ownership + authenticated SQL."""
import os
from pathlib import Path
import subprocess
import time

import psutil

from services.shared.process_lifecycle import (
    InstanceLock, LifecycleError, ProcessReference, assert_port_free, canonical,
)


class Postgres:
    def __init__(self, ctl, data, port, user, database, password=None):
        self.ctl, self.data = Path(ctl).resolve(), Path(data).resolve()
        self.port, self.user, self.database, self.password = int(port), user, database, password
        self.executable = self.ctl.with_name('postgres.exe' if os.name == 'nt' else 'postgres')
        self.psql = self.ctl.with_name('psql.exe' if os.name == 'nt' else 'psql')
        self.directory = self.data.parent / '.orion-lifecycle-postgres'

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
            if abs(process.create_time() - started) > 2 or canonical(process.exe()) != canonical(self.executable):
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
        env = dict(os.environ, PGCONNECT_TIMEOUT='3')
        if self.password is not None:
            env['PGPASSWORD'] = self.password
        result = subprocess.run([str(self.psql), '-h', '127.0.0.1', '-p', str(self.port),
                                 '-U', self.user, '-d', self.database, '-w', '-X', '-At', '-c', query],
                                capture_output=True, text=True, env=env, timeout=5,
                                **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        if result.returncode:
            raise LifecycleError('PostgreSQL authenticated readiness failed')
        return result.stdout.strip()

    def ready(self):
        owner = self.owner()
        if not owner:
            return False
        with ProcessReference(owner) as reference:
            status = subprocess.run([str(self.ctl), 'status', '-D', str(self.data)], capture_output=True,
                                    timeout=5, **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
            if status.returncode:
                return False
            if canonical(self.sql('SHOW data_directory')) != canonical(self.data):
                raise LifecycleError('Connected to another PostgreSQL cluster')
            for setting in ('fsync', 'full_page_writes', 'synchronous_commit'):
                if self.sql('SHOW ' + setting) != 'on':
                    raise LifecycleError('PostgreSQL durability disabled: ' + setting)
            return reference.alive()

    def ensure(self):
        with InstanceLock(self.directory, 'operation'):
            if self.owner():
                if not self.ready():
                    raise LifecycleError('Existing PostgreSQL is not ready')
                return
            assert_port_free(self.port)
            if not (self.data / 'PG_VERSION').is_file() or not self.ctl.is_file():
                raise LifecycleError('PostgreSQL requires setup before start')
            command = [str(self.ctl), 'start', '-D', str(self.data), '-l', str(self.data / 'server.log'),
                       '-o', f'-p {self.port} -c fsync=on -c full_page_writes=on -c synchronous_commit=on', '-w', '-t', '30']
            # Windows pg_ctl may hand inherited pipe handles to its daemon.
            # A logfile + DEVNULL avoids communicate() waiting on that daemon.
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=35,
                                    **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
            if result.returncode or not self.ready():
                raise LifecycleError('PostgreSQL failed readiness; dependent services not started')

    def stop(self):
        with InstanceLock(self.directory, 'operation'):
            owner = self.owner()
            if owner is None:
                return True
            with ProcessReference(owner) as reference:
                result = subprocess.run([str(self.ctl), 'stop', '-D', str(self.data), '-m', 'fast', '-w', '-t', '30'],
                                        capture_output=True, timeout=35,
                                        **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
                # No forced DB kill. A failed controlled stop retains all evidence.
                if result.returncode:
                    raise LifecycleError('PostgreSQL shutdown failed; data/PID retained')
                reference.wait(5)
            return True
