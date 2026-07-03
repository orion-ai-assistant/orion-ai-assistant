import os
import sys
import subprocess

# --- YAPILANDIRMA ---
SERVICE_MAP = {
    "installer": ("installer", "app")
}

# --- YARDIMCI FONKSİYONLAR ---
def get_python_executable(venv_dir):
    if os.name == 'nt':
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")

def ensure_docker_running():
    import time
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    if os.name != 'nt':
        print("\n[ERROR] Docker daemon is not running. Please start Docker manually and try again.")
        sys.exit(1)

    print("[!] Docker daemon is not running. Attempting to start it...")
    
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    
    docker_apps = [
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Docker Desktop.lnk"),
        r"C:\Program Files\Rancher Desktop\Rancher Desktop.exe",
        os.path.join(localappdata, r"Programs\Rancher Desktop\Rancher Desktop.exe")
    ]
    
    launched = False
    for app in docker_apps:
        if os.path.exists(app):
            print(f"    Found: {app}")
            try:
                os.startfile(app)
                launched = True
                break
            except Exception as e:
                print(f"    Failed to start {app}: {e}")
                
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
    """Sanal ortam yoksa veya bozuksa oluşturur."""
    pyvenv_cfg = os.path.join(venv_dir, "pyvenv.cfg")
    is_broken = os.path.exists(venv_dir) and not os.path.exists(pyvenv_cfg)
    
    if os.path.exists(venv_dir) and not is_broken:
        return

    if is_broken:
        print(f"[!] Bozuk sanal ortam tespit edildi, temizleniyor: {venv_dir}")
        import shutil
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
    """Gerekirse bağımlılıkları günceller."""
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
    """Tüm ortam hazırlıklarını yönetir ve python yolunu döner."""
    target_dir = os.path.join(os.path.dirname(__file__), sub_dir)
    venv_dir = os.path.join(target_dir, ".venv")
    py_exe = get_python_executable(venv_dir)

    ensure_venv(target_dir, venv_dir)
    sync_dependencies(target_dir, venv_dir, py_exe)
    
    return py_exe


# --- KOMUT ÇALIŞTIRICI ---
def cmd_run(alias, extra_args):
    """Seçilen servisi ayağa kaldırır."""
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
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
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
        if "--local" in args or "local" in args:
            mode = "local"
        elif "--docker" in args or "docker" in args:
            mode = "docker"
    return mode

# --- KOMUT İŞLEYİCİLER (Open/Closed Principle) ---
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
        
    os.environ["ORION_INSTALL_MODE"] = mode
    
    if mode == "docker":
        ensure_docker_running()
        
    cmd_run("installer", [])

def find_orionrouter_script():
    import shutil
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

def handle_setup(args):
    mode = get_mode_from_args(args)
        
    print(f"\n==========================================")
    print(f"   Orion AI Assistant Setup ({mode.upper()})")
    print(f"==========================================\n")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    reqs = ["git", "python"] if mode == "local" else ["git", "docker", "python"]
    for req in reqs:
        import shutil
        if not shutil.which(req):
            print(f"[ERROR] Required tool not found: {req}")
            sys.exit(1)
            
    print("[OK] All required tools are present.")
    
    if mode == "local":
        local_db_script = os.path.join(base_dir, "local_db_setup.py")
        if os.path.exists(local_db_script):
            subprocess.run([sys.executable, local_db_script], check=True)
            
        print("[*] Installing Orion Router globally...")
        import urllib.request
        import tempfile
        if os.name == 'nt':
            script_url = "https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.ps1"
            with urllib.request.urlopen(script_url) as response:
                script_content = response.read().decode('utf-8')
            # Prevent auto-start
            script_content = script_content.replace('& (Join-Path $TargetFolder "orionrouter.ps1") start', '')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ps1") as tmp:
                tmp.write(script_content.encode('utf-8'))
                tmp_path = tmp.name
            
            subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", tmp_path, "local"], check=True)
            os.remove(tmp_path)
        else:
            script_url = "https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.sh"
            with urllib.request.urlopen(script_url) as response:
                script_content = response.read().decode('utf-8')
            # Prevent auto-start
            script_content = script_content.replace('orionrouter start', '')
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".sh") as tmp:
                tmp.write(script_content.encode('utf-8'))
                tmp_path = tmp.name
                
            subprocess.run(["bash", tmp_path, "local"], check=True)
            os.remove(tmp_path)
    print("[*] Setting up Installer Virtual Environment...")
    setup_environment("installer")
    print(f"\n[OK] Setup completed successfully! You can now use 'python orion.py start {mode}' or other commands.")

