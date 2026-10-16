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

def _read_env_files():
    """Her iki global env dosyasını okuyup birleştirilmiş bir dict döner.
    .env.global.local değerleri .env.global'ı override eder."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    result = {}
    for env_name in [".env.global", ".env.global.local"]:
        env_path = os.path.join(base_dir, "services", env_name)
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            result[k.strip()] = v.strip()
            except Exception:
                pass
    return result

def load_env_port(key):
    values = _read_env_files()
    if key == 'ROUTER_PORT':
        # The Router owns its TLS listener port; old Hub routing ports are not it.
        val = os.getenv('ORION_ROUTER_TLS_PORT') or values.get('ORION_ROUTER_TLS_PORT', '9443')
        _, script = find_orionrouter_script()
        if script:
            for filename in ('.env', '.env.local'):
                candidate = os.path.join(os.path.dirname(os.path.abspath(script)), filename)
                if os.path.isfile(candidate):
                    with open(candidate, encoding='utf-8') as settings:
                        for line in settings:
                            if line.strip().startswith('ORION_ROUTER_TLS_PORT='):
                                val = line.strip().split('=', 1)[1].strip().strip('"\'')
    else:
        val = values.get(key)
    if val is None:
        raise RuntimeError(f"Missing required port env var: {key}")
    try:
        return int(val)
    except ValueError as e:
        raise RuntimeError(f"Invalid port value for {key}: {val}") from e

def load_env_var(key, default=None):
    """Global env dosyalarından string bir değeri okur."""
    return _read_env_files().get(key, default)

def save_install_mode(mode):
    """Kurulum modunu .env.global.local dosyasına kalıcı olarak yazar."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_env_path = os.path.join(base_dir, "services", ".env.global.local")
    example_env_path = os.path.join(base_dir, "services", ".env.global.local.example")

    if not os.path.exists(local_env_path) and os.path.exists(example_env_path):
        import shutil
        try:
            shutil.copyfile(example_env_path, local_env_path)
        except OSError:
            pass

    lines = []
    mode_updated = False
    if os.path.exists(local_env_path):
        try:
            with open(local_env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            pass

    for i, line in enumerate(lines):
        if line.strip().startswith("ORION_INSTALL_MODE="):
            lines[i] = f"ORION_INSTALL_MODE={mode}\n"
            mode_updated = True
            break

    if not mode_updated:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"ORION_INSTALL_MODE={mode}\n")

    try:
        with open(local_env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as e:
        print(f"[!] Warning: Could not save installation mode: {e}")

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
    try:
        uv_check = subprocess.run(["uv", "--version"], capture_output=True)
        has_uv = (uv_check.returncode == 0)
    except FileNotFoundError:
        has_uv = False

    if has_uv:
        subprocess.run(["uv", "venv"], cwd=target_dir, check=True)
    else:
        base_python = getattr(sys, '_base_executable', sys.executable)
        subprocess.run([base_python, "-m", "venv", ".venv"], cwd=target_dir, check=True)

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
        if alias == 'installer':
            print(f"[LIFECYCLE manager pid={os.getpid()}] KeyboardInterrupt received; installer pid={proc.pid if proc else 'not started'}", flush=True)
        print("\n[!] Durduruldu.")
        if proc:
            try:
                # Ctrl+C has reached the console child; allow orderly exit.
                # Popen retains the exact OS process handle for the fallback.
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
    except Exception as e:
        print(f"\n[X] Kritik Hata: {e}")

def get_mode_from_args(args):
    # 1. Öncelik: CLI argümanları (örn: orion start --docker)
    if args:
        normalized_args = [arg.strip().lower() for arg in args]
        if "--local" in normalized_args or "local" in normalized_args:
            return "local"
        elif "--docker" in normalized_args or "docker" in normalized_args:
            return "docker"

    # 2. Öncelik: Kurulumda kaydedilen mod (ORION_INSTALL_MODE in .env.global.local)
    saved_mode = load_env_var("ORION_INSTALL_MODE")
    if saved_mode in ("local", "docker"):
        return saved_mode

    # 3. Geriye dönük uyumluluk: eski kurulumlardan kalan .local_db varsa local
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.exists(os.path.join(base_dir, ".local_db")):
        return "local"

    # Nihai varsayılan
    return "docker"

def find_orionrouter_script():
    from services.shared.router_installation import find_router_script
    return find_router_script()


# --- POSTGRESQL YARDIMCILARI (stale PID / lock sorunu için) ---
def get_pg_status(pg_ctl, pg_data):
    """PostgreSQL'in GERÇEKTEN çalışıp çalışmadığını döner (pg_ctl status ile)."""
    try:
        result = subprocess.run([pg_ctl, "status", "-D", pg_data], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False

def clean_stale_postmaster_lock(pg_data):
    # Only PostgreSQL may remove its stale postmaster lock after validation.
    return False

def start_postgres_blocking(pg_ctl, pg_data, show_terminals, result_holder, postgres_port):
    from services.shared.native_services import hub_database
    try:
        hub_database(os.path.dirname(os.path.abspath(__file__)), load_env_var).ensure()
        result_holder["success"] = True
    except Exception as error:
        result_holder.update(success=False, error=str(error))

# --- KOMUT İŞLEYİCİLER ---
def cmd_help(args=None):
    print("\n=== ORION AI ASSISTANT MANAGER ===")
    print("Usage: python manager.py <command> [options]")
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
    save_install_mode(mode)
    os.environ["ORION_INSTALL_MODE"] = mode
    if mode == "docker":
        ensure_docker_running()
    cmd_run("installer", [])

def handle_setup(args):
    mode = get_mode_from_args(args)
    save_install_mode(mode)
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

    if mode == "local" and os.environ.get('ORION_SKIP_GLOBAL_REGISTRATION') != '1':

        print("[*] Setting up 'orion' global CLI command...")
        try:
            if os.name == 'nt':
                cmd_path = os.path.join(base_dir, "orion.cmd")
                with open(cmd_path, "w") as f:
                    f.write('@echo off\npython "%~dp0manager.py" %*\n')
                # Add to PATH using PowerShell
                ps_cmd = f"$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User'); if ($userPath -notlike '*{base_dir}*') {{ [Environment]::SetEnvironmentVariable('PATH', $userPath + ';{base_dir}', 'User') }}"
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd])
            else:
                sh_path = os.path.join(base_dir, "orion")
                with open(sh_path, "w") as f:
                    f.write('#!/usr/bin/env bash\npython3 "$(dirname "$0")/manager.py" "$@"\n')
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
    installer_python = setup_environment("installer")
    if mode == 'local' and os.environ.get('ORION_BOOTSTRAP_REGISTER') == '1':
        # The installer environment now has the lock dependencies. Registration
        # runs only after successful setup and uses this source's known module.
        subprocess.run([installer_python, '-m', 'services.shared.bootstrap_registration'],
                       cwd=base_dir, check=True)
    print(f"\n[OK] Setup completed successfully! You can now use 'python manager.py start {mode}' or other commands.")

