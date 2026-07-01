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

    try:
        subprocess.run(cmd, cwd=target_cwd)
    except KeyboardInterrupt:
        print("\n[!] Durduruldu.")
    except Exception as e:
        print(f"\n[X] Kritik Hata: {e}")


# --- KOMUT İŞLEYİCİLER (Open/Closed Principle) ---
def cmd_help(args=None):
    print("\n=== ORION AI ASSISTANT MANAGER ===")
    print("Kullanım:")
    print("  python orion.py installer")
    print("Bilgi:")
    print("  Diğer servisler Docker üzerinden yönetilmelidir.")
    print("==================================\n")

def handle_installer(args):
    """Sadece ve sadece 'installer' komutunu kabul eder."""
    if args:
        print(f"[!] Hata: 'installer' komutu ek parametre almaz. Fazla parametre: {args[0]}")
        return
    ensure_docker_running()
    cmd_run("installer", [])


# YENİ KOMUT EKLENECEĞİ ZAMAN SADECE BU SÖZLÜĞE YAZILACAK
COMMANDS = {
    "installer": handle_installer,
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