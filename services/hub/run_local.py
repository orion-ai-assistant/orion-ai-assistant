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
    """Restart only an exited Worker, with a finite lifetime restart budget."""

    def __init__(self, worker, start_worker, replace_worker, stopping, report):
        self.worker = worker
        self.start_worker = start_worker
        self.replace_worker = replace_worker
        self.stopping = stopping
        self.report = report
        self.attempts = 0
        self.restart_at = None
        self.exhausted = False

    def check(self):
        if self.stopping.is_set() or self.exhausted:
            return
        exit_code = self.worker.poll()
        if exit_code is None:
            return
        if self.attempts >= 5:
            self.exhausted = True
            self.report("Worker restart limit reached (5 attempts); automatic restarts disabled")
            return
        now = time.monotonic()
        if self.restart_at is None:
            delay = min(2 ** self.attempts, 30)
            self.restart_at = now + delay
            self.report(f"Worker exited with code {exit_code}; restart {self.attempts + 1}/5 in {delay}s")
            return
        if now < self.restart_at or self.stopping.is_set():
            return
        self.attempts += 1
        self.restart_at = None
        try:
            replacement = self.start_worker()
        except OSError as exc:
            self.report(f"Worker restart {self.attempts}/5 failed: {exc}")
            return
        self.replace_worker(self.worker, replacement)
        self.worker = replacement
        self.report(f"Worker restarted (PID {replacement.pid}, attempt {self.attempts}/5)")


def _main(runtime):
    if os.name == 'nt':
        os.system("")  # Enable ANSI colors in Windows cmd
        
    print("==========================================")
    print(" Starting Orion Hub natively (Local Mode)")
    print("==========================================")
    
    # Environment config
    hub_port = get_env("HUB_PORT")
    env = os.environ.copy()
    # A separate LAN/tunnel entry point never exposes local owner pages/APIs.
    public_port = get_env('ORION_PUBLIC_PORT', default='8910', required=False)
    local_port = int(get_env('ORION_LOCAL_PORT', default='8910', required=False))
    public_port = int(get_env('ORION_WEB_PORT', default=public_port, required=False))
    if public_port == local_port:
        public_port = 8911
    env['ORION_PUBLIC_PORT'] = str(public_port)
    env['ORION_LOCAL_PORT'] = str(local_port)
    mobile_port = get_env('ORION_MOBILE_PORT', default='8443', required=False)
    try:
        sys.path.insert(0, os.path.join(_hub_dir, 'src'))
        from orion.kernel.local_installation import register_native_hub
        register_native_hub(_repo_root, int(mobile_port))
    except (OSError, ValueError, ImportError) as error:
        print(f'[!] Local desktop registration unavailable: {type(error).__name__}; phone QR remains available')
    if len({int(hub_port), int(public_port), int(mobile_port), local_port}) != 4:
        raise RuntimeError('Hub HTTPS ports must differ')
    if str(public_port) == str(hub_port):
        raise RuntimeError('ORION_PUBLIC_PORT must differ from HUB_PORT')
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
    public_cmd = [py_bin, '-m', 'orion.api.web_server', '--public', '--host',
                  get_env('ORION_PUBLIC_BIND_HOST', default='0.0.0.0', required=False), '--port', str(public_port)]
    mobile_cmd = [py_bin, '-m', 'orion.api.mobile_server']
    local_cmd = [py_bin, '-m', 'orion.api.web_server', '--local', '--http-loopback', '--host', '127.0.0.1', '--port', str(local_port)]
    shortcuts_enabled = get_env('ORION_LOCAL_SHORTCUTS', default='1', required=False).lower() not in {'0', 'false', 'off', 'no'}
    shortcuts_port = int(get_env('ORION_LOCAL_SHORTCUT_PORT', default='80', required=False))
    if shortcuts_enabled and shortcuts_port in {int(public_port), int(hub_port), int(mobile_port), local_port}:
        raise RuntimeError('Local shortcut port must differ from other Hub listeners')
    # Always local, even with legacy LAN/gateway environment settings present.
    shortcuts_cmd = [py_bin, '-m', 'orion.api.web_server', '--local', '--http-loopback', '--host', '127.0.0.1', '--port', str(shortcuts_port)]
    worker_cmd = [py_bin, "-m", "orion.worker.main"]
    redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
    
    hide_flags = 0x08000000 if os.name == 'nt' else 0
    
    log_file = open(log_path, "a", encoding="utf-8")
    log_lock = threading.Lock()
    
    processes = []
    stopping = threading.Event()
    all_pids = [os.getpid()]  # Include this process (run_local.py) itself
    
    from services.shared.process_lifecycle import assert_port_free, wait_health, owns_listener, LifecycleError
    from services.shared.native_services import hub_database
    database = hub_database(base_dir, get_env)
    # No API, worker or Redis accepts work until the authenticated DB is ready.
    database.ensure()
    runtime.database = database
    def before_stop():
        if not stop_hub_browsers(hub_dir):
            raise LifecycleError('Browser shutdown could not be verified')
    runtime.before_stop = before_stop
    pg_port = get_env('POSTGRES_PORT')
    redis_port = get_env('REDIS_PORT')
    if 'redis' not in runtime.children:
        assert_port_free(redis_port)
    if not os.path.isfile(redis_exe):
        raise LifecycleError('Portable Redis missing; run setup first')
    redis_proc = runtime.launch('redis', [redis_exe, '--bind', '127.0.0.1', '--port', str(redis_port)],
        cwd=str(mutable_hub), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=hide_flags)
    redis_proc.child['native'] = {'kind': 'redis', 'port': int(redis_port)}
    processes.append(redis_proc)
    all_pids.append(redis_proc.pid)
    threading.Thread(target=stream_logs, args=(redis_proc, '[REDIS]', log_file, log_lock), daemon=True).start()
    deadline = time.monotonic() + 15
    while True:
        if owns_listener(redis_proc.child['process'], redis_port):
            with socket.create_connection(('127.0.0.1', int(redis_port)), timeout=1) as redis_check:
                redis_check.sendall(b'*1\r\n$4\r\nPING\r\n')
                if redis_check.recv(64).startswith(b'+PONG'):
                    break
        if time.monotonic() >= deadline:
            raise LifecycleError('Redis readiness failed')
        time.sleep(.1)
    for label, port in [('api', hub_port), ('public', public_port), ('local', local_port),
                         ('mobile', mobile_port)] + ([('shortcuts', shortcuts_port)] if shortcuts_enabled else []):
        if label not in runtime.children:
            assert_port_free(port)

    print(f"[*] Launching API on port {hub_port}...")
    api_proc = runtime.launch('api', api_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(api_proc)
    all_pids.append(api_proc.pid)
    threading.Thread(target=stream_logs, args=(api_proc, "[API]", log_file, log_lock), daemon=True).start()

    public_proc = runtime.launch('public', public_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(public_proc)
    all_pids.append(public_proc.pid)
    threading.Thread(target=stream_logs, args=(public_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] PC owner home: https://localhost:{hub_port}/; phone/tunnel entry point: port {public_port}')
    local_proc = runtime.launch('local', local_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(local_proc)
    all_pids.append(local_proc.pid)
    threading.Thread(target=stream_logs, args=(local_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] Local management: http://127.0.0.1:{local_port}/manage')

    if shortcuts_enabled:
        shortcut_proc = runtime.launch('shortcuts', shortcuts_cmd, python=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
        processes.append(shortcut_proc)
        all_pids.append(shortcut_proc.pid)
        threading.Thread(target=stream_logs, args=(shortcut_proc, '[API]', log_file, log_lock), daemon=True).start()
        suffix = '' if shortcuts_port == 80 else f':{shortcuts_port}'
        print(f'[*] Local entry: http://localhost{suffix}/; chat: /hub; owner panel: /panel; Router: /orionrouter')
    
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

    # Installer's Hub button and manager.py both use this entry point.
    try:
        from services.shared.flutter_launcher import start_flutter
        flutter_proc = start_flutter(repo_root, py_bin, get_env,
            {int(hub_port), int(public_port), int(mobile_port), local_port,
             shortcuts_port, int(redis_port), int(pg_port),
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
    wait_health(public_proc, public_port, certificate=identity['cert'])
    wait_health(local_proc, local_port)
    wait_health(mobile_proc, mobile_port, certificate=identity['cert'], identity=identity['id'])
    if shortcuts_enabled:
        wait_health(shortcut_proc, shortcuts_port)
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
    
    try:
        while not stopping.wait(.2):
            if runtime.requested():
                break
            runtime.refresh_children()
            for label, command, port, tls in [
                    ('api', api_cmd, hub_port, True), ('public', public_cmd, public_port, True),
                    ('local', local_cmd, local_port, False), ('mobile', mobile_cmd, mobile_port, True)
                    ] + ([('shortcuts', shortcuts_cmd, shortcuts_port, False)] if shortcuts_enabled else []):
                from services.shared.process_lifecycle import living
                if not living(runtime.children[label]['process']):
                    runtime.status = 'starting'
                    runtime.save()
                    assert_port_free(port)
                    replacement = runtime.launch(label, command, python=True, env=env, stdout=subprocess.PIPE,
                                                 stderr=subprocess.STDOUT, creationflags=hide_flags)
                    threading.Thread(target=stream_logs, args=(replacement, '[API]', log_file, log_lock), daemon=True).start()
                    wait_health(replacement, port, certificate=identity['cert'] if tls else None,
                                identity=identity['id'] if label == 'mobile' else None)
                    runtime.ready()
            supervisor.check()
            if not worker_ready(supervisor.worker, redis_port):
                runtime.status = 'starting'
                runtime.save()
            elif runtime.status == 'starting':
                runtime.ready()
    except KeyboardInterrupt:
        pass
    finally:
        handle_shutdown()

def main():
    from services.shared.process_lifecycle import Runtime
    with Runtime(_repo_root, 'hub') as runtime:
        return _main(runtime)


if __name__ == "__main__":
    main()