def _owns_compose_project(project, working_dir):
    canonical = lambda path: os.path.normcase(os.path.realpath(path))
    from installer.app import config
    if not working_dir:
        return False
    env = config._load_global_env()
    return any(canonical(working_dir) == canonical(directory) and
        project == env.get(manifest.get('category', 'misc').upper() + '_PROJECT_NAME',
                           'orion-' + manifest.get('category', 'misc'))
        for manifest, path in config.all_manifests()
        for directory in [os.path.dirname(path)])


def handle_start(args):
    mode = get_mode_from_args(args)
    show_terminals = "--show-terminals" in args

    if mode == "local":
        from services.shared.native_services import start_hub, start_router
        base_dir = os.path.dirname(os.path.abspath(__file__))
        _, router = find_orionrouter_script()
        if router:
            start_router(router)
        start_hub(base_dir)
        print("[OK] Local services verified ready.")
    else:
        print(f"\nStarting Orion AI Assistant in {mode.upper()} mode...")
        if not shutil.which("docker"):
            print("[!] Docker is not installed.")
            return
        ensure_docker_running()
        
        print("[*] Starting Orion services...")
        res = subprocess.run(["docker", "ps", "-a", "--format", '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}'], capture_output=True, text=True)
        to_start = []
        skipped = []
        for line in res.stdout.strip().splitlines():
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) < 3:
                    continue
                cname, proj, working_dir = parts
                if not _owns_compose_project(proj, working_dir):
                    continue
                # Check .disabled / .enabled markers (same logic as web app)
                if working_dir:
                    disabled_path = os.path.join(working_dir, ".disabled")
                    enabled_path = os.path.join(working_dir, ".enabled")
                    manifest_path = os.path.join(working_dir, "manifest.json")
                    is_manifest_disabled = False
                    if os.path.exists(manifest_path):
                        try:
                            with open(manifest_path, "r", encoding="utf-8") as mf:
                                m_data = json.load(mf)
                            is_manifest_disabled = m_data.get("status") == "disabled"
                        except Exception:
                            pass
                    if is_manifest_disabled:
                        is_active = os.path.exists(enabled_path)
                    else:
                        is_active = not os.path.exists(disabled_path)
                    if not is_active:
                        skipped.append(cname)
                        continue
                to_start.append(cname)
                    
        if to_start:
            subprocess.run(["docker", "start"] + to_start)
            print(f"[OK] {len(to_start)} containers started.")
        else:
            print("[i] No installed Orion containers found to start.")
        if skipped:
            print(f"[i] {len(skipped)} disabled service(s) skipped: {', '.join(skipped)}")


