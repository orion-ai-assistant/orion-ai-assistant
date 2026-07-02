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


# --- KOMUT İŞLEYİCİLER (Open/Closed Principle) ---
def cmd_help(args=None):
    print("\n=== ORION AI ASSISTANT MANAGER ===")
    print("  orion setup [local|docker]   : Prepare the environment")
    print("  orion start [local|docker]   : Start services")
    print("  orion stop [local|docker]    : Stop services")
    print("  orion installer              : Start GUI installer")
    print("==================================\n")

def handle_installer(args):
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
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
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
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
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
    print(f"\nStarting Orion AI Assistant in {mode.upper()} mode...")
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hub_dir = os.path.join(base_dir, "services", "hub")
        hub_py = setup_environment("services/hub")
        cflags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", "pg_ctl.exe")
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            print("[*] Starting Portable PostgreSQL...")
            subprocess.run([pg_ctl, "start", "-D", pg_data, "-w"])
            
        redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
        if os.path.exists(redis_exe):
            print("[*] Starting Portable Redis...")
            subprocess.Popen([redis_exe], creationflags=cflags)
        
        if os.path.exists(os.path.join(hub_dir, "run_local.py")) and hub_py and os.path.exists(hub_py):
            print("[*] Starting Orion Hub (Local)...")
            cmd_hub = ["cmd", "/k", hub_py, "run_local.py"] if os.name == 'nt' else [hub_py, "run_local.py"]
            subprocess.Popen(cmd_hub, cwd=hub_dir, creationflags=cflags)
            
        plat, path = find_orionrouter_script()
        if path:
            print("[*] Starting Orion Router (Local)...")
            if plat == "win":
                cmd_router = ["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start"]
                subprocess.Popen(cmd_router, creationflags=cflags)
            else:
                subprocess.Popen([path, "start"], creationflags=cflags)
        else:
            print("[!] Orion Router is not installed yet. Please run 'python orion.py setup local' first.")
            
        print("[OK] Native processes have been started in separate windows/background.")
    else:
        import shutil
        if not shutil.which("docker"):
            print("[!] Docker is not installed.")
            return
        ensure_docker_running()
        subprocess.run(["docker", "compose", "up", "-d"])

def handle_stop(args):
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
    print(f"\nStopping Orion AI Assistant in {mode.upper()} mode...")
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if os.name == 'nt':
            subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "run_local.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
            plat, path = find_orionrouter_script()
            if path and plat == "win":
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "stop"], capture_output=True)
            else:
                subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "orionrouter" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
            
            pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
            pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", "pg_ctl.exe")
            if os.path.exists(pg_ctl) and os.path.exists(pg_data):
                subprocess.run([pg_ctl, "stop", "-D", pg_data, "-m", "fast"])
            
            subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "redis-server.exe" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "run_local.py"], capture_output=True)
            plat, path = find_orionrouter_script()
            if path:
                subprocess.run([path, "stop"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "orionrouter"], capture_output=True)
        print("[OK] Local Python processes stopped.")


# YENİ KOMUT EKLENECEĞİ ZAMAN SADECE BU SÖZLÜĞE YAZILACAK
COMMANDS = {
    "installer": handle_installer,
    "setup": handle_setup,
    "start": handle_start,
    "stop": handle_stop,
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