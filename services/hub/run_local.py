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


def main():
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
        overrides = os.path.join(_repo_root, 'services', '.env.global.local')
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
    pid_path = os.path.join(hub_dir, "hub.pid")
    log_path = os.path.join(hub_dir, "hub.log")
    
    # Commands
    hub_venv_py = os.path.join(hub_dir, ".venv", "Scripts", "python.exe") if os.name == 'nt' else os.path.join(hub_dir, ".venv", "bin", "python")
    py_bin = hub_venv_py if os.path.exists(hub_venv_py) else sys.executable
    api_cmd = [py_bin, '-m', 'orion.api.web_server', '--host', '127.0.0.1', '--port', str(hub_port)]
    public_cmd = [py_bin, '-m', 'orion.api.web_server', '--public', '--host', '0.0.0.0', '--port', str(public_port)]
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
    
    redis_running = False
    redis_port = get_env('REDIS_PORT')
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as redis_probe:
            redis_probe.settimeout(0.3)
            redis_running = redis_probe.connect_ex(('127.0.0.1', int(redis_port))) == 0
    except OSError:
        pass
    if os.path.exists(redis_exe) and not redis_running:
        print("[*] Launching Portable Redis...")
        redis_dir = os.path.dirname(redis_exe)
        redis_port = get_env("REDIS_PORT")
        redis_proc = subprocess.Popen([redis_exe, "--port", str(redis_port)], cwd=redis_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
        processes.append(redis_proc)
        all_pids.append(redis_proc.pid)
        threading.Thread(target=stream_logs, args=(redis_proc, "[REDIS]", log_file, log_lock), daemon=True).start()

    # Portable PostgreSQL check & launch
    pg_port = get_env("POSTGRES_PORT")
    pg_ctl_name = "pg_ctl.exe" if os.name == 'nt' else "pg_ctl"
    pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", pg_ctl_name)
    pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
    if os.path.exists(pg_ctl) and os.path.exists(pg_data):
        pg_running = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", int(pg_port))) == 0:
                    pg_running = True
        except Exception:
            pass

        if not pg_running:
            print(f"[*] Launching Portable PostgreSQL on port {pg_port}...")
            pg_log = os.path.join(pg_data, "server.log")
            pg_cmd = [pg_ctl, "start", "-D", pg_data, "-l", pg_log, "-o", f"-F -p {pg_port}"]
            subprocess.run(pg_cmd, creationflags=hide_flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    print(f"[*] Launching API on port {hub_port}...")
    api_proc = subprocess.Popen(api_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(api_proc)
    all_pids.append(api_proc.pid)
    threading.Thread(target=stream_logs, args=(api_proc, "[API]", log_file, log_lock), daemon=True).start()

    public_proc = subprocess.Popen(public_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(public_proc)
    all_pids.append(public_proc.pid)
    threading.Thread(target=stream_logs, args=(public_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] PC owner home: https://localhost:{hub_port}/; phone/tunnel entry point: port {public_port}')
    local_proc = subprocess.Popen(local_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(local_proc)
    all_pids.append(local_proc.pid)
    threading.Thread(target=stream_logs, args=(local_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] Local management: http://127.0.0.1:{local_port}/manage')

    if shortcuts_enabled:
        shortcut_proc = subprocess.Popen(shortcuts_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
        processes.append(shortcut_proc)
        all_pids.append(shortcut_proc.pid)
        threading.Thread(target=stream_logs, args=(shortcut_proc, '[API]', log_file, log_lock), daemon=True).start()
        suffix = '' if shortcuts_port == 80 else f':{shortcuts_port}'
        print(f'[*] Local entry: http://localhost{suffix}/; chat: /hub; owner panel: /panel; Router: /orionrouter')
    
    mobile_proc = subprocess.Popen(mobile_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(mobile_proc)
    all_pids.append(mobile_proc.pid)
    threading.Thread(target=stream_logs, args=(mobile_proc, '[API]', log_file, log_lock), daemon=True).start()
    print(f'[*] Mobile pinned HTTPS: port {mobile_port}')

    print("[*] Launching Worker...")
    def start_worker():
        proc = subprocess.Popen(worker_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
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
            log_file, stopping)
        if flutter_proc is not None:
            processes.append(flutter_proc)
            all_pids.append(flutter_proc.pid)
    except (OSError, ValueError) as error:
        print(f'[!] Flutter UI could not start: {error}', flush=True)
    
    # Write ALL PIDs to hub.pid so manager.py stop can cleanly kill them all
    write_pids(pid_path, all_pids)
    print("[*] Tailing logs (Close this window to leave processes running in background)...\n" + "-"*40)
    
    def handle_shutdown(*args):
        # Set this before terminating children, so shutdown cannot trigger a restart.
        stopping.set()
        stop_hub_browsers(hub_dir)
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
        for p in processes:
            try:
                p.wait(timeout=5)
            except Exception:
                pass
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
        while not stopping.wait(1):
            if all(p.poll() is not None for p in processes):
                break
            supervisor.check()
    except KeyboardInterrupt:
        pass
    finally:
        handle_shutdown()

if __name__ == "__main__":
    main()