def handle_stop(args):
    mode = get_mode_from_args(args)

    print(f"\nStopping Orion AI Assistant in {mode.upper()} mode...")

    if mode == "local":
        from services.shared.native_services import stop_hub, stop_router
        base_dir = os.path.dirname(os.path.abspath(__file__))
        stop_hub(base_dir)
        _, router = find_orionrouter_script()
        if router:
            stop_router(router)
        print("[OK] Local services verified stopped.")
    else:
        print("[*] Stopping Orion services...")
        res = subprocess.run(["docker", "ps", "--format", '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}'], capture_output=True, text=True)
        containers = []
        for line in res.stdout.strip().splitlines():
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                cname, proj, working_dir = parts
                if _owns_compose_project(proj, working_dir):
                    containers.append(cname)
                    
        if containers:
            subprocess.run(["docker", "stop"] + containers)
            print(f"[OK] {len(containers)} containers stopped.")
        else:
            print("[i] No running Orion containers found to stop.")

def handle_status(args):
    mode = get_mode_from_args(args)
    print(f"\n[*] Checking Orion AI Assistant status ({mode.upper()} mode)...\n")
    if mode == "local":
        from services.shared.native_services import component_status, router_root
        root = os.path.dirname(os.path.abspath(__file__))
        print(f"  Orion Hub: {component_status(root, 'hub')}")
        _, script = find_orionrouter_script()
        router_state = component_status(router_root(script), 'router-prod') if script else 'unverified'
        print(f"  Orion Router: {router_state}")
        for name, relative in [('Orion LLM', 'llm/llama-cpp'),
                               ('Orion Embedding', 'embedding/llama-cpp-embed'), ('Orion TTS', 'tts')]:
            print(f"  {name}: {component_status(os.path.join(root, 'services', relative), 'service')}")
    else:
        print("--- DOCKER SERVICES ---")
        res = subprocess.run(["docker", "ps", "-a", "--format", '{{.Names}}|{{.State}}|{{.Label "com.docker.compose.project"}}'], capture_output=True, text=True)
        containers = {}
        for line in res.stdout.strip().splitlines():
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) == 3:
                    cname, state, proj = parts
                    if proj.startswith("orion-") or cname.startswith("orion-"):
                        containers[cname] = state.upper()
        
        if not containers:
            print("  No installed Orion containers found.")
        else:
            for name, state in containers.items():
                if state == "RUNNING":
                    color = "\033[92m"
                elif state == "EXITED":
                    color = "\033[91m"
                else:
                    color = "\033[93m"
                print(f"  {name.ljust(20)} : {color}{state}\033[0m")
        print("\n")

def safe_print(text):
    with log_lock:
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            sys.stdout.write(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8', errors='replace'))
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
        if not filters:
            print("[!] Please specify a service to view logs in Docker mode (e.g. --hub, --router, --llm, --embedding, --tts)")
            return
            
        base_dir = os.path.dirname(os.path.abspath(__file__))
        services_map = {
            "HUB": "hub",
            "API": "hub",
            "WORKER": "hub",
            "REDIS": "hub",
            "ROUTER": "router",
            "LLM": "llm/llama-cpp",
            "EMBEDDING": "embedding/llama-cpp-embed",
            "TTS": "tts"
        }
        
        target_dirs = set()
        for f in filters:
            if f in services_map:
                target_dirs.add(os.path.join(base_dir, "services", services_map[f]))
                
        if not target_dirs:
            print("[i] No matching services found for the given filters.")
            return
            
        if len(target_dirs) > 1:
            print("[!] Docker mode currently supports viewing logs for one service group at a time. Using the first one.")
            
        target_dir = list(target_dirs)[0]
        manifest_path = os.path.join(target_dir, "manifest.json")
        compose_files = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    m_data = json.load(mf)
                    compose_files = list(m_data.get("compose_files", {}).values())
            except:
                pass
                
        if not compose_files:
            compose_files = ["docker-compose.yml"]
            
        cmd = ["docker-compose"]
        for cf in set(compose_files):
            if cf and os.path.exists(os.path.join(target_dir, cf)):
                cmd.extend(["-f", cf])
        cmd.extend(["logs", "-f"])
        
        print(f"\n[*] Tailing Docker logs in {os.path.basename(target_dir)}... (Press CTRL+C to exit)\n" + "-"*50)
        try:
            subprocess.run(cmd, cwd=target_dir)
        except KeyboardInterrupt:
            print("\n[OK] Stopped streaming logs.")

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
        print("Geçerli komutları görmek için: python manager.py help")
