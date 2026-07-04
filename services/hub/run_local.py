import os
import sys
import subprocess
import signal
import time
import threading

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

def main():
    if os.name == 'nt':
        os.system("")  # Enable ANSI colors in Windows cmd
        
    print("==========================================")
    print(" Starting Orion Hub natively (Local Mode)")
    print("==========================================")
    
    # Environment config
    hub_port = os.environ["HUB_PORT"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    env["PYTHONUNBUFFERED"] = "1"
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    hub_dir = os.path.dirname(os.path.abspath(__file__))
    pid_path = os.path.join(hub_dir, "hub.pid")
    log_path = os.path.join(hub_dir, "hub.log")
    
    # Commands
    api_cmd = [sys.executable, "-m", "uvicorn", "orion.api.main:app", "--host", "127.0.0.1", "--port", hub_port]
    worker_cmd = [sys.executable, "-m", "orion.worker.main"]
    redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
    
    hide_flags = 0x08000000 if os.name == 'nt' else 0
    
    log_file = open(log_path, "a", encoding="utf-8")
    log_lock = threading.Lock()
    
    processes = []
    all_pids = [os.getpid()]  # Include this process (run_local.py) itself
    
    if os.path.exists(redis_exe):
        print("[*] Launching Portable Redis...")
        redis_proc = subprocess.Popen([redis_exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
        processes.append(redis_proc)
        all_pids.append(redis_proc.pid)
        threading.Thread(target=stream_logs, args=(redis_proc, "[REDIS]", log_file, log_lock), daemon=True).start()
        
    print(f"[*] Launching API on port {hub_port}...")
    api_proc = subprocess.Popen(api_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(api_proc)
    all_pids.append(api_proc.pid)
    threading.Thread(target=stream_logs, args=(api_proc, "[API]", log_file, log_lock), daemon=True).start()
    
    print("[*] Launching Worker...")
    worker_proc = subprocess.Popen(worker_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=hide_flags)
    processes.append(worker_proc)
    all_pids.append(worker_proc.pid)
    threading.Thread(target=stream_logs, args=(worker_proc, "[WORKER]", log_file, log_lock), daemon=True).start()
    
    # Write ALL PIDs to hub.pid so orion.py stop can cleanly kill them all
    write_pids(pid_path, all_pids)
    print("[*] Tailing logs (Close this window to leave processes running in background)...\n" + "-"*40)
    
    def handle_shutdown(*args):
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
        
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, handle_shutdown)
    
    try:
        while True:
            time.sleep(1)
            if all(p.poll() is not None for p in processes):
                break
    except KeyboardInterrupt:
        pass
    
    handle_shutdown()

if __name__ == "__main__":
    main()