def handle_start(args):
    mode = get_mode_from_args(args)
    show_terminals = "--show-terminals" in args
    
    if mode == "local":
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", 8000)) == 0:
                print("\n[!] Orion Hub (API) is already running on port 8000.")
                print("    Please run 'python orion.py stop' before starting again.")
                return
                
    print(f"\nStarting Orion AI Assistant in {mode.upper()} mode" + (" (WITH VISIBLE TERMINALS)" if show_terminals else " (SILENT BACKGROUND)") + "...")
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hub_dir = os.path.join(base_dir, "services", "hub")
        hub_py = setup_environment("services/hub")
        
        # Temiz başlangıç için eski log ve PID dosyalarını sıfırla
        log_path = os.path.join(hub_dir, "hub.log")
        pid_path = os.path.join(hub_dir, "hub.pid")
        if os.path.exists(log_path):
            open(log_path, "w").close()
        if os.path.exists(pid_path):
            os.remove(pid_path)
            
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", "pg_ctl.exe")
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            print("[*] Starting Portable PostgreSQL...")
            cflags_pg = subprocess.CREATE_NEW_CONSOLE if (os.name == 'nt' and show_terminals) else (0x00000200 if os.name == 'nt' else 0)
            subprocess.run([pg_ctl, "start", "-D", pg_data, "-w"], creationflags=cflags_pg)
            
        redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
        if os.path.exists(redis_exe):
            print("[*] Starting Portable Redis...")
            cflags_redis = subprocess.CREATE_NEW_CONSOLE if (os.name == 'nt' and show_terminals) else (0x08000200 if os.name == 'nt' else 0)
            subprocess.Popen([redis_exe], creationflags=cflags_redis)
        
        if os.path.exists(os.path.join(hub_dir, "run_local.py")) and hub_py and os.path.exists(hub_py):
            print("[*] Starting Orion Hub (Local)...")
            cflags_hub = subprocess.CREATE_NEW_CONSOLE if (os.name == 'nt' and show_terminals) else (0x08000200 if os.name == 'nt' else 0)
            cmd_hub = ["cmd", "/k", hub_py, "run_local.py"] if (os.name == 'nt' and show_terminals) else [hub_py, "run_local.py"]
            subprocess.Popen(cmd_hub, cwd=hub_dir, creationflags=cflags_hub, start_new_session=(os.name != 'nt' and not show_terminals))
            
        plat, path = find_orionrouter_script()
        if path:
            print("[*] Starting Orion Router (Local)...")
            if plat == "win":
                if show_terminals:
                    cmd_router = ["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start"]
                    subprocess.Popen(cmd_router, creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    cmd_router = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start", "--silent"]
                    subprocess.Popen(cmd_router, creationflags=0x08000200)
            else:
                if show_terminals:
                    cmd_router = [path, "start"]
                    subprocess.Popen(cmd_router)
                else:
                    cmd_router = [path, "start", "--silent"]
                    subprocess.Popen(cmd_router, start_new_session=True)
        else:
            print("[!] Orion Router is not installed yet. Please run 'python orion.py setup --local' first.")
            
        if show_terminals:
            print("\n[OK] Native processes have been started in separate visible windows.")
        else:
            print("\n[OK] All local services have been started silently in the background.")
            print("[ℹ] To view live logs at any time, run: python orion.py logs")
    else:
        import shutil
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
        
        # 1. Hub süreçlerini PID dosyasından oku ve kapat (Cross-platform, pure Python)
        if os.path.exists(pid_path):
            try:
                with open(pid_path, "r") as f:
                    pids = [int(line.strip()) for line in f if line.strip().isdigit()]
                for pid in pids:
                    try:
                        if os.name == 'nt':
                            import signal
                            os.kill(pid, signal.SIGTERM)
                        else:
                            import signal
                            os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass  # Zaten kapanmış
                import time
                time.sleep(1.5)
                # Hala açık olanları zorla kapat
                for pid in pids:
                    try:
                        os.kill(pid, 0)  # Hala yaşıyor mu kontrol et
                        import signal
                        os.kill(pid, signal.SIGKILL if os.name != 'nt' else signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                os.remove(pid_path)
                print("[OK] Hub services (API, Worker, Redis) stopped.")
            except Exception as e:
                print(f"[!] Could not read PID file: {e}")
        else:
            print("[!] No hub.pid found. Hub may not be running or was started with an older version.")
        
        # 2. Router'ı kapat
        plat, path = find_orionrouter_script()
        if path:
            if plat == "win":
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "stop"], capture_output=True)
            else:
                subprocess.run([path, "stop"], capture_output=True)
        
        # 3. PostgreSQL'i kapat (pg_ctl, cross-platform binary)
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
        pg_ctl_name = "pg_ctl.exe" if os.name == 'nt' else "pg_ctl"
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", pg_ctl_name)
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            result = subprocess.run([pg_ctl, "stop", "-D", pg_data, "-m", "fast"], capture_output=True)
            if result.returncode == 0:
                print("[OK] PostgreSQL stopped.")
        
        print("[OK] All local services stopped.")

