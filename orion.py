import os
import sys
import subprocess
import socket
import time
import threading
import shutil
import urllib.request
import tempfile
import signal
import json

# --- YAPILANDIRMA ---
SERVICE_MAP = {
    "installer": ("installer", "app")
}

# --- THREAD SAFETY ---
log_lock = threading.Lock()

# --- YARDIMCI FONKSİYONLAR ---
def get_python_executable(venv_dir):
    if os.name == 'nt':
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")

def load_env_port(key, default):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ports = {}

    global_env = os.path.join(base_dir, "services", ".env.global")
    if os.path.exists(global_env):
        try:
            with open(global_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        ports[k.strip()] = v.strip()
        except Exception:
            pass

    local_env = os.path.join(base_dir, "services", ".env.global.local")
    if os.path.exists(local_env):
        try:
            with open(local_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        ports[k.strip()] = v.strip()
        except Exception:
            pass

    val = ports.get(key)
    if val and val.isdigit():
        return int(val)
    return default

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def wait_for_port(port, timeout=120, interval=0.25):
    """Bir portun gerçekten dinlemeye başlamasını bekler ve kaç saniye sürdüğünü döner.
    Zaman aşımına uğrarsa None döner. Bu, 'process spawn edildi' ile
    'servis gerçekten istek almaya hazır' arasındaki farkı ölçmek için kullanılır."""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(port):
            return time.time() - start
        time.sleep(interval)
    return None

def tail_file(path, n=25):
    """Bir log dosyasının son n satırını döner. PostgreSQL'in kendi log dosyasını
    otomatik göstererek yavaş açılışın gerçek sebebini (crash recovery, checkpoint,
    hata vb.) elle aramaya gerek kalmadan görmek için kullanılır."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []

# --- DOCKER (Windows / macOS / Linux ortak) ---
def _try_launch_windows_docker():
    appdata = os.environ.get("APPDATA", "")
    docker_apps = [
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Docker Desktop.lnk"),
        r"C:\Program Files\Rancher Desktop\Rancher Desktop.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Rancher Desktop\Rancher Desktop.exe")
    ]
    for app in docker_apps:
        if os.path.exists(app):
            print(f"    Found: {app}")
            try:
                os.startfile(app)
                return True
            except Exception as e:
                print(f"    Failed to start {app}: {e}")
    return False

def _try_launch_macos_docker():
    docker_app_paths = ["/Applications/Docker.app", "/Applications/Rancher Desktop.app"]
    for app in docker_app_paths:
        if os.path.exists(app):
            print(f"    Found: {app}")
            try:
                subprocess.run(["open", "-a", app], check=True)
                return True
            except Exception as e:
                print(f"    Failed to start {app}: {e}")
    return False

def ensure_docker_running():
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    print("[!] Docker daemon is not running. Attempting to start it...")

    launched = False
    if os.name == 'nt':
        launched = _try_launch_windows_docker()
    elif sys.platform == 'darwin':
        launched = _try_launch_macos_docker()
    else:
        print("\n[ERROR] Docker daemon is not running.")
        print("  Please start it manually, e.g.: sudo systemctl start docker")
        sys.exit(1)

    if launched:
        max_retry = 12
        for i in range(1, max_retry + 1):
            time.sleep(5)
            try:
                subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                print("\n[OK] Docker is now running.")
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                dots = "." * i
                print(f"\r    Waiting for Docker{dots} ({i * 5}s)", end="", flush=True)
        print()

    print("\n[ERROR] Could not start Docker automatically.")
    print("  Please start your Docker runtime manually and try again.")
    sys.exit(1)

def ensure_venv(target_dir, venv_dir):
    pyvenv_cfg = os.path.join(venv_dir, "pyvenv.cfg")
    is_broken = os.path.exists(venv_dir) and not os.path.exists(pyvenv_cfg)

    if os.path.exists(venv_dir) and not is_broken:
        return

    if is_broken:
        print(f"[!] Bozuk sanal ortam tespit edildi, temizleniyor: {venv_dir}")
        try:
            shutil.rmtree(venv_dir)
        except Exception as e:
            print(f"[!] Hata: Eski venv silinemedi: {e}")

    print(f"[!] Sanal ortam oluşturuluyor: {target_dir}")
    uv_check = subprocess.run(["uv", "--version"], capture_output=True)

    if uv_check.returncode == 0:
        subprocess.run(["uv", "venv"], cwd=target_dir, check=True)
    else:
        subprocess.run([sys.executable, "-m", "venv", ".venv"], cwd=target_dir, check=True)

def sync_dependencies(target_dir, venv_dir, py_exe):
    pyproj = os.path.join(target_dir, "pyproject.toml")
    sync_marker = os.path.join(venv_dir, ".last_sync")

    if not os.path.exists(pyproj):
        return

    needs_sync = not os.path.exists(sync_marker) or os.path.getmtime(pyproj) > os.path.getmtime(sync_marker)
    if not needs_sync:
        return

    print(f"[>] Bağımlılıklar güncelleniyor: {target_dir}")
    try:
        subprocess.run(["uv", "pip", "install", "--link-mode=copy", "-e", ".[dev]"], cwd=target_dir, check=True)
    except Exception:
        subprocess.run([py_exe, "-m", "pip", "install", "-e", ".[dev]"], cwd=target_dir, check=True)

    with open(sync_marker, "w") as f:
        f.write("ok")

def setup_environment(sub_dir):
    target_dir = os.path.join(os.path.dirname(__file__), sub_dir)
    venv_dir = os.path.join(target_dir, ".venv")
    py_exe = get_python_executable(venv_dir)

    ensure_venv(target_dir, venv_dir)
    sync_dependencies(target_dir, venv_dir, py_exe)

    return py_exe

# --- KOMUT ÇALIŞTIRICI ---
def cmd_run(alias, extra_args):
    sub_dir, module = SERVICE_MAP.get(alias, (None, None))
    if not sub_dir:
        print(f"[!] Kritik Hata: '{alias}' için konfigürasyon bulunamadı.")
        return

    py_exe = setup_environment(sub_dir)
    print(f"\n[OK] {alias.upper()} başlatılıyor...\n" + "-"*40)

    cmd = [py_exe, "-m", module] + extra_args
    target_cwd = os.path.join(os.path.dirname(__file__), sub_dir)

    proc = None
    try:
        proc = subprocess.Popen(cmd, cwd=target_cwd)
        proc.wait()
    except KeyboardInterrupt:
        print("\n[!] Durduruldu.")
        if proc:
            try:
                if os.name == 'nt':
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
                    proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception as e:
        print(f"\n[X] Kritik Hata: {e}")

def get_mode_from_args(args):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    mode = "local" if os.path.exists(os.path.join(base_dir, "services", "hub", ".venv")) else "docker"
    if args:
        normalized_args = [arg.strip().lower() for arg in args]
        if "--local" in normalized_args or "local" in normalized_args:
            mode = "local"
        elif "--docker" in normalized_args or "docker" in normalized_args:
            mode = "docker"
    return mode

def find_orionrouter_script():
    if os.name == 'nt':
        appdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            ps1_path = os.path.join(appdata, "OrionRouter", "orionrouter.ps1")
            if os.path.exists(ps1_path):
                return "win", ps1_path
    else:
        sh_path = os.path.expanduser("~/.local/share/OrionRouter/orionrouter")
        if os.path.exists(sh_path):
            return "unix", sh_path

    cmd = shutil.which("orionrouter")
    if cmd:
        return "cmd", cmd
    return None, None

# --- POSTGRESQL YARDIMCILARI (stale PID / lock sorunu için) ---
def get_pg_status(pg_ctl, pg_data):
    """PostgreSQL'in GERÇEKTEN çalışıp çalışmadığını döner (pg_ctl status ile)."""
    try:
        result = subprocess.run([pg_ctl, "status", "-D", pg_data], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False

def clean_stale_postmaster_lock(pg_data):
    """
    postmaster.pid dosyası, sunucu force-kill ile kapatıldığında geride kalabilir.
    Bir sonraki 'pg_ctl stop' çağrısı bu bayat PID'e sinyal göndermeye çalışıp
    'No such process' hatası verir. Bu fonksiyon o dosyayı temizler.
    """
    lock_file = os.path.join(pg_data, "postmaster.pid")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass

def start_postgres_blocking(pg_ctl, pg_data, show_terminals, result_holder):
    """
    pg_ctl start -w komutu doğası gereği bloklayıcıdır (sunucu hazır olana kadar bekler).
    Router ve Hub'ı bekletmemek için bu fonksiyon ayrı bir thread içinde çalıştırılır.
    """
    pg_log_dir = os.path.join(pg_data, "log")
    if not os.path.exists(pg_log_dir):
        os.makedirs(pg_log_dir)
    pg_log_file = os.path.join(pg_log_dir, "postgres.log")

    cmd = [pg_ctl, "start", "-D", pg_data, "-l", pg_log_file, "-w"]
    try:
        if os.name == 'nt':
            NO_WINDOW = 0x08000000 | 0x00000200
            if show_terminals:
                proc = subprocess.run(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                proc = subprocess.run(
                    cmd, creationflags=NO_WINDOW,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        else:
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        result_holder["success"] = (proc.returncode == 0)
    except Exception as e:
        result_holder["success"] = False
        result_holder["error"] = str(e)

# --- KOMUT İŞLEYİCİLER ---
def cmd_help(args=None):
    print("\n=== ORION AI ASSISTANT MANAGER ===")
    print("Usage: python orion.py <command> [options]")
    print("\nCommands:")
    print("  setup      Install dependencies and prepare environment")
    print("  start      Start all services (default: silent, background)")
    print("  stop       Stop all running services")
    print("  logs       View live logs of services")
    print("  status     Show running services and system status")
    print("  installer  Start the web-based installer")
    print("\nGlobal Mode Options (Auto-detected by default):")
    print("  --local    Force native execution on the host machine using Python/Node")
    print("  --docker   Force execution via Docker Compose")
    print("\nOptions for 'start':")
    print("  --show-terminals  Open separate, visible terminal windows for Hub and Router")
    print("\nOptions for 'logs':")
    print("  --api, --worker, --redis, --router   Show live logs for a specific service")
    print("  --hub (api, worker, redis)           Show all Hub logs combined")
    print("\n  Note: PostgreSQL manages its own log files securely inside .local_db/postgres/data/log/")
    print("==================================\n")

def handle_installer(args):
    mode = get_mode_from_args(args)
    print(f"[i] Installation Mode Set: {mode.upper()}")
    os.environ["ORION_INSTALL_MODE"] = mode
    if mode == "docker":
        ensure_docker_running()
    cmd_run("installer", [])

def handle_setup(args):
    mode = get_mode_from_args(args)
    print(f"\n==========================================")
    print(f"   Orion AI Assistant Setup ({mode.upper()})")
    print(f"==========================================\n")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    reqs = ["git", "python"] if mode == "local" else ["git", "docker", "python"]
    for req in reqs:
        if not shutil.which(req):
            print(f"[ERROR] Required tool not found: {req}")
            sys.exit(1)

    print("[OK] All required tools are present.")

    if mode == "local":
        local_db_script = os.path.join(base_dir, "local_db_setup.py")
        if os.path.exists(local_db_script):
            subprocess.run([sys.executable, local_db_script], check=True)

        print("[*] Setting up 'orion' global CLI command...")
        try:
            if os.name == 'nt':
                cmd_path = os.path.join(base_dir, "orion.cmd")
                with open(cmd_path, "w") as f:
                    f.write('@echo off\npython "%~dp0orion.py" %*\n')
                # Add to PATH using PowerShell
                ps_cmd = f"$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User'); if ($userPath -notlike '*{base_dir}*') {{ [Environment]::SetEnvironmentVariable('PATH', $userPath + ';{base_dir}', 'User') }}"
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd])
            else:
                sh_path = os.path.join(base_dir, "orion")
                with open(sh_path, "w") as f:
                    f.write('#!/usr/bin/env bash\npython3 "$(dirname "$0")/orion.py" "$@"\n')
                os.chmod(sh_path, 0o755)
                # Add to shell profiles
                export_line = f'\nexport PATH="$PATH:{base_dir}"\n'
                for profile in [os.path.expanduser("~/.bashrc"), os.path.expanduser("~/.zshrc"), os.path.expanduser("~/.profile")]:
                    if os.path.exists(profile):
                        with open(profile, "r") as f:
                            content = f.read()
                        if f'export PATH="$PATH:{base_dir}"' not in content:
                            with open(profile, "a") as f:
                                f.write(export_line)
        except Exception as e:
            print(f"[!] Warning: Could not setup global CLI command: {e}")

    print("[*] Setting up Installer Virtual Environment...")
    setup_environment("installer")
    print(f"\n[OK] Setup completed successfully! You can now use 'python orion.py start {mode}' or other commands.")

def handle_start(args):
    mode = get_mode_from_args(args)
    show_terminals = "--show-terminals" in args

    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hub_port = load_env_port("HUB_PORT", 8000)
        router_port = load_env_port("ROUTER_PORT", 20128)
        redis_port = load_env_port("REDIS_PORT", 6379)

        pg_ctl_name = "pg_ctl.exe" if os.name == 'nt' else "pg_ctl"
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", pg_ctl_name)
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")

        postgres_running = os.path.exists(pg_ctl) and os.path.exists(pg_data) and get_pg_status(pg_ctl, pg_data)
        redis_running = is_port_in_use(redis_port)
        hub_running = is_port_in_use(hub_port)
        router_running = is_port_in_use(router_port)

        if postgres_running and redis_running and hub_running and router_running:
            print("\n[!] All local services are already running.")
            print("    Please run 'python orion.py stop' before starting again.")
            return

        print(f"\nStarting Orion AI Assistant in {mode.upper()} mode" + (" (WITH VISIBLE TERMINALS)" if show_terminals else " (SILENT BACKGROUND)") + "...")
        print("[*] Launching PostgreSQL, Orion Router and Orion Hub in parallel...")

        # SADECE PENCERE GİZLEME VE YENİ GRUP BAYRAĞI (ÇAKIŞMAYAN KOMBİNASYON)
        NO_WINDOW = 0x08000000 | 0x00000200 if os.name == 'nt' else 0

        # 1. POSTGRESQL — bloklayıcı 'pg_ctl start -w' çağrısını ayrı bir thread'e alıyoruz
        #    ki Router ve Hub, PostgreSQL hazır olana kadar BEKLEMESİN.
        pg_thread = None
        pg_result = {}
        pg_start_time = None
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            if postgres_running:
                postgres_port = load_env_port("POSTGRES_PORT", 5432)
                print(f"[!] PostgreSQL is already running on port {postgres_port}.")
            else:
                # Önceki bir force-kill'den kalmış bayat kilit dosyası varsa temizle,
                # yoksa pg_ctl start bazı durumlarda gereksiz uyarı/çakışma verebiliyor.
                clean_stale_postmaster_lock(pg_data)
                print("[*] Starting Portable PostgreSQL (background thread, non-blocking)...")
                pg_start_time = time.time()
                pg_thread = threading.Thread(
                    target=start_postgres_blocking,
                    args=(pg_ctl, pg_data, show_terminals, pg_result),
                    daemon=True
                )
                pg_thread.start()

        # 2. ORION ROUTER — hemen, PostgreSQL'i beklemeden başlat
        plat, path = find_orionrouter_script()
        if path:
            if router_running:
                print(f"[!] Orion Router is already running on port {router_port}.")
            else:
                print("[*] Starting Orion Router (Local, Parallel)...")
                if plat == "win":
                    if show_terminals:
                        cmd_router = ["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start"]
                        subprocess.Popen(cmd_router, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    else:
                        cmd_router = ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start", "--silent"]
                        subprocess.Popen(cmd_router, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
                else:
                    if show_terminals:
                        cmd_router = [path, "start"]
                        subprocess.Popen(cmd_router)
                    else:
                        cmd_router = [path, "start", "--silent"]
                        subprocess.Popen(cmd_router, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            if not router_running:
                print("[!] Orion Router is not installed yet. Please install it from the Orion Installer panel.")

        # 3. POSTGRESQL'İN HAZIR OLMASINI BEKLE (sadece burada, Hub'ı başlatmadan önce)
        #    pg_ctl start -w zaten "database system is ready to accept connections"
        #    olana kadar bloke olur; bu join'i Hub'dan ÖNCEYE aldık ki Hub, DB hazır
        #    olmadan asla başlamasın. Router bundan etkilenmiyor, o zaten bağımsız.
        if pg_thread:
            print("[*] Waiting for PostgreSQL to become ready before starting Hub...")
            pg_thread.join()
            pg_elapsed = time.time() - pg_start_time if pg_start_time else None
            if pg_result.get("success"):
                suffix = f" (took {pg_elapsed:.1f}s)" if pg_elapsed is not None else ""
                print(f"[OK] PostgreSQL is ready.{suffix}")
                if pg_elapsed is not None and pg_elapsed > 8:
                    pg_log_file = os.path.join(pg_data, "log", "postgres.log")
                    log_tail = tail_file(pg_log_file, 25)
                    if log_tail:
                        print(f"\n[i] PostgreSQL took longer than expected ({pg_elapsed:.1f}s). Last lines of {pg_log_file}:")
                        print("    " + "-" * 60)
                        for line in log_tail:
                            print("    " + line.rstrip())
                        print("    " + "-" * 60)
                        print("    Look for lines like 'database system was not properly shut down'")
                        print("    or 'redo starts at' — these mean it's replaying WAL (crash recovery),")
                        print("    which is the most common reason for a slow local startup.\n")
            else:
                print(f"[!] PostgreSQL failed to start. Check the log at: {os.path.join(pg_data, 'log', 'postgres.log')}")
                print("[!] Starting Hub anyway, but it will likely fail to connect to the database.")

        # 4. ORION HUB (API, Worker, Redis) — artık DB hazır olduktan SONRA başlıyor
        hub_dir = os.path.join(base_dir, "services", "hub")
        hub_py = setup_environment("services/hub")
        if os.path.exists(os.path.join(hub_dir, "run_local.py")) and hub_py and os.path.exists(hub_py):
            if hub_running:
                print(f"[!] Orion Hub (API) is already running on port {hub_port}.")
            else:
                print("[*] Starting Orion Hub (API, Worker, Redis)...")
                log_path = os.path.join(hub_dir, "hub.log")
                pid_path = os.path.join(hub_dir, "hub.pid")
                if os.path.exists(log_path):
                    open(log_path, "w").close()
                if os.path.exists(pid_path):
                    os.remove(pid_path)

                if os.name == 'nt':
                    if show_terminals:
                        cmd_hub = ["cmd", "/k", hub_py, "run_local.py"]
                        subprocess.Popen(cmd_hub, cwd=hub_dir, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    else:
                        cmd_hub = [hub_py, "run_local.py"]
                        subprocess.Popen(cmd_hub, cwd=hub_dir, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    cmd_hub = [hub_py, "run_local.py"]
                    subprocess.Popen(cmd_hub, cwd=hub_dir, start_new_session=not show_terminals, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            if not hub_running:
                print("[!] Orion Hub files or environment missing. Cannot start Hub.")

        # 5. GERÇEK HAZIR OLMA SÜRELERİNİ ÖLÇ (tahmin değil, ölçüm)
        #    Router bağımsız başladığı için onun süresini hâlâ ölçüyoruz;
        #    Hub zaten DB'den sonra başladığı için onun süresi artık normalde çok kısa olmalı.
        readiness_results = {}
        measure_threads = []

        def _measure(name, port, timeout=120):
            elapsed = wait_for_port(port, timeout=timeout)
            readiness_results[name] = elapsed

        if path and not router_running:
            t = threading.Thread(target=_measure, args=("Orion Router", router_port), daemon=True)
            measure_threads.append(t)
        if os.path.exists(os.path.join(hub_dir, "run_local.py")) and hub_py and os.path.exists(hub_py) and not hub_running:
            t = threading.Thread(target=_measure, args=("Orion Hub (API)", hub_port), daemon=True)
            measure_threads.append(t)

        for t in measure_threads:
            t.start()
        for t in measure_threads:
            t.join()

        if measure_threads:
            print("\n--- ACTUAL READINESS TIMES (port gerçekten açıldı) ---")
            for name, elapsed in readiness_results.items():
                if elapsed is not None:
                    print(f"    {name.ljust(20)} : {elapsed:.1f}s")
                else:
                    print(f"    {name.ljust(20)} : did NOT open its port within timeout")
            print("--------------------------------------------------------")

        if show_terminals:
            print("\n[OK] Native processes have been started in separate visible windows.")
        else:
            print("\n[OK] All local services have been started in parallel in the background.")
            print("[i] To view live logs at any time, run: python orion.py logs")
    else:
        print(f"\nStarting Orion AI Assistant in {mode.upper()} mode...")
        if not shutil.which("docker"):
            print("[!] Docker is not installed.")
            return
        ensure_docker_running()
        subprocess.run(["docker", "compose", "up", "-d"])

def handle_stop(args):
    mode = get_mode_from_args(args)

    print(f"\nStopping Orion AI Assistant in {mode.upper()} mode...")

    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hub_dir = os.path.join(base_dir, "services", "hub")
        pid_path = os.path.join(hub_dir, "hub.pid")

        # 1. Hub süreçlerini PID'den güvenli kapat
        if os.path.exists(pid_path):
            try:
                with open(pid_path, "r") as f:
                    pids = [int(line.strip()) for line in f if line.strip().isdigit()]
                for pid in pids:
                    # Windows'ta yanlış PID öldürmemek için çalışan sürecin adını teyit et
                    if os.name == 'nt':
                        check_cmd = f'tasklist /FI "PID eq {pid}" /NH'
                        output = subprocess.check_output(check_cmd, shell=True).decode(errors='ignore')
                        if "python" not in output.lower() and "cmd" not in output.lower() and "redis" not in output.lower():
                            continue
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                if os.name != 'nt':
                    time.sleep(1.5)
                    for pid in pids:
                        try:
                            os.kill(pid, 0)
                            os.kill(pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                os.remove(pid_path)
                print("[OK] Hub services (API, Worker, Redis) stopped safely.")
            except Exception as e:
                print(f"[!] Could not read PID file: {e}")
        else:
            print("[!] No hub.pid found. Hub may not be running.")

        # 2. Router'ı kapat
        plat, path = find_orionrouter_script()
        if path:
            if plat == "win":
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "stop"], capture_output=True)
            else:
                subprocess.run([path, "stop"], capture_output=True)

        # 3. PostgreSQL'i kapat
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
        pg_ctl_name = "pg_ctl.exe" if os.name == 'nt' else "pg_ctl"
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", pg_ctl_name)
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            postgres_port_for_check = load_env_port("POSTGRES_PORT", 5432)
            if not get_pg_status(pg_ctl, pg_data):
                if is_port_in_use(postgres_port_for_check):
                    # pg_ctl status "çalışmıyor" diyor ama port hâlâ açık: postmaster.pid
                    # muhtemelen bir önceki crash/kill nedeniyle güncel değil, fakat
                    # postgres.exe hâlâ arka planda yaşıyor (orphan). Bunu sessizce
                    # es geçmek bir sonraki açılışta yine kirli-kapanma döngüsüne
                    # (uzun fsync + recovery) yol açar; o yüzden zorla kapatıyoruz.
                    print("[!] pg_ctl reports PostgreSQL as stopped, but its port is still open (orphaned process).")
                    print("[*] Force-stopping the orphaned PostgreSQL process to prevent an unclean shutdown...")
                    if os.name == 'nt':
                        subprocess.run(["taskkill", "/F", "/IM", "postgres.exe", "/T"], capture_output=True)
                    else:
                        subprocess.run(["pkill", "-9", "-f", "postgres"], capture_output=True)
                    print("[!] Orphaned PostgreSQL process force stopped.")
                else:
                    # PostgreSQL zaten çalışmıyor: "pg_ctl stop" çağırmak bayat postmaster.pid'e
                    # sinyal göndermeye çalışıp "No such process" hatası üretir. Bunun yerine
                    # doğrudan durumu bildir ve varsa bayat kilit dosyasını temizle.
                    print("[i] PostgreSQL is not running (already stopped). Cleaning up any stale lock file.")
                clean_stale_postmaster_lock(pg_data)
            else:
                print("[*] Attempting graceful PostgreSQL shutdown...")
                result = subprocess.run([pg_ctl, "stop", "-D", pg_data, "-m", "fast"], capture_output=True, text=True)

                if result.returncode == 0:
                    print("[OK] PostgreSQL stopped gracefully.")
                else:
                    print(f"[!] pg_ctl failed with code {result.returncode}.")
                    print(f"--- DETAYLI HATA LOGU ---\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}\n-------------------------")

                    if os.name == 'nt':
                        subprocess.run(["taskkill", "/F", "/IM", "postgres.exe", "/T"], capture_output=True)
                    else:
                        subprocess.run(["pkill", "-9", "-f", "postgres"], capture_output=True)
                    print("[!] PostgreSQL force stopped as fallback.")

                # Graceful ya da force, her durumda bir sonraki çalıştırmada aynı hatayı
                # tekrar görmemek için geride kalabilecek kilit dosyasını temizle.
                clean_stale_postmaster_lock(pg_data)

        # 4. Redis Güvenli Temizlik (Sadece Orion dizinindekileri hedefle)
        if os.name == 'nt':
            subprocess.run(["powershell", "-c", "Get-Process redis-server -ErrorAction SilentlyContinue | Where-Object {$_.Path -like '*orion*'} | Stop-Process -Force"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "redis-server.*orion"], capture_output=True)

        print("[OK] All local services stopped.")
    else:
        subprocess.run(["docker", "compose", "down"])

def handle_status(args):
    mode = get_mode_from_args(args)
    print(f"\n[*] Checking Orion AI Assistant status ({mode.upper()} mode)...\n")
    if mode == "local":
        print("--- CORE SERVICES ---")
        hub_port = load_env_port("HUB_PORT", 8000)
        router_port = load_env_port("ROUTER_PORT", 20128)
        postgres_port = load_env_port("POSTGRES_PORT", 5432)
        redis_port = load_env_port("REDIS_PORT", 6379)
        core_services = [
            ("PostgreSQL", postgres_port),
            ("Redis", redis_port),
            ("Orion Hub (API)", hub_port),
            ("Orion Router", router_port)
        ]
        all_running = True
        hub_running = False
        for name, port in core_services:
            is_running = is_port_in_use(port)
            if name == "Orion Hub (API)":
                hub_running = is_running

            status_text = "\033[92mRUNNING\033[0m" if is_running else "\033[91mSTOPPED\033[0m"
            print(f"  {name.ljust(20)} : {status_text}")
            if not is_running:
                all_running = False

        print("\n--- AI MODELS ---")
        models = [
            ("Orion LLM", "llama-cpp"),
            ("Orion Embedding", "llama-cpp-embed"),
            ("Orion TTS", "orion-tts")
        ]

        if hub_running:
            api_data = {}
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{hub_port}/api/services")
                with urllib.request.urlopen(req, timeout=1.0) as response:
                    res_data = json.loads(response.read().decode())
                    if isinstance(res_data, list):
                        for s in res_data:
                            api_data[s.get("id", "")] = s.get("status", "UNKNOWN")
                    elif isinstance(res_data, dict):
                        api_data = res_data
            except Exception:
                pass

            for name, sid in models:
                st = api_data.get(sid, {}).get("status") if isinstance(api_data.get(sid), dict) else api_data.get(sid)
                if not st or st == "UNKNOWN":
                    st = "DISABLED"
                color = "\033[92m" if st == "ACTIVE" else "\033[90m"
                print(f"  {name.ljust(20)} : {color}{st}\033[0m")
        else:
            for name, sid in models:
                print(f"  {name.ljust(20)} : \033[90mUNKNOWN (Hub is stopped)\033[0m")

        print("\n" + ("All core services are RUNNING." if all_running else "Some services are STOPPED. Run 'python orion.py start' to launch them."))
    else:
        subprocess.run(["docker", "compose", "ps"])

def safe_print(text):
    with log_lock:
        sys.stdout.write(text)
        sys.stdout.flush()

def handle_logs(args):
    mode = get_mode_from_args(args)
    filters = [arg.replace("--", "").upper() for arg in args if arg.startswith("--") and arg not in ["--local", "--docker"]]

    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base_dir, "services", "hub", "hub.log")

        filter_str = ", ".join(filters) if filters else "ALL"
        print(f"\n[*] Tailing Orion AI Assistant logs ({filter_str})... (Press CTRL+C to exit)\n" + "-"*50)

        COLORS = {
            "[API]": "\033[96m",
            "[WORKER]": "\033[93m",
            "[REDIS]": "\033[35m",
            "RESET": "\033[0m"
        }

        router_proc = None
        if not filters or "ROUTER" in filters:
            plat, path = find_orionrouter_script()
            if path and plat == "win":
                router_proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "logs"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                )

                def tail_router(proc):
                    try:
                        for line in iter(proc.stdout.readline, b''):
                            decoded = line.decode('utf-8', errors='replace')
                            safe_print(f"\033[95m[ROUTER]\033[0m {decoded}")
                    except:
                        pass

                threading.Thread(target=tail_router, args=(router_proc,), daemon=True).start()

        try:
            hub_filters = []
            if "HUB" in filters:
                hub_filters = ["API", "WORKER", "REDIS"]
            else:
                hub_filters = [f for f in filters if f in ["API", "WORKER", "REDIS"]]

            if not filters or hub_filters:
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for line in lines[-30:]:
                            if not filters or any(line.startswith(f"[{hf}]") for hf in hub_filters):
                                colored_line = line
                                for prefix, color in COLORS.items():
                                    if prefix != "RESET" and line.startswith(prefix):
                                        colored_line = line.replace(prefix, f"{color}{prefix}{COLORS['RESET']}", 1)
                                        break
                                safe_print(colored_line)

                        while True:
                            line = f.readline()
                            if not line:
                                time.sleep(0.1)
                                continue

                            if not filters or any(line.startswith(f"[{hf}]") for hf in hub_filters):
                                colored_line = line
                                for prefix, color in COLORS.items():
                                    if prefix != "RESET" and line.startswith(prefix):
                                        colored_line = line.replace(prefix, f"{color}{prefix}{COLORS['RESET']}", 1)
                                        break
                                safe_print(colored_line)
                else:
                    while True:
                        time.sleep(1)
            else:
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            if router_proc:
                try:
                    router_proc.terminate()
                except:
                    pass
            print(f"\n{COLORS['RESET']}[OK] Stopped streaming logs.")
    else:
        subprocess.run(["docker", "compose", "logs", "-f"])

COMMANDS = {
    "installer": handle_installer,
    "setup": handle_setup,
    "start": handle_start,
    "stop": handle_stop,
    "logs": handle_logs,
    "status": handle_status,
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    action = sys.argv[1].lower()
    args = sys.argv[2:]

    handler = COMMANDS.get(action)

    if handler:
        handler(args)
    else:
        print(f"[!] Hata: Geçersiz komut '{action}'")
        print("Geçerli komutları görmek için: python orion.py help")