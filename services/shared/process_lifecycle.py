"""Native process ownership, locks and supervisor state (schema 1).

Canonical source. Router vendors this exact module for independent installations.
No port, PID file or directory substring grants permission to signal a process.

One owner per process tree: the supervisor holds the only lasting handle of every job its children
run in, so they end with it, whatever ends it. A new supervisor never adopts survivors of an older
one; it stops whatever an earlier run left behind and starts clean.
"""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import psutil


class LifecycleError(RuntimeError):
    pass


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


def durable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value).encode())
        stream.flush()
        os.fsync(stream.fileno())
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/antivirus may deny replace for a while (a freshly extracted release is
                # scanned on first use). Once that is over, rewrite in place: state files are small and
                # every reader tolerates a retry, while failing here made a clean Router shutdown "fail".
                if time.monotonic() >= deadline:
                    path.write_bytes(temporary.read_bytes())
                    break
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    deadline = time.monotonic() + 2
    while True:
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            # A concurrent Windows replace can briefly deny an open, too.
            if time.monotonic() >= deadline:
                raise LifecycleError('State access denied; repair required') from exc
            time.sleep(.01)
        except (OSError, ValueError) as exc:
            raise LifecycleError('State unreadable; repair required') from exc


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes as W
    K = ctypes.WinDLL('kernel32', use_last_error=True)
    A = ctypes.WinDLL('advapi32', use_last_error=True)
    K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    K.OpenProcess.restype = W.HANDLE
    K.CloseHandle.argtypes = [W.HANDLE]
    K.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    K.WaitForSingleObject.restype = W.DWORD
    K.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
    K.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME)]
    K.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
    K.CreateMutexW.restype = W.HANDLE
    K.ReleaseMutex.argtypes = [W.HANDLE]
    K.LocalFree.argtypes = [ctypes.c_void_p]
    A.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(W.DWORD)]

    @lru_cache(maxsize=1)
    def user_key():
        # No shell, no mutable environment identity. Used for mutex DACL and name.
        import csv
        output = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True, creationflags=0x08000000)
        return next(csv.reader(output.splitlines()))[1]

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [('length', W.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', W.BOOL)]

    def native_started(handle):
        values = [W.FILETIME() for _ in range(4)]
        if not K.GetProcessTimes(handle, *[ctypes.byref(value) for value in values]):
            raise ctypes.WinError(ctypes.get_last_error())
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
else:
    def user_key():
        return str(os.getuid())


class InstanceLock:
    """OS-owned lock, separate names for operation and supervisor lifetime."""
    def __init__(self, directory, kind, timeout=15):
        self.directory, self.kind, self.timeout = Path(directory), kind, timeout
        self.handle = None
        self.abandoned = False

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            sid = user_key()
            descriptor = ctypes.c_void_p()
            if not A.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    f'D:P(A;;GA;;;{sid})', 1, ctypes.byref(descriptor), None):
                raise LifecycleError('Cannot restrict lifecycle mutex')
            try:
                attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
                name = 'Local\\Orion-' + digest([sid, canonical(self.directory), self.kind])
                self.handle = K.CreateMutexW(ctypes.byref(attrs), False, name)
                if not self.handle:
                    raise LifecycleError('Mutex access denied')
            finally:
                K.LocalFree(descriptor)
            result = K.WaitForSingleObject(self.handle, int(self.timeout * 1000))
            self.abandoned = result == 0x80
            if result not in (0, 0x80):
                K.CloseHandle(self.handle)
                self.handle = None
                raise LifecycleError('Lifecycle busy or mutex inaccessible')
        else:
            import fcntl
            self.handle = (self.directory / (self.kind + '.lock')).open('a+b')
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.handle.close()
                        raise LifecycleError('Lifecycle busy')
                    time.sleep(.05)
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            if os.name == 'nt':
                K.ReleaseMutex(self.handle)
                K.CloseHandle(self.handle)
            else:
                self.handle.close()
            self.handle = None


class ProcessReference:
    """Open a Windows handle *before* validation; force/wait use that handle."""
    def __init__(self, record):
        self.record, self.handle = record, None
        self.pid = record['pid']
        if os.name == 'nt':
            self.handle = K.OpenProcess(0x100000 | 0x1000 | 0x1, False, self.pid)
            if not self.handle:
                error = ctypes.get_last_error()
                if error == 87:
                    raise psutil.NoSuchProcess(self.pid)
                raise LifecycleError('Process access denied; ownership unknown')
        try:
            process = psutil.Process(self.pid)
            actual = self.describe(process)
            if any(actual[key] != record[key] for key in ('started', 'exe', 'command_hash', 'user')):
                raise LifecycleError('Process identity mismatch; no signal sent')
            if self.handle and native_started(self.handle) != record['native_started']:
                raise LifecycleError('Process handle identity mismatch')
            self.process = process
        except Exception:
            self.close()
            raise

    @staticmethod
    def describe(process):
        return dict(pid=process.pid, started=process.create_time(), exe=canonical(process.exe()),
                    command_hash=digest(process.cmdline()), user=process.username())

    @classmethod
    def capture(cls, pid):
        record = cls.describe(psutil.Process(pid))
        if os.name == 'nt':
            handle = K.OpenProcess(0x1000, False, pid)
            if not handle:
                raise LifecycleError('Cannot capture process handle')
            try:
                record['native_started'] = native_started(handle)
                seconds = record['native_started'] / 10000000 - 11644473600
                if abs(seconds - record['started']) > .001:
                    raise LifecycleError('PID changed during capture')
            finally:
                K.CloseHandle(handle)
        with cls(record):
            pass
        return record

    def alive(self):
        if self.handle:
            return K.WaitForSingleObject(self.handle, 0) == 0x102
        return self.process.is_running() and self.process.status() != psutil.STATUS_ZOMBIE

    def wait(self, timeout):
        if self.handle:
            result = K.WaitForSingleObject(self.handle, int(timeout * 1000))
            if result == 0:
                return
            if result != 0x102:
                raise LifecycleError('Process wait failed')
            raise subprocess.TimeoutExpired('owned process', timeout)
        try:
            self.process.wait(timeout)
        except psutil.TimeoutExpired as exc:
            raise subprocess.TimeoutExpired('owned process', timeout) from exc

    def force(self):
        if self.handle:
            if self.alive() and not K.TerminateProcess(self.handle, 1):
                raise LifecycleError('Owned process termination failed')
        else:
            # psutil checks creation time again before signaling (Windows uses handles).
            self.process.kill()

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def living(record):
    if record is None:
        return False
    try:
        with ProcessReference(record) as reference:
            return reference.alive()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as exc:
        raise LifecycleError('Process access denied; ownership unknown') from exc


def job_adapter():
    from importlib import import_module
    return import_module(__package__ + '.windows_job')


def child_running(child):
    """The root application host is alive, with verified containment if present."""
    if child.get('job'):
        with job_adapter().Job(child['job']) as job:
            return job.root_alive(child['process'])
    return living(child['process'])


def child_tree_alive(child):
    if child.get('job'):
        with job_adapter().Job(child['job']) as job:
            job.root_alive(child['process'])  # A live root must belong to this job.
            return bool(job.pids())
    return living(child['process']) or any(living(item) for item in child.get('descendants', []))


def assert_port_free(port):
    # Diagnostic only; no process or container is ever stopped to free a port.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', int(port)))
        except OSError as exc:
            raise LifecycleError(f'Port conflict: {port}') from exc


def owns_listener(record, port):
    with ProcessReference(record) as reference:
        # Inspection failure is ownership unknown, never a healthy result.
        if not reference.alive():
            return False
        # Windows venv's python.exe is a launcher; its verified child owns sockets.
        processes = [reference.process, *reference.process.children(recursive=True)]
        for process in processes:
            try:
                child_record = ProcessReference.capture(process.pid)
                if child_record['user'] != record['user']:
                    raise LifecycleError('Listener child user mismatch')
                with ProcessReference(child_record) as child:
                    connections = getattr(child.process, 'net_connections', None) or child.process.connections
                    if any(item.status == psutil.CONN_LISTEN and item.laddr.port == int(port)
                           for item in connections(kind='tcp')):
                        return reference.alive()
            except psutil.NoSuchProcess:
                continue
        return False


def wait_health(child, port, *, certificate=None, identity=None, timeout=30,
                path='/health', json_response=True):
    import ssl
    import urllib.request
    context = ssl.create_default_context(cafile=str(certificate)) if certificate else None
    scheme = 'https' if certificate else 'http'
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('Readiness path must be local')
    endpoint = '/api/v1/tls/identity' if identity else path
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise LifecycleError('Service exited before readiness')
        if owns_listener(child.child['process'], port):
            try:
                request = urllib.request.Request(f'{scheme}://127.0.0.1:{port}{endpoint}')
                # Loopback maintenance must not use user HTTP proxy settings.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=context))
                with opener.open(request, timeout=1) as response:
                    value = json.load(response) if json_response or identity else {}
                    if response.status == 200 and (not identity or value.get('id') == identity):
                        return
            except (OSError, ValueError):
                pass
        time.sleep(.1)
    raise LifecycleError('Identity/health readiness timeout')


def state_dir(root, component):
    from .runtime_layout import state_root
    return state_root(Path(root).resolve()) / component


class Runtime:
    """Supervisor owns lifetime lock; callers serialize with a distinct op lock."""
    def __init__(self, root, component):
        self.root, self.component = Path(root).resolve(), component
        self.directory = state_dir(root, component)
        self.path = self.directory / 'processes.json'
        self.children = {}
        self.jobs = {}
        self.lock = InstanceLock(self.directory, 'instance', timeout=0)

    def __enter__(self):
        self.lock.__enter__()
        try:
            identity = read_json(self.directory / 'identity.json')
            if identity is None:
                identity = dict(v=1, id=str(uuid.uuid4()), root=canonical(self.root), component=self.component)
                durable_json(self.directory / 'identity.json', identity)
            if identity.get('v') != 1 or identity.get('root') != canonical(self.root) or identity.get('component') != self.component:
                raise LifecycleError('Component identity mismatch')
            self.identity = identity
            previous = self.read()
            self.previous_database = previous.get('database') if previous else None
            if previous:
                if previous.get('status') != 'stopped' and living(previous['supervisor']):
                    raise LifecycleError('A previous supervisor is still alive')
                # Ownerless leftovers (an older Orion whose hosts kept their own job handles, or a run that
                # died between two steps): stopped here, never adopted. This run starts every child itself.
                for child in previous['children'].values():
                    if child_tree_alive(child):
                        ManagedChild(child).stop()
            self.run_id = str(uuid.uuid4())
            self.supervisor = ProcessReference.capture(os.getpid())
            self.status = 'starting'
            self.save()
            return self
        except Exception:
            for job in self.jobs.values():
                job.close()
            self.jobs.clear()
            self.lock.__exit__()
            raise

    def read(self):
        value = read_json(self.path)
        if value is not None and (value.get('v') != 1 or value.get('component') != self.component
                                  or value.get('root') != canonical(self.root)):
            raise LifecycleError('Invalid process record')
        if value is not None:
            for child in value['children'].values():
                if child.get('job'):
                    if os.name != 'nt':
                        raise LifecycleError('Windows process job requires Windows recovery')
                    identity = read_json(self.directory / 'identity.json')
                    if not identity or value['installation_id'] != identity['id']:
                        raise LifecycleError('Process job installation identity mismatch')
                    expected = job_adapter().metadata(self.root, self.component, identity['id'], child['instance_id'])
                    if child['job'] != expected or child['control'] not in (None, str(self.directory / ('child-' + child['instance_id']))):
                        raise LifecycleError('Process job scope mismatch')
        return value

    def save(self):
        from .runtime_layout import component
        installed = component(self.root)
        release = installed['package']['id'] if installed else 'legacy-source'
        database = getattr(self, 'database', None)
        database_record = (dict(ctl=str(database.ctl), data=str(database.data), port=database.port,
                                user=database.user, database=database.database) if database is not None
                           else getattr(self, 'previous_database', None))
        durable_json(self.path, dict(v=1, component=self.component, installation_id=self.identity['id'],
                     root=canonical(self.root), run_id=self.run_id, release=release,
                     supervisor=self.supervisor, children=self.children, database=database_record, status=self.status))

    def requested(self):
        return (self.directory / ('stop-' + self.run_id)).exists()

    def launch(self, label, argv, *, cwd=None, env=None, python=False, node=False, **options):
        target_hash = digest([list(map(str, argv)), canonical(cwd or self.root)])
        old = self.children.get(label)
        if old and child_running(old):
            if old['target_hash'] != target_hash:
                raise LifecycleError('Live child configuration changed; stop before restarting')
            return ManagedChild(old)
        if old and child_tree_alive(old):
            raise LifecycleError('Live descendants require reconciliation before replacement')
        if label in self.jobs:
            self.jobs.pop(label).close()
        instance_id = str(uuid.uuid4())
        control = self.directory / ('child-' + instance_id)
        command = list(map(str, argv))
        if python:
            command = [command[0], str(Path(__file__).with_name('process_child.py').resolve()), str(control), *command[1:]]
        elif node:
            command = [command[0], '--require', str(Path(__file__).with_name('process_child.cjs').resolve()), *command[1:]]
            env = dict(env or os.environ, ORION_LIFECYCLE_CONTROL=str(control))
        entrypoint = str(argv[0])
        # Use the original target, never wrapper/control paths as app identity.
        if python or node:
            entrypoint = str(argv[2]) if argv[1] == '-m' else canonical(Path(cwd or self.root) / argv[1])
        child = dict(process=None, instance_id=instance_id, entrypoint=entrypoint,
                     target_hash=target_hash, control=str(control) if python or node else None)
        job = None
        proc = None
        try:
            if os.name == 'nt':
                child['job'] = job_adapter().metadata(self.root, self.component, self.identity['id'], instance_id)
                job = job_adapter().Job(child['job'], create=True)
                # Persist authority before any application can run. Recovery can
                # stop a live job even if the root PID was not saved yet.
                self.children[label] = child
                self.save()
                command = [sys._base_executable, '-I', '-S', str(Path(__file__).with_name('process_host.py')),
                           child['job']['name'], str(job.cancel_path), *command]
            proc = subprocess.Popen(command, cwd=cwd or self.root, env=env, **options)
            record = ProcessReference.capture(proc.pid)
            if job is not None:
                deadline = time.monotonic() + 10
                with ProcessReference(record) as reference:
                    while not job.contains(reference.handle):
                        if not reference.alive() or time.monotonic() >= deadline:
                            raise LifecycleError('Process host failed containment before application readiness')
                        time.sleep(.01)
            # Until containment is verified, readers see a pending launch, not
            # a live root that appears to be outside its expected job.
            child['process'] = record
            self.children[label] = child
            self.save()
            if job is not None:
                self.jobs[label] = job
                job = None  # Parent retains the handle through Runtime lifetime.
        except Exception:
            # A created Popen handle is exact; the new job is exact as well.
            # Never leave an unrecorded application after a failed launch/save.
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            if job is not None:
                job.terminate()
            raise
        finally:
            if job is not None:
                job.close()
        return ManagedChild(child, proc)

    def ready(self):
        if not self.children:
            raise LifecycleError('No children registered for readiness')
        if not all(child_running(child) for child in self.children.values()):
            self.status = 'starting'
            self.save()
            return False
        self.status = 'ready'
        self.save()
        return True

    def refresh_children(self):
        for child in self.children.values():
            if child.get('job'):
                with job_adapter().Job(child['job']) as job:
                    job.root_alive(child['process'])
                    # Only current kernel members; historical PIDs cannot block
                    # stop or authorize signaling after reuse by another process.
                    child['descendants'] = [item for item in job.records()
                        if child['process'] is None or item['pid'] != child['process']['pid']]
                continue
            try:
                with ProcessReference(child['process']) as reference:
                    if not reference.alive():
                        continue
                    descendants = child.setdefault('descendants', [])
                    known = {(item['pid'], item['started']) for item in descendants}
                    for process in reference.process.children(recursive=True):
                        try:
                            item = ProcessReference.capture(process.pid)
                            if item['user'] != child['process']['user']:
                                raise LifecycleError('Child user identity mismatch')
                            if (item['pid'], item['started']) not in known:
                                descendants.append(item)
                        except psutil.NoSuchProcess:
                            pass
            except psutil.NoSuchProcess:
                continue
        self.save()

    def stop_children(self, timeout=10):
        """Stop every child; never give up halfway. Each step is attempted even when an earlier one failed,
        and whatever a graceful stop leaves alive is ended through its job. Raises only when a child is still
        alive afterwards (the database is then left alone: its clients may still be writing)."""
        errors = []
        def attempt(step, *args):
            try:
                step(*args)
            except Exception as error:
                errors.append(error)
                print(f'[!] Stop step failed: {type(error).__name__}: {error}', flush=True)
        # Startup may fail before the steady-state polling loop ever runs.
        # Capture existing descendants while their parents are still alive.
        attempt(self.refresh_children)
        self.status = 'stopping'
        self.save()
        before_stop = getattr(self, 'before_stop', None)
        if before_stop:
            attempt(before_stop)
        # Python applications first, database-like native children last. The Python applications are
        # independent of each other: they get their stop signal together and share one deadline (a serial
        # stop added up to 10 s per child that waited on an open connection - the >20 s tray quit).
        python = [child for child in self.children.values() if child['control']]
        if python:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(len(python)) as pool:
                for future in [pool.submit(ManagedChild(child).stop, timeout) for child in python]:
                    attempt(future.result)
        for child in self.children.values():
            if not child['control']:
                before_native = getattr(self, 'before_native_stop', None)
                if before_native:
                    attempt(before_native, child)
                attempt(ManagedChild(child).stop, timeout)
        left = []
        for label, child in self.children.items():
            try:
                if child_tree_alive(child):
                    if child.get('job'):
                        # The job is the authority over the whole tree: end it.
                        with job_adapter().Job(child['job']) as job:
                            job.terminate()
                    else:
                        ManagedChild(child).stop(0)
                if child_tree_alive(child):
                    left.append(label)
            except Exception as error:
                errors.append(error)
                left.append(label)
        if left:
            raise LifecycleError('Children still running after stop: ' + ', '.join(sorted(left))) from (
                errors[0] if errors else None)
        self.status = 'children-stopped'
        self.save()

    def __exit__(self, *args):
        try:
            if self.status != 'stopped':
                # The supervisor's own shutdown already stopped every child: do not run the whole stop
                # (browser shutdown process, per-child waits) a second time.
                if not (self.status == 'children-stopped'
                        and not any(child_tree_alive(child) for child in self.children.values())):
                    self.stop_children()  # Raises only while a child still lives: the database is kept.
                database = getattr(self, 'database', None)
                if database is not None:
                    database.stop()
                elif getattr(self, 'previous_database', None):
                    # A restarted supervisor can fail before rebinding its DB
                    # adapter. Do not publish stopped while the old DB survives.
                    from importlib import import_module
                    adapter = import_module(__package__ + '.native_postgres').Postgres
                    adapter(**self.previous_database).stop()
                self.status = 'stopped'
                self.save()
        finally:
            # The last handles: anything still in these jobs ends now, with its owner.
            for job in self.jobs.values():
                job.close()
            self.jobs.clear()
            self.lock.__exit__()


class ManagedChild:
    def __init__(self, child, popen=None):
        self.child, self.popen = child, popen
        self.pid = child['process']['pid'] if child['process'] else None
        self.stdout = popen.stdout if popen else None

    def poll(self):
        return None if child_running(self.child) else 0

    def wait(self, timeout=5):
        try:
            with ProcessReference(self.child['process']) as reference:
                reference.wait(timeout)
        except psutil.NoSuchProcess:
            pass
        if self.popen:
            self.popen.wait(timeout=timeout)

    def stop(self, timeout=10):
        if self.child.get('job'):
            return self._stop_job(timeout)
        descendants = []
        try:
            with ProcessReference(self.child['process']) as reference:
                for item in self.child.get('descendants', []):
                    try:
                        descendants.append(ProcessReference(item))
                    except psutil.NoSuchProcess:
                        pass
                if self.child['control']:
                    Path(self.child['control']).touch()
                elif self.child.get('native', {}).get('kind') == 'redis':
                    port = self.child['native']['port']
                    if owns_listener(self.child['process'], port):
                        with socket.create_connection(('127.0.0.1', int(port)), timeout=2) as channel:
                            channel.sendall(b'*2\r\n$8\r\nSHUTDOWN\r\n$4\r\nSAVE\r\n')
                elif os.name != 'nt':
                    reference.process.terminate()
                # Windows native binaries require a component-specific graceful
                # callback before this bounded handle-only fallback.
                try:
                    reference.wait(timeout)
                except subprocess.TimeoutExpired:
                    reference.force()
                    reference.wait(5)
        except psutil.NoSuchProcess:
            # Supervisor may have died, but recorded children still need validation.
            for item in self.child.get('descendants', []):
                try:
                    descendants.append(ProcessReference(item))
                except psutil.NoSuchProcess:
                    pass
        finally:
            try:
                for descendant in descendants:
                    if descendant.alive():
                        try:
                            descendant.wait(timeout)
                        except subprocess.TimeoutExpired:
                            descendant.force()
                            descendant.wait(5)
            finally:
                for descendant in descendants:
                    descendant.close()

    def _stop_job(self, timeout):
        with job_adapter().Job(self.child['job']) as job:
            job.root_alive(self.child['process'])
            job.cancel()
            if self.child['control']:
                Path(self.child['control']).touch()
            elif self.child.get('native', {}).get('kind') == 'redis':
                port = self.child['native']['port']
                if any(owns_listener(member, port) for member in job.records()):
                    with socket.create_connection(('127.0.0.1', int(port)), timeout=2) as channel:
                        channel.sendall(b'*2\r\n$8\r\nSHUTDOWN\r\n$4\r\nSAVE\r\n')
            if not job.wait_empty(timeout):
                job.terminate()
            if self.popen:
                self.popen.wait(timeout=5)


def stop_component(root, component, timeout=120):
    directory = state_dir(root, component)
    with InstanceLock(directory, 'operation', timeout=timeout):
        record = Runtime(root, component).read()
        if not record:
            raise LifecycleError('No ownership record; legacy process must be stopped manually')
        if record['status'] == 'stopped':
            if any(child_tree_alive(child) for child in record['children'].values()):
                raise LifecycleError('Stopped record still has living children')
            return True
        if living(record['supervisor']):
            (directory / ('stop-' + record['run_id'])).touch()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                current = Runtime(root, component).read()
                if current['run_id'] != record['run_id']:
                    raise LifecycleError('Supervisor changed during stop')
                if current['status'] == 'stopped' and not any(child_tree_alive(c) for c in current['children'].values()):
                    return True
                if not living(current['supervisor']):
                    # Its own stop failed and it exited (before: 120 s of waiting for a status that never came).
                    record = Runtime(root, component).read()
                    if record['status'] == 'stopped' and not any(child_tree_alive(c) for c in record['children'].values()):
                        return True
                    break
                time.sleep(.1)
            else:
                raise LifecycleError('Shutdown timeout; state retained and database left running')
        # Crash recovery must reconcile *all* live children before any database stop.
        with InstanceLock(directory, 'instance', timeout=0):
            for child in sorted(record['children'].values(), key=lambda item: item['control'] is None):
                ManagedChild(child).stop()
            if record.get('database'):
                from importlib import import_module
                adapter = import_module(__package__ + '.native_postgres').Postgres
                adapter(**record['database']).stop()
            record['status'] = 'stopped'
            durable_json(directory / 'processes.json', record)
        return True


def ensure_started(root, component, argv, *, env=None, timeout=60):
    directory = state_dir(root, component)
    with InstanceLock(directory, 'operation', timeout=timeout):
        previous = Runtime(root, component).read()
        if previous and previous['status'] == 'ready' and living(previous['supervisor']):
            if all(child_running(child) for child in previous['children'].values()):
                return True
            # Give the owning supervisor an opportunity to repair missing children.
        if previous and previous['status'] not in ('stopped', 'failed') and living(previous['supervisor']):
            proc = None
        else:
            options = dict(creationflags=0x08000000) if os.name == 'nt' else dict(start_new_session=True)
            log_path = directory / 'supervisor.log'
            with log_path.open('ab') as log:
                proc = subprocess.Popen(list(map(str, argv)), cwd=root, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=log, **options)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = Runtime(root, component).read()
            if current and current['status'] == 'ready' and living(current['supervisor']):
                if all(child_running(c) for c in current['children'].values()):
                    return True
            if proc and proc.poll() is not None:
                raise LifecycleError('Supervisor startup failed; inspect supervisor.log')
            time.sleep(.1)
        raise LifecycleError('Readiness timeout; no successful start claimed')