def handle_status(args):
    mode = get_mode_from_args(args)
    print(f"\n[*] Checking Orion AI Assistant status ({mode.upper()} mode)...\n")
    if mode == "local":
        print("--- CORE SERVICES ---")
        core_services = [
            ("Orion Hub (API)", 8000),
            ("Orion Router", 20128)
        ]
        all_running = True
        hub_running = False
        import socket
        for name, port in core_services:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                is_running = (s.connect_ex(("127.0.0.1", port)) == 0)
            
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
            import urllib.request
            import json
            api_data = {}
            try:
                req = urllib.request.Request("http://127.0.0.1:8000/api/services")
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
                    st = "DISABLED" # Eğer API'den gelmiyorsa veya kapalıysa DEVRE DIŞI
                color = "\033[92m" if st == "ACTIVE" else "\033[90m"
                print(f"  {name.ljust(20)} : {color}{st}\033[0m")
        else:
            for name, sid in models:
                print(f"  {name.ljust(20)} : \033[90mUNKNOWN (Hub is stopped)\033[0m")
                
        print("\n" + ("All core services are RUNNING." if all_running else "Some services are STOPPED. Run 'python orion.py start' to launch them."))
    else:
        import subprocess
        subprocess.run(["docker", "compose", "ps"])

def handle_logs(args):
    mode = get_mode_from_args(args)
    filters = [arg.replace("--", "").upper() for arg in args if arg.startswith("--") and arg not in ["--local", "--docker"]]
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base_dir, "services", "hub", "hub.log")
        
        filter_str = ", ".join(filters) if filters else "ALL"
        print(f"\n[*] Tailing Orion AI Assistant logs ({filter_str})... (Press CTRL+C to exit)\n" + "-"*50)
        
        COLORS = {
            "[API]": "\033[96m",     # Cyan
            "[WORKER]": "\033[93m",  # Yellow
            "[REDIS]": "\033[35m",   # Magenta (Changed from Red to avoid error confusion)
            "RESET": "\033[0m"
        }
        
        import time, threading
        
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
                            sys.stdout.write(f"\033[95m[ROUTER]\033[0m {decoded}")
                            sys.stdout.flush()
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
                                sys.stdout.write(colored_line)
                        sys.stdout.flush()
                        
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
                                sys.stdout.write(colored_line)
                                sys.stdout.flush()
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

# YENİ KOMUT EKLENECEĞİ ZAMAN SADECE BU SÖZLÜĞE YAZILACAK
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

# --- ANA YÖNLENDİRİCİ ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    action = sys.argv[1].lower()
    args = sys.argv[2:]

    # Komutu sözlükten getir
    handler = COMMANDS.get(action)
    
    if handler:
        handler(args)
    else:
        print(f"[!] Hata: Geçersiz komut '{action}'")
        print("Geçerli komutları görmek için: python orion.py help")