import os
import sys
import subprocess
import signal
import time
import threading
import socket

# Dynamic path injection for services package imports (shared environment module)
_hub_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.abspath(os.path.join(_hub_dir, "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from services.shared.environment import get_env

COLORS = {
    "[API]": "\033[96m",     # Cyan
    "[WORKER]": "\033[93m",  # Yellow
    "[REDIS]": "\033[35m",   # Magenta
    "RESET": "\033[0m"
}

def stream_logs(proc, prefix, log_file, lock):
    if proc.stdout is None:
        return
    color = COLORS.get(prefix, "")
    reset = COLORS["RESET"]
    for line in iter(proc.stdout.readline, b''):
        decoded = line.decode('utf-8', errors='replace')
        plain_line = f"{prefix} {decoded}"
        # Only color the prefix tag, leave the message body white
        colored_line = f"{color}{prefix}{reset} {decoded}" if color else plain_line
        with lock:
            log_file.write(plain_line)
            log_file.flush()
            if sys.stdout.isatty():
                try:
                    sys.stdout.write(colored_line)
                    sys.stdout.flush()
                except Exception:
                    pass

class RotatingLog:
    """hub.log, written by the Hub's children for weeks: at MAX_BYTES it becomes hub.log.1 (older ones shift up to
    hub.log.<BACKUPS>, the oldest is dropped). A rename refused because another program holds the file open is
    not an error: writing goes on in the same file and the next limit tries again."""
    MAX_BYTES = 20 * 1024 * 1024
    BACKUPS = 3

    def __init__(self, path, max_bytes=None, backups=None):
        self.path = path
        self.max_bytes = max_bytes or self.MAX_BYTES
        self.backups = self.BACKUPS if backups is None else backups
        self.lock = threading.RLock()
        self.file = None
        self.closed = False
        self._open()

    def _open(self):
        self.file = open(self.path, 'a', encoding='utf-8')
        try:
            self.size = self.file.seek(0, os.SEEK_END)  # Appending starts at the end: its size.
        except (OSError, ValueError):
            self.size = 0

    def _rotate(self):
        self.file.close()
        try:
            for index in range(self.backups - 1, 0, -1):
                older = f'{self.path}.{index}'
                if os.path.exists(older):
                    os.replace(older, f'{self.path}.{index + 1}')
            if self.backups:
                os.replace(self.path, f'{self.path}.1')
            else:
                os.remove(self.path)
        except OSError:
            pass  # Held open elsewhere: keep writing to it, try again at the next limit.
        self._open()
        if self.size >= self.max_bytes:
            self.size = 0  # Not rotated: count afresh rather than retrying on every line.

    def write(self, text):
        with self.lock:
            if self.closed:
                return 0
            if self.size >= self.max_bytes:
                self._rotate()
            written = self.file.write(text)
            self.size += len(text.encode('utf-8', errors='replace'))
            return written

    def flush(self):
        with self.lock:
            if not self.closed:
                self.file.flush()

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.file.close()


def write_pids(pid_path, pids):
    """Write a list of PIDs to hub.pid for cross-platform stop support."""
    try:
        with open(pid_path, "w") as f:
            for pid in pids:
                f.write(f"{pid}\n")
    except Exception as e:
        print(f"[!] Could not write PID file: {e}", file=sys.stderr)


def worker_ready(worker, redis_port):
    """A living launcher is insufficient: the Worker must own its journal."""
    import json
    import psutil
    from redis import Redis
    from redis.exceptions import RedisError
    try:
        with Redis(host='127.0.0.1', port=int(redis_port), socket_timeout=1,
                   socket_connect_timeout=1, decode_responses=True, protocol=2) as client:
            raw = client.get('orion:worker:owner')
        if not raw:
            return False
        owner = json.loads(raw)
        process = psutil.Process(worker.pid)
        candidates = [process, *process.children(recursive=True)]
        return owner['host'] == socket.gethostname() and any(
            item.pid == owner['pid'] and abs(item.create_time() - owner['created']) < .001
            and item.is_running() for item in candidates)
    except (OSError, ValueError, KeyError, psutil.Error, RedisError):
        return False


class WorkerSupervisor:
    """Restart only an exited Worker: five quick restarts (1-16 s apart), then one every PAUSE seconds.

    The Hub runs for weeks: a Worker that has stayed up for STABLE seconds earns its five quick restarts back
    (a Redis restart or a lost journal ends it once in a while, and that once used up a lifetime budget and left
    the Hub without a Worker). A Worker that keeps failing is not given up either, only slowed down."""
    STABLE = 600
    PAUSE = 600

    def __init__(self, worker, start_worker, replace_worker, stopping, report):
        self.worker = worker
        self.start_worker = start_worker
        self.replace_worker = replace_worker
        self.stopping = stopping
        self.report = report
        self.attempts = 0
        self.restart_at = None
        self.exhausted = False
        self.started_at = time.monotonic()

    def check(self):
        if self.stopping.is_set():
            return
        exit_code = self.worker.poll()
        now = time.monotonic()
        if exit_code is None:
            if self.attempts and now - self.started_at >= self.STABLE:
                self.attempts = 0
                self.exhausted = False
            return
        if self.restart_at is None:
            if self.attempts >= 5:
                delay = self.PAUSE
                if not self.exhausted:
                    self.exhausted = True
                    self.report(f"Worker restart limit reached (5 attempts); trying again every {delay}s")
            else:
                delay = min(2 ** self.attempts, 30)
                self.report(f"Worker exited with code {exit_code}; restart {self.attempts + 1}/5 in {delay}s")
            self.restart_at = now + delay
            return
        if now < self.restart_at or self.stopping.is_set():
            return
        if self.attempts < 5:
            self.attempts += 1
        self.restart_at = None
        try:
            replacement = self.start_worker()
        except Exception as exc:  # OSError, or a LifecycleError that must not end the whole Hub.
            self.report(f"Worker restart {self.attempts}/5 failed: {exc}")
            return
        self.replace_worker(self.worker, replacement)
        self.worker = replacement
        self.started_at = now
        self.report(f"Worker restarted (PID {replacement.pid}, attempt {self.attempts}/5)")


class DatabaseSupervisor:
    """Start the Hub's PostgreSQL again when it ends under a running Hub.

    Windows once refused it memory (error 1455, the paging file full) and it shut down; the Hub then answered 503
    for hours. Checked every CHECK seconds; a failed start is retried after a growing delay of up to MAX_DELAY,
    never given up: nothing works without the database."""
    CHECK = 10
    MAX_DELAY = 60

    def __init__(self, database, stopping, report, clock=time.monotonic):
        self.database = database
        self.stopping = stopping
        self.report = report
        self.clock = clock
        self.failures = 0
        self.next_check = clock() + self.CHECK

    def alive(self):
        # The server this process started (Windows): a cheap look at its process. Otherwise pg_ctl status.
        server = getattr(self.database, '_server', None)
        if server is not None:
            return server.poll() is None
        return self.database.ready()

    def check(self):
        if self.stopping.is_set() or self.clock() < self.next_check:
            return
        try:
            alive = self.alive()
        except Exception:
            alive = False  # Not answering (still recovering, or gone): treated as down.
        if alive:
            self.failures = 0
            self.next_check = self.clock() + self.CHECK
            return
        self.report("PostgreSQL is not running; starting it again")
        try:
            self.database.ensure()
        except Exception as error:
            self.failures += 1
            delay = min(self.CHECK * 2 ** self.failures, self.MAX_DELAY)
            self.report(f"PostgreSQL restart failed ({error}); next try in {delay}s")
            self.next_check = self.clock() + delay
            return
        self.failures = 0
        self.report("PostgreSQL started again")
        self.next_check = self.clock() + self.CHECK


class ChildSupervisor:
    """Start one child (Redis, an API server, the window's web server) again when it ends.

    `running()` says whether it lives; `restart()` starts it and waits until it serves, raising when it could not.
    A failed start is retried after a growing delay of up to MAX_DELAY and never given up: before this a failed
    restart (port still held, Redis down while an API started) ended the whole Hub. A `running()` that cannot
    tell (access denied) leaves the child alone."""
    MAX_DELAY = 60

    def __init__(self, name, running, restart, stopping, report, clock=time.monotonic, every=0.0):
        self.name = name
        self.running = running
        self.restart = restart
        self.stopping = stopping
        self.report = report
        self.clock = clock
        self.every = every  # A child that is costly to look at is looked at this often.
        self.failures = 0
        self.next_try = 0.0

    def check(self):
        if self.stopping.is_set() or self.clock() < self.next_try:
            return
        try:
            if self.running():
                self.next_try = self.clock() + self.every
                return
        except Exception as error:
            self.report(f"{self.name}: state unknown ({error}); left alone")
            self.next_try = self.clock() + self.MAX_DELAY
            return
        self.report(f"{self.name} is not running; starting it again")
        try:
            self.restart()
        except Exception as error:
            self.failures += 1
            delay = min(2 ** self.failures, self.MAX_DELAY)
            self.report(f"{self.name} restart failed ({error}); next try in {delay}s")
            self.next_try = self.clock() + delay
            return
        self.failures = 0
        self.report(f"{self.name} started again")


def watched_router_root(repo_root):
    """The Router this Hub brings back: an installed Hub its registered Router; a development checkout only the
    sibling checkout (never the installed Router: both use the same ports)."""
    from pathlib import Path
    from services.shared.runtime_layout import component
    if component(repo_root) is None:
        sibling = Path(repo_root).resolve().parent / 'orion-router'
        return sibling if (sibling / 'cli.py').is_file() else None
    try:
        from services.shared.router_installation import registered_router_root
        registered = registered_router_root()
    except Exception:
        return None
    return Path(registered) if registered else None


class RouterWatch:
    """The Router runs under its own supervisor (cli.py prod), which restarts the Router's service. When that
    supervisor itself is gone while its record still says it runs (it crashed, Windows ended it), the Router
    stayed down. A Router stopped on purpose (stop_router: record 'stopped') is left alone."""

    def __init__(self, root, start):
        self.root = root
        self.start = start
        self.thread = None
        self.error = None

    def down(self):
        from services.shared.process_lifecycle import Runtime, living
        record = Runtime(self.root, 'router-prod').read()
        if not record or record.get('status') in ('stopped', 'failed', 'children-stopped', 'stopping'):
            return False
        return not living(record['supervisor'])

    def running(self):
        if self.thread is not None and self.thread.is_alive():
            return True  # Still starting.
        if self.thread is not None:
            self.thread = None
            if self.error is not None:
                error, self.error = self.error, None
                raise RuntimeError(f'start failed: {error}')
        return not self.down()

    def restart(self):
        # Its start waits for its database (minutes after a hard stop): in the background, the Hub keeps watching.
        self.error = None

        def run():
            try:
                self.start(str(self.root / 'cli.py'))
            except Exception as error:
                self.error = error
        self.thread = threading.Thread(target=run, name='router-start', daemon=True)
        self.thread.start()


def wait_redis(record, redis_port, timeout=15):
    """Until this Redis process owns the port and answers PING."""
    from services.shared.process_lifecycle import owns_listener, LifecycleError
    deadline = time.monotonic() + timeout
    while True:
        try:
            if owns_listener(record, redis_port):
                with socket.create_connection(('127.0.0.1', int(redis_port)), timeout=1) as redis_check:
                    redis_check.sendall(b'*1\r\n$4\r\nPING\r\n')
                    if redis_check.recv(64).startswith(b'+PONG'):
                        return
        except OSError:
            pass  # Still loading its snapshot.
        if time.monotonic() >= deadline:
            raise LifecycleError('Redis readiness failed')
        time.sleep(.1)


AOF_NAME = 'appendonly.aof'


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def redis_argv(redis_exe, redis_port, directory):
    """Redis's command line, and whether its append-only file still has to be switched on.

    Snapshots alone (Redis's default rules) lose up to minutes of jobs and chat state in a crash; the append-only
    file loses at most a second. Starting with --appendonly yes while only dump.rdb exists would load an empty
    AOF instead of the snapshot, so the first time Redis starts from its snapshot, a copy of the snapshot is kept
    (dump.rdb.before-aof) and the AOF is switched on while it runs (switch_on_aof). Later starts load the AOF."""
    argv = [redis_exe, '--bind', '127.0.0.1', '--port', str(redis_port)]
    aof = os.path.join(directory, AOF_NAME)
    snapshot = os.path.join(directory, 'dump.rdb')
    if _size(aof) == 0 and _size(snapshot) > 0:
        # Redis creates the file when the switch starts and fills it when its first rewrite ends: one ended
        # in between (empty file) must not replace the snapshot with nothing.
        os.replace(aof, aof + '.empty')
    if os.path.isfile(aof):
        return argv + ['--appendonly', 'yes', '--appendfilename', AOF_NAME], False
    backup = snapshot + '.before-aof'
    if os.path.isfile(snapshot) and not os.path.exists(backup):
        import shutil
        shutil.copy2(snapshot, backup)
    return argv, True


def redis_call(redis_port, *words, timeout=5):
    """One command over RESP; the first reply line."""
    command = b'*%d\r\n' % len(words) + b''.join(b'$%d\r\n%s\r\n' % (len(w), w) for w in
                                                    (word.encode() for word in words))
    with socket.create_connection(('127.0.0.1', int(redis_port)), timeout=timeout) as client:
        client.sendall(command)
        return client.recv(256).split(b'\r\n', 1)[0]


def switch_on_aof(redis_port):
    """Redis writes the AOF from what it holds now (the snapshot it loaded), then appends every change."""
    reply = redis_call(redis_port, 'CONFIG', 'SET', 'appendonly', 'yes')
    if not reply.startswith(b'+OK'):
        raise RuntimeError(f'Redis did not switch on its append-only file: {reply[:80]!r}')


def aof_written(redis_port):
    """The first rewrite after the switch has ended and succeeded (until then the file may be empty)."""
    with socket.create_connection(('127.0.0.1', int(redis_port)), timeout=5) as client:
        client.sendall(b'*2\r\n$4\r\nINFO\r\n$11\r\npersistence\r\n')
        data = b''
        while b'aof_last_write_status' not in data:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    info = dict(line.split(':', 1) for line in data.decode(errors='replace').splitlines() if ':' in line)
    return (info.get('aof_enabled') == '1' and info.get('aof_rewrite_in_progress') == '0'
            and info.get('aof_rewrite_scheduled') == '0' and info.get('aof_last_bgrewrite_status') == 'ok')


def _main(runtime):
    if os.name == 'nt':
        os.system("")  # Enable ANSI colors in Windows cmd
        
    print("==========================================")
    print(" Starting Orion Hub natively (Local Mode)")
    print("==========================================")
    
    # Environment config
    hub_port = get_env("HUB_PORT")
    env = os.environ.copy()
    # No browser entry for other devices: they use the Orion app on the mobile port.
    local_port = int(get_env('ORION_LOCAL_PORT', default='8910', required=False))
    env['ORION_LOCAL_PORT'] = str(local_port)
    if get_env('ORION_HUB_MODE', default='', required=False).strip().lower() == 'relay':
        return _relay_main(runtime, env, local_port)
    mobile_port = get_env('ORION_MOBILE_PORT', default='8443', required=False)
    try:
        sys.path.insert(0, os.path.join(_hub_dir, 'src'))
        from orion.kernel.local_installation import register_native_hub
        register_native_hub(_repo_root, int(mobile_port))
    except (OSError, ValueError, ImportError) as error:
        print(f'[!] Local desktop registration unavailable: {type(error).__name__}; phone QR remains available')
    if len({int(hub_port), int(mobile_port), local_port}) != 3:
        raise RuntimeError('Hub ports must differ')
    if not env.get('JWT_SECRET_KEY') or env.get('JWT_SECRET_KEY') == 'super-secret-default-key-for-dev':
        import secrets
        env['JWT_SECRET_KEY'] = secrets.token_urlsafe(48)
        from services.shared.runtime_layout import data_root
        overrides = str(data_root(_repo_root) / 'services/.env.global.local')
        with open(overrides, 'a', encoding='utf-8') as configuration:
            configuration.write('\n# Hub public entry point signing key\nJWT_SECRET_KEY=' + env['JWT_SECRET_KEY'] + '\n')
    
    hub_dir = os.path.dirname(os.path.abspath(__file__))
    from services.shared.browser_shutdown import allow_hub_browsers, stop_hub_browsers
    allow_hub_browsers(hub_dir)
    repo_root = os.path.abspath(os.path.join(hub_dir, "..", ".."))
    hub_src = os.path.join(hub_dir, "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    new_pythonpath = os.pathsep.join(filter(None, [hub_src, repo_root, existing_pythonpath]))
    
    env["PYTHONPATH"] = new_pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    hub_dir = os.path.dirname(os.path.abspath(__file__))
    from services.shared.runtime_layout import data_root, component
    mutable_hub = data_root(base_dir) / 'services/hub'
    mutable_hub.mkdir(parents=True, exist_ok=True)
    pid_path = str(mutable_hub / 'hub.pid')
    log_path = str(mutable_hub / 'hub.log')
    
    # Commands
    hub_venv_py = os.path.join(hub_dir, ".venv", "Scripts", "python.exe") if os.name == 'nt' else os.path.join(hub_dir, ".venv", "bin", "python")
    py_bin = hub_venv_py if os.path.exists(hub_venv_py) else sys.executable
    api_cmd = [py_bin, '-m', 'orion.api.web_server', '--host', '127.0.0.1', '--port', str(hub_port)]
    mobile_cmd = [py_bin, '-m', 'orion.api.mobile_server']
    local_cmd = [py_bin, '-m', 'orion.api.web_server', '--local', '--http-loopback', '--host', '127.0.0.1', '--port', str(local_port)]
    worker_cmd = [py_bin, "-m", "orion.worker.main"]
    redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
    
    hide_flags = 0x08000000 if os.name == 'nt' else 0
    
    log_file = RotatingLog(log_path)
    log_lock = threading.Lock()
    
    processes = []
    stopping = threading.Event()
    all_pids = [os.getpid()]  # Include this process (run_local.py) itself
    
    from services.shared.process_lifecycle import assert_port_free, wait_health, owns_listener, LifecycleError
    from services.shared import postgres_auth
    from services.shared.native_services import hub_database, hub_database_passwords
    passwords = hub_database_passwords(base_dir)
    # API and worker log in with the app's password; only this supervisor needs the superuser's.
    os.environ['POSTGRES_PASSWORD'] = env['POSTGRES_PASSWORD'] = passwords['POSTGRES_PASSWORD']
    env.pop('POSTGRES_ADMIN_PASSWORD', None)
    database = hub_database(base_dir, get_env)
    admin, app = ('postgres', passwords['POSTGRES_ADMIN_PASSWORD']), (database.user, database.password)
    postgres_auth.prepare(database, admin, app, database.database)
    # No API, worker or Redis accepts work until the authenticated DB is ready.
    database.ensure()
    runtime.database = database
    try:
        protected = postgres_auth.secure(database, admin, app, database.database)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'[!] PostgreSQL password setup failed: {error}', flush=True)
        protected = False
    if not protected:
        print('[!] PostgreSQL still accepts logins without a password from this PC', flush=True)
    def before_stop():
        # Only drops the API's connections to the user's own browsers; the Job ends every Hub child anyway.
        # A failure here (API already gone, Windows ending the session) must not abort the stop: it left
        # API, worker, Redis and PostgreSQL running under a dead supervisor.
        if not stop_hub_browsers(hub_dir):
            print('[!] Browser shutdown could not be verified; stopping services anyway', flush=True)
    runtime.before_stop = before_stop
    pg_port = get_env('POSTGRES_PORT')
    redis_port = get_env('REDIS_PORT')
    if 'redis' not in runtime.children:
        assert_port_free(redis_port)
    if not os.path.isfile(redis_exe):
        raise LifecycleError('Portable Redis missing; run setup first')
    def start_redis():
        argv, switch = redis_argv(redis_exe, redis_port, str(mutable_hub))
        proc = runtime.launch('redis', argv,
            cwd=str(mutable_hub), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=hide_flags)
        proc.child['native'] = {'kind': 'redis', 'port': int(redis_port)}
        threading.Thread(target=stream_logs, args=(proc, '[REDIS]', log_file, log_lock), daemon=True).start()
        wait_redis(proc.child['process'], redis_port)
        if switch:
            try:
                switch_on_aof(redis_port)
                deadline = time.monotonic() + 60
                while not aof_written(redis_port):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('its first rewrite did not finish in 60 s')
                    time.sleep(.2)
                print('[*] Redis keeps an append-only file now (at most a second lost in a crash)', flush=True)
            except (OSError, RuntimeError) as error:
                # Snapshots still work as before; tried again at the next start.
                print(f'[!] Redis append-only file not switched on: {error}', flush=True)
        return proc

    redis_proc = start_redis()
    processes.append(redis_proc)
    all_pids.append(redis_proc.pid)
    for label, port in [('api', hub_port), ('local', local_port), ('mobile', mobile_port)]:
        if label not in runtime.children:
            assert_port_free(port)

    print(f"[*] Launching API on port {hub_port}...")
    api_proc = runtime.launch('api', api_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(api_proc)
    all_pids.append(api_proc.pid)
    threading.Thread(target=stream_logs, args=(api_proc, "[API]", log_file, log_lock), daemon=True).start()

    print(f'[*] PC owner home: https://localhost:{hub_port}/')
    local_proc = runtime.launch('local', local_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(local_proc)
    all_pids.append(local_proc.pid)
    threading.Thread(target=stream_logs, args=(local_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] Local management: http://127.0.0.1:{local_port}/panel')

    mobile_proc = runtime.launch('mobile', mobile_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(mobile_proc)
    all_pids.append(mobile_proc.pid)
    threading.Thread(target=stream_logs, args=(mobile_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] Mobile pinned HTTPS: port {mobile_port}')

    print("[*] Launching Worker...")
    def start_worker():
        proc = runtime.launch('worker', worker_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
        threading.Thread(target=stream_logs, args=(proc, "[WORKER]", log_file, log_lock), daemon=True).start()
        return proc

    worker_proc = start_worker()
    processes.append(worker_proc)
    all_pids.append(worker_proc.pid)

    def replace_worker(previous, replacement):
        processes[processes.index(previous)] = replacement
        all_pids[all_pids.index(previous.pid)] = replacement.pid
        write_pids(pid_path, all_pids)

    def report_worker(message):
        with log_lock:
            log_file.write(f"[WORKER] {message}\n")
            log_file.flush()
        print(f"[WORKER] {message}")

    supervisor = WorkerSupervisor(worker_proc, start_worker, replace_worker, stopping, report_worker)

    def report_database(message):
        with log_lock:
            log_file.write(f"[DATABASE] {message}\n")
            log_file.flush()
        print(f"[DATABASE] {message}")

    database_supervisor = DatabaseSupervisor(database, stopping, report_database)

    # Installer's Hub button and manager.py both use this entry point.
    flutter_proc = None
    try:
        from services.shared.flutter_launcher import start_flutter
        flutter_proc = start_flutter(repo_root, py_bin, get_env,
            {int(hub_port), int(mobile_port), local_port, int(redis_port), int(pg_port),
             int(get_env('ORION_ROUTER_TLS_PORT', default='9443', required=False)),
             int(get_env('ORION_ROUTER_LOCAL_PORT', default='20128', required=False)),
             int(get_env('INSTALLER_PORT', default='7171', required=False))},
            log_file, stopping, runtime=runtime)
        if flutter_proc is not None:
            processes.append(flutter_proc)
            all_pids.append(flutter_proc.pid)
    except (OSError, ValueError) as error:
        print(f'[!] Flutter UI could not start: {error}', flush=True)
    
    from orion.kernel.local_security import tls_identity
    identity = tls_identity()
    wait_health(api_proc, hub_port, certificate=identity['cert'])
    wait_health(local_proc, local_port)
    wait_health(mobile_proc, mobile_port, certificate=identity['cert'], identity=identity['id'])
    worker_deadline = time.monotonic() + 30
    while not worker_ready(supervisor.worker, redis_port):
        supervisor.check()
        if time.monotonic() >= worker_deadline or supervisor.exhausted:
            raise LifecycleError('Worker did not acquire journal ownership')
        time.sleep(.1)
    runtime.ready()

    # Write ALL PIDs to hub.pid so manager.py stop can cleanly kill them all
    write_pids(pid_path, all_pids)
    print("[*] Tailing logs (Close this window to leave processes running in background)...\n" + "-"*40)
    
    def handle_shutdown(*args):
        # Set this before terminating children, so shutdown cannot trigger a restart.
        stopping.set()
        runtime.stop_children()
        database.stop()
        log_file.close()
        # Clean up PID file on graceful shutdown
        if os.path.exists(pid_path):
            try:
                os.remove(pid_path)
            except Exception:
                pass
        sys.exit(0)
        
    def request_shutdown(*args):
        stopping.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, request_shutdown)
    
    from services.shared.process_lifecycle import living

    def report_child(message):
        with log_lock:
            log_file.write(f"[SUPERVISOR] {message}\n")
            log_file.flush()
        print(f"[SUPERVISOR] {message}")

    def replace_child(previous, replacement):
        if previous in processes:
            processes[processes.index(previous)] = replacement
        else:
            processes.append(replacement)
        if previous is not None and previous.pid in all_pids:
            all_pids[all_pids.index(previous.pid)] = replacement.pid
        else:
            all_pids.append(replacement.pid)
        write_pids(pid_path, all_pids)

    def mark_starting():
        if runtime.status != 'starting':
            runtime.status = 'starting'
            runtime.save()

    current = {'redis': redis_proc, 'api': api_proc, 'local': local_proc, 'mobile': mobile_proc}

    def child_running(label):
        child = runtime.children.get(label)
        return child is not None and living(child['process'])

    def all_running():
        try:
            return all(child_running(label) for label in current)
        except Exception:
            return False  # Unknown: reported by its supervisor.

    def server_restarter(label, command, port, tls):
        def restart():
            mark_starting()
            assert_port_free(port)
            replacement = runtime.launch(label, command, python=True, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, creationflags=hide_flags)
            threading.Thread(target=stream_logs, args=(replacement, '[API]', log_file, log_lock), daemon=True).start()
            wait_health(replacement, port, certificate=identity['cert'] if tls else None,
                        identity=identity['id'] if label == 'mobile' else None)
            replace_child(current.get(label), replacement)
            current[label] = replacement
        return restart

    def restart_redis():
        mark_starting()
        assert_port_free(redis_port)
        replacement = start_redis()
        replace_child(current['redis'], replacement)
        current['redis'] = replacement

    # Redis first: an API server that starts while Redis is down fails its startup.
    child_supervisors = [ChildSupervisor('Redis', lambda: child_running('redis'), restart_redis, stopping, report_child)]
    for label, command, port, tls in [('api', api_cmd, hub_port, True), ('local', local_cmd, local_port, False),
                                      ('mobile', mobile_cmd, mobile_port, True)]:
        child_supervisors.append(ChildSupervisor(f'API server {label}', lambda label=label: child_running(label),
                                                 server_restarter(label, command, port, tls), stopping, report_child))
    if flutter_proc is not None and getattr(flutter_proc, 'relaunch', None):
        def restart_flutter():
            replacement = current['flutter'].relaunch()
            replace_child(current['flutter'], replacement)
            current['flutter'] = replacement
        current['flutter'] = flutter_proc
        child_supervisors.append(ChildSupervisor('Window web server', lambda: child_running('flutter'),
                                                 restart_flutter, stopping, report_child))

    router_root = watched_router_root(repo_root)
    if router_root is not None:
        from services.shared.native_services import start_router
        router_watch = RouterWatch(router_root, start_router)
        child_supervisors.append(ChildSupervisor('Router', router_watch.running, router_watch.restart, stopping,
                                                 report_child, every=30))

    # Stop requests are seen within TICK; the children are looked at every CHECK seconds. Looking every 0.2 s
    # (process handles, a Redis connection and an fsync'd state file each time) cost ~15% of a core at idle.
    TICK, CHECK = .2, 2.0
    next_check = 0.0
    try:
        while not stopping.wait(TICK):
            if runtime.requested():
                break
            if time.monotonic() < next_check:
                continue
            next_check = time.monotonic() + CHECK
            try:
                runtime.refresh_children()
            except Exception as error:  # A job that cannot be read this time must not end the Hub.
                report_child(f'Child records not refreshed: {error}')
            for child_supervisor in child_supervisors:
                child_supervisor.check()
            database_supervisor.check()
            supervisor.check()
            if not worker_ready(supervisor.worker, redis_port) or not all_running():
                mark_starting()
            elif runtime.status == 'starting':
                runtime.ready()
    except KeyboardInterrupt:
        pass
    finally:
        handle_shutdown()

def _relay_main(runtime, env, local_port):
    """A computer that uses another PC's Hub: only the window's loopback app and the window itself.

    No database, Redis, worker, Router, phone listener or LAN advertisement (see orion.api.relay_server)."""
    from services.shared.process_lifecycle import assert_port_free, wait_health, living
    from services.shared.runtime_layout import data_root
    hub_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(hub_dir, '..', '..'))
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [os.path.join(hub_dir, 'src'), repo_root, env.get('PYTHONPATH', '')]))
    env['PYTHONUNBUFFERED'] = '1'
    mutable_hub = data_root(repo_root) / 'services/hub'
    mutable_hub.mkdir(parents=True, exist_ok=True)
    pid_path = str(mutable_hub / 'hub.pid')
    venv_py = os.path.join(hub_dir, '.venv', 'Scripts', 'python.exe') if os.name == 'nt' else os.path.join(hub_dir, '.venv', 'bin', 'python')
    py_bin = venv_py if os.path.exists(venv_py) else sys.executable
    hide_flags = 0x08000000 if os.name == 'nt' else 0
    log_file = open(str(mutable_hub / 'hub.log'), 'a', encoding='utf-8')
    log_lock = threading.Lock()
    stopping = threading.Event()
    pids = [os.getpid()]
    local_cmd = [py_bin, '-m', 'orion.api.web_server', '--local', '--http-loopback', '--relay',
                 '--host', '127.0.0.1', '--port', str(local_port)]
    print('[*] Light Hub: this PC uses another Hub through the Orion window', flush=True)

    def launch():
        if 'local' not in runtime.children:
            assert_port_free(local_port)
        process = runtime.launch('local', local_cmd, python=True, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, creationflags=hide_flags)
        threading.Thread(target=stream_logs, args=(process, '[RELAY]', log_file, log_lock), daemon=True).start()
        return process
    local_proc = launch()
    pids.append(local_proc.pid)
    try:
        from services.shared.flutter_launcher import start_flutter
        flutter_proc = start_flutter(repo_root, py_bin, get_env, {local_port}, log_file, stopping, runtime=runtime)
        if flutter_proc is not None:
            pids.append(flutter_proc.pid)
    except (OSError, ValueError) as error:
        print(f'[!] Flutter UI could not start: {error}', flush=True)
    wait_health(local_proc, local_port)
    runtime.ready()
    write_pids(pid_path, pids)
    signal.signal(signal.SIGINT, lambda *args: stopping.set())
    signal.signal(signal.SIGTERM, lambda *args: stopping.set())
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda *args: stopping.set())
    try:
        while not stopping.wait(.2):
            if runtime.requested():
                break
            try:
                runtime.refresh_children()
            except Exception as error:  # A look that fails this time must not stop the window server.
                print(f'[!] Child records not refreshed: {error}', flush=True)
            if not living(runtime.children['local']['process']):
                runtime.status = 'starting'
                runtime.save()
                wait_health(launch(), local_port)
                runtime.ready()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        runtime.stop_children()
        log_file.close()
        if os.path.exists(pid_path):
            try:
                os.remove(pid_path)
            except OSError:
                pass


def main():
    from services.shared.process_lifecycle import Runtime
    with Runtime(_repo_root, 'hub') as runtime:
        return _main(runtime)


if __name__ == "__main__":
    main()
