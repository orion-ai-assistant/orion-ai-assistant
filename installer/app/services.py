import os
import re
import subprocess
import logging
from . import config
from .core import i18n
from .utils import docker_utils, system_utils, model_resolver

# ==========================================
# AYARLAR VE SABİTLER
# ==========================================
_ENV_KEY_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?:[^}]*)\}")
_CONFLICT_PATTERN = re.compile(r'container name "(/[^"]+)" is already in use', re.IGNORECASE)

MANAGED_HOSTS = {"REDIS_HOST", "POSTGRES_HOST", "HUB_HOST", "WORKER_HOST", "API_2_HOST", "NGINX_HOST", "EMBED_HOST"}
GPU_VENDORS = {"nvidia", "amd"}

# ==========================================
# YARDIMCI FONKSİYONLAR
# ==========================================
def _get_context(service_id: str) -> tuple[dict | None, str, dict]:
    manifest, m_path = config.find_manifest(service_id)
    if not manifest: 
        return None, "", {}
    return manifest, os.path.dirname(m_path), config._load_global_env()

def _validate_params(params: dict, specs: dict) -> tuple[dict | None, dict | None]:
    validated = {}
    for p_id, val in params.items():
        if p_id not in specs:
            validated[p_id] = val
            continue
        
        spec = specs[p_id]
        is_obj = isinstance(spec, dict)
        default_val = spec.get("default") if is_obj else spec
        
        # Orijinal bool ve default_val algılaması geri getirildi
        p_type = spec.get("type") if is_obj else ("int" if isinstance(default_val, int) else "bool" if isinstance(default_val, bool) else "text")
        label = spec.get("label", p_id) if is_obj else p_id
        
        if p_type in ["int", "number"] or isinstance(default_val, (int, float)):
            try:
                num_val = float(val)
                if p_type == "int" or isinstance(default_val, int):
                    num_val = int(num_val)
                    
                p_min, p_max = (spec.get("min", 0), spec.get("max")) if is_obj else (0, None)
                
                if num_val < p_min or (p_max is not None and num_val > p_max):
                    return None, {"status": "error", "message": i18n.t("MSG_PARAM_RANGE_ERROR", label, p_min, p_max)}
                validated[p_id] = num_val
            except (ValueError, TypeError):
                return None, {"status": "error", "message": i18n.t("MSG_PARAM_MUST_BE_NUM", label)}
        else:
            validated[p_id] = val

    return {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in validated.items()}, None

def toggle_autostart(service_id: str) -> dict:
    manifest, s_dir, _ = _get_context(service_id)
    if not manifest: return {"status": "error", "message": i18n.t("MSG_SERVICE_NOT_FOUND")}
    
    is_manifest_disabled = manifest.get("status") == "disabled"
    disabled_path = os.path.join(s_dir, ".disabled")
    enabled_path = os.path.join(s_dir, ".enabled")
    
    if is_manifest_disabled:
        if os.path.exists(enabled_path):
            try:
                os.remove(enabled_path)
                return {"status": "success", "message": i18n.t("MSG_SERVICE_DISABLED"), "autostart": False}
            except OSError:
                return {"status": "error", "message": i18n.t("MSG_FILE_CREATE_FAILED")}
        else:
            try:
                with open(enabled_path, "w") as f: pass
                return {"status": "success", "message": i18n.t("MSG_SERVICE_AUTOSTART_ADDED"), "autostart": True}
            except OSError:
                return {"status": "error", "message": i18n.t("MSG_FILE_CREATE_FAILED")}
    else:
        if os.path.exists(disabled_path):
            try:
                os.remove(disabled_path)
                return {"status": "success", "message": i18n.t("MSG_SERVICE_AUTOSTART_ADDED"), "autostart": True}
            except OSError:
                return {"status": "error", "message": i18n.t("MSG_FILE_CREATE_FAILED")}
        else:
            try:
                with open(disabled_path, "w") as f: pass
                return {"status": "success", "message": i18n.t("MSG_SERVICE_DISABLED"), "autostart": False}
            except OSError:
                return {"status": "error", "message": i18n.t("MSG_FILE_CREATE_FAILED")}

def get_services() -> list[dict]:
    containers = docker_utils.get_running_containers()
    services = []
    for data, m_path in config.all_manifests():
        c_name = data.get("container_name", "")
        s_dir = os.path.dirname(m_path)
        
        is_manifest_disabled = data.get("status") == "disabled"
        if is_manifest_disabled:
            autostart = os.path.exists(os.path.join(s_dir, ".enabled"))
        else:
            autostart = not os.path.exists(os.path.join(s_dir, ".disabled"))
        
        install_mode = os.environ.get("ORION_INSTALL_MODE", "docker")
        is_native = data.get("id") in ["orion-hub", "orion-router"]

        if install_mode == "local" and is_native:
            if data.get("id") == "orion-router":
                plat, r_path = find_orionrouter_script()
                is_installed = r_path is not None
            else:
                setup_dir = s_dir
                venv_path = os.path.join(setup_dir, ".venv")
                is_installed = os.path.exists(venv_path)
            
            is_running = False
            if os.name == 'nt':
                try:
                    out = subprocess.run(["wmic", "process", "where", "name='python.exe'", "get", "commandline"], capture_output=True, text=True).stdout
                    if data.get("id") == "orion-router":
                        if "orion.py prod" in out:
                            is_running = True
                    else:
                        if "run_local.py" in out or "orion.api.main" in out or "orion.worker.main" in out:
                            is_running = True
                except Exception:
                    pass
            else:
                try:
                    if data.get("id") == "orion-router":
                        out = subprocess.run(["pgrep", "-f", "orion.py prod"], capture_output=True, text=True).stdout
                    else:
                        out = subprocess.run(["pgrep", "-f", "run_local.py|orion.api.main|orion.worker.main"], capture_output=True, text=True).stdout
                    if out.strip():
                        is_running = True
                except Exception:
                    pass

            data.update({
                "is_installed": is_installed,
                "is_running": is_running,
                "is_installing": data["id"] in config.INSTALLING_SERVICES,
                "autostart": autostart,
                "install_error": config.INSTALL_ERRORS.get(data["id"])
            })
        else:
            data.update({
                "is_installed": c_name in containers,
                "is_running": containers.get(c_name, False),
                "is_installing": data["id"] in config.INSTALLING_SERVICES,
                "autostart": autostart,
                "install_error": config.INSTALL_ERRORS.get(data["id"])
            })
        services.append(data)
        
    # Sıralama mantığı: Hub/Core -> Order -> Name
    return sorted(services, key=lambda s: (0 if s.get("id") == "orion-hub" or s.get("category") in {"core", "hub", "router"} else 1, int(s.get("order", 50)), s.get("name", "")))

def prepare_install(service_id: str, hardware: str, env_id: str, model_file: str, mmproj_file: str, params: dict):
    if service_id in config.INSTALLING_SERVICES: 
        return {"status": "error", "message": "Bu servis zaten kuruluyor."}, "", "", {}, set()
    
    if service_id in config.INSTALL_ERRORS:
        del config.INSTALL_ERRORS[service_id]
    
    # Yerel ayar dosyasini ilk kurulum aninda kopyala
    global_env_path = os.path.join(config.SERVICES_DIR, ".env.global.local")
    global_env_example_path = os.path.join(config.SERVICES_DIR, ".env.global.local.example")
    if not os.path.exists(global_env_path) and os.path.exists(global_env_example_path):
        import shutil
        try:
            shutil.copyfile(global_env_example_path, global_env_path)
        except OSError:
            pass

    manifest, s_dir, g_vars = _get_context(service_id)
    if not manifest: 
        return {"status": "error", "message": i18n.t("MSG_MANIFEST_NOT_FOUND")}, "", "", {}, set()

    is_core = manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}
    if not model_file and not is_core: 
        return {"status": "error", "message": i18n.t("MSG_SELECT_MODEL_FILE")}, "", "", {}, set()

    install_mode = os.environ.get("ORION_INSTALL_MODE", "docker")
    if install_mode == "local" and manifest.get("id") in ["orion-hub", "orion-router"]:
        return None, s_dir, "local", {}, set()

    hw = "cpu" if is_core else (hardware or g_vars.get("DETECTED_GPU_VENDOR", "cpu"))
    compose_file = manifest.get("compose_files", {}).get(hw)
    if not compose_file: 
        return {"status": "error", "message": i18n.t("MSG_HW_NOT_SUPPORTED", hw)}, "", "", {}, set()

    # Orijinal Environment Fallback
    envs = manifest.get("supported_environments", [])
    if env_id:
        selected_env_id = env_id
    elif envs:
        matched_env = next((e for e in envs if e.get("hardware") == hw), None)
        selected_env_id = matched_env["id"] if matched_env else envs[0]["id"]
    else:
        selected_env_id = None

    c_env = next((e for e in envs if e.get("id") == selected_env_id), {})
    catalog = manifest.get("models_catalog", [])
    sel_mod = next((m for m in catalog if model_file in (m.get("folder"), m.get("id"), m.get("file_name"))), {})
    model_env = sel_mod.get("env", {})

    f_params, err = _validate_params(params, manifest.get("parameters", {}))
    if err: 
        return err, "", "", {}, set()

    from .core.models import VALID_EXTENSIONS 
    m_dir = os.path.join(s_dir, manifest.get("models_path", "models"))
    model_file = model_resolver._resolve_model_path(service_id, m_dir, model_file, VALID_EXTENSIONS)
    
    # Akıllı MMPROJ Otomatik Algılama: 
    # Sadece LLM kategorisindeki servisler için vizyon dosyası seçilmediyse otomatik bul.
    if not mmproj_file and "/" in model_file and manifest.get("category") == "llm":
        model_dir_part = os.path.dirname(os.path.join(m_dir, model_file))
        if os.path.isdir(model_dir_part):
            # Akıllı mmproj bulucu (Öncelik: Vision > Genel > Audio)
            projectors = [f for f in os.listdir(model_dir_part) if f.lower().endswith(".gguf") and "mmproj" in f.lower()]
            
            auto_mmproj = None
            if projectors:
                # Vision içeren varsa onu al, yoksa ilk bulduğunu al
                auto_mmproj = next((p for p in projectors if "vision" in p.lower()), projectors[0])
            
            if auto_mmproj:
                rel_dir = os.path.dirname(model_file)
                mmproj_path = os.path.join(rel_dir, auto_mmproj).replace("\\", "/")
                mmproj_args = f"--mmproj /app/models/{mmproj_path}"
                print(f"[DEBUG] Multimodal projector detected: {mmproj_path}")

    mmproj_file = model_resolver._resolve_model_path(service_id, m_dir, mmproj_file, (".gguf",))

    enable_vision = False
    enable_audio = False

    mmproj_args = ""

    # Embedding servisleri için karmaşık mmproj tespitini kaldırıyoruz.
    # Sadece seçilen ana model üzerinden devam edilecek.
    pass

    # Genel Fallback: Eğer yukarıdaki özel mantıklar çalışmadıysa ama bir mmproj_file bulunduysa
    if mmproj_file and not mmproj_args:
        mmproj_args = f"--mmproj /app/models/{mmproj_file}"

    # Multimodal modeller için ekstra argüman zorlamasını kaldırıyoruz, 
    # llama.cpp'nin kendi varsayılanlarını veya kullanıcının EXTRA_ARGS değerini kullanmasına izin veriyoruz.
    pass



    category_upper = manifest.get("category", "misc").upper()
    project_name_key = f"{category_upper}_PROJECT_NAME"
    g_vars["COMPOSE_PROJECT_NAME"] = g_vars.get(project_name_key, f"orion-{manifest.get('category', 'misc')}")

    port_env = manifest.get("port_env")
    if port_env and port_env in g_vars: 
        g_vars["APP_PORT"] = g_vars[port_env]

    # --- GLOBAL SECURITY AUTO-CONFIG ---
    added_global_vars = {}
    
    # 1. ADMIN_API_KEY for Hub API
    has_api_key = g_vars.get("ADMIN_API_KEY")
    if not has_api_key or not has_api_key.strip():
        new_api_key = "orionadmin"
        g_vars["ADMIN_API_KEY"] = new_api_key
        added_global_vars["ADMIN_API_KEY"] = new_api_key

    # 2. Router-specific keys (only generated if installing orion-router)
    if service_id == "orion-router":
        has_secret = g_vars.get("ADMIN_SECRET")
        if not has_secret or not has_secret.strip():
            g_vars["ADMIN_SECRET"] = "orion"
            added_global_vars["ADMIN_SECRET"] = "orion"

    # 3. Auto-detect CLI_LANG
    has_cli_lang = g_vars.get("CLI_LANG")
    if not has_cli_lang or not has_cli_lang.strip():
        import locale
        try:
            sys_lang = locale.getdefaultlocale()[0]
            lang_code = sys_lang.split("_")[0] if sys_lang else "en"
        except Exception:
            lang_code = "en"
        g_vars["CLI_LANG"] = lang_code
        added_global_vars["CLI_LANG"] = lang_code

    if added_global_vars:
        global_env_path = os.path.join(config.SERVICES_DIR, ".env.global.local")
        try:
            exists = os.path.exists(global_env_path)
            with open(global_env_path, "a", encoding="utf-8") as gf:
                if not exists:
                    gf.write("# Local overrides and auto-generated keys for Orion Services\n")
                gf.write(f"\n# Auto-generated by Orion Installer\n")
                for k, v in added_global_vars.items():
                    gf.write(f"{k}={v}\n")
        except OSError:
            pass
    # -----------------------------------------

    # EXTRA_ARGS Tahmini:
    # Katalogda özel bir ayar yoksa ve kategori embedding ise default 'mean' kullan.
    extra_args = model_env.get("EXTRA_ARGS", "")
    # if not extra_args:
    #     if manifest.get("category") == "embedding":
    #         extra_args = "--pooling mean"
    #     else:
    #         extra_args = ""

    build_env = {
        **manifest.get("env_defaults", {}),
        **manifest.get("env", {}), 
        **g_vars, 
        **c_env.get("env", {}),
        **sel_mod.get("build_args", {}), 
        **{k: v for k, v in model_env.items() if k != "EXTRA_ARGS"},
        "BASE_IMAGE": c_env.get("base_image", ""), 
        "MODEL_FILE": model_file, 
        "MMPROJ_FILE": mmproj_file,
        "MMPROJ_ARGS": mmproj_args,
        "EXTRA_ARGS": extra_args,
        "GPU_COUNT": "1" if hw in GPU_VENDORS else "0", 
        "COMPOSE_FILE": compose_file,
        **f_params,
    }
    
    # Tüm değerleri stringe zorla (Docker/subprocess güvenliği için)
    build_env = {k: str(v) for k, v in build_env.items() if v is not None}

    try: 
        with open(os.path.join(s_dir, compose_file), encoding="utf-8") as f:
            keys = set(_ENV_KEY_PATTERN.findall(f.read()))
    except OSError: 
        keys = set()
    
    # Python geriye dönük uyumluluğu korumak için klasik update kullanıldı (dict_keys merge hatası önlemi)
    keys.update(manifest.get("parameters", {}).keys())
    keys.update(manifest.get("env_defaults", {}).keys())
    keys.update(manifest.get("env", {}).keys())
    keys.update(k for k in model_env.keys() if k != "EXTRA_ARGS")
    keys.update({"COMPOSE_PROJECT_NAME", "BASE_IMAGE", "MODEL_FILE", "MMPROJ_FILE", "MMPROJ_ARGS", "EXTRA_ARGS", "GPU_COUNT", "REBUILD_IMAGE", "COMPOSE_FILE"})

    config.INSTALLING_SERVICES.add(service_id)
    return None, s_dir, compose_file, build_env, keys

def stop_service(service_id: str) -> bool:
    manifest, s_dir, _ = _get_context(service_id)
    if not manifest: return False

    install_mode = os.environ.get("ORION_INSTALL_MODE", "docker")
    if install_mode == "local" and manifest.get("id") in ["orion-hub", "orion-router"]:
        is_router = manifest.get("id") == "orion-router"
        if is_router:
            if os.name == 'nt':
                subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "orionrouter" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "orionrouter"], capture_output=True)
            return True
        else:
            match_str = "run_local.py|orion.api.main|orion.worker.main"
            if os.name == 'nt':
                subprocess.run(["powershell", "-c", f'Get-WmiObject Win32_Process | Where-Object {{ $_.CommandLine -match "{match_str}" }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}'], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "run_local.py"], capture_output=True)
                subprocess.run(["pkill", "-f", "orion.api.main"], capture_output=True)
                subprocess.run(["pkill", "-f", "orion.worker.main"], capture_output=True)
            return True

    g_vars = config._load_global_env()
    category_upper = manifest.get("category", "misc").upper()
    project_name_key = f"{category_upper}_PROJECT_NAME"
    g_vars["COMPOSE_PROJECT_NAME"] = g_vars.get(project_name_key, f"orion-{manifest.get('category', 'misc')}")

    if manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}:
        return docker_utils._run_compose(list(manifest.get("compose_files", {}).values()), "stop", s_dir, g_vars)
    
    host_env = manifest.get("host_env")
    c_name = g_vars.get(host_env) if host_env else manifest.get("container_name")
    if c_name: subprocess.run(["docker", "stop", c_name], capture_output=True)
    return bool(c_name)

def remove_service(service_id: str) -> bool:
    manifest, s_dir, g_vars = _get_context(service_id)
    if not manifest: return False
    
    category_upper = manifest.get("category", "misc").upper()
    project_name_key = f"{category_upper}_PROJECT_NAME"
    g_vars["COMPOSE_PROJECT_NAME"] = g_vars.get(project_name_key, f"orion-{manifest.get('category', 'misc')}")

    return docker_utils._run_compose(list(manifest.get("compose_files", {}).values()), "down", s_dir, g_vars)

def remove_image(service_id: str) -> bool:
    manifest, s_dir, g_vars = _get_context(service_id)
    if not manifest: return False

    # First delete the container
    remove_service(service_id)

    hw = g_vars.get("DETECTED_GPU_VENDOR", "cpu")
    if manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}:
        hw = "cpu"
        
    compose_file = manifest.get("compose_files", {}).get(hw)
    if not compose_file: return False
    
    image_name = docker_utils._get_compose_image(compose_file, s_dir)
    return docker_utils._remove_image(image_name)

def run_local_installation(service_id: str, service_dir: str):
    import sys
    try:
        config.INSTALLING_SERVICES.add(service_id)
        
        # Native Router setup support
        if service_id == "orion-router":
            print("[*] Installing Orion Router globally...")
            if os.name == 'nt':
                subprocess.run(["powershell", "-c", "Invoke-Command -ScriptBlock ([scriptblock]::Create((irm https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.ps1))) -ArgumentList 'local', $true"], check=True)
            else:
                subprocess.run(["bash", "-c", "curl -sL https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.sh | bash -s local --no-start"], check=True)
            return
            
        setup_dir = service_dir
            
        venv_dir = os.path.join(setup_dir, ".venv")
        py_exe = os.path.join(venv_dir, "Scripts", "python.exe") if os.name == 'nt' else os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe") if os.name == 'nt' else os.path.join(venv_dir, "bin", "pip")
        
        if not os.path.exists(venv_dir):
            subprocess.run([sys.executable, "-m", "venv", ".venv"], cwd=setup_dir, check=True)
            
        subprocess.run([py_exe, "-m", "pip", "install", "--upgrade", "pip"], cwd=setup_dir, check=True)
        subprocess.run([pip_exe, "install", "-e", "."], cwd=setup_dir, check=True)
        
    except Exception as e:
        config.INSTALL_ERRORS[service_id] = f"Yerel Kurulum Hatasi: {str(e)}"
    finally:
        config.INSTALLING_SERVICES.discard(service_id)

def run_installation(service_id: str, service_dir: str, compose_file: str, build_env: dict, env_file_keys: set[str]):
    try:
        # Kurulumda `.env.global` ve `.env.global.local` referansı
        g_vars_file = {
            **system_utils._read_env(os.path.join(config.SERVICES_DIR, ".env.global")),
            **system_utils._read_env(os.path.join(config.SERVICES_DIR, ".env.global.local"))
        }
        
        with open(os.path.join(service_dir, ".env"), "w", encoding="utf-8") as f:
            for k, v in build_env.items():
                if k not in env_file_keys:
                    continue
                # Explicit empty string & None check (0 ve False değerlerini kaybetmemek için)
                if v is None or str(v).strip() == "":
                    continue
                if k in g_vars_file and str(g_vars_file[k]) == str(v):
                    continue
                f.write(f"{k}={v}\n")

        # Network oluşturmak için runtime vars çağrısı
        runtime_g_vars = config._load_global_env()
        subprocess.run(["docker", "network", "create", runtime_g_vars.get("ORION_NETWORK", "orion-network")], stderr=subprocess.DEVNULL)
        
        # Adım 1: Şimdi servisi oluştur (başlatmadan)
        force_rebuild = str(build_env.get("REBUILD_IMAGE", "false")).lower() == "true"
        cmd = ["docker-compose", "-f", compose_file, "up", "--no-start"]
        if force_rebuild:
            cmd.append("--build")
            
        process = subprocess.Popen(cmd, cwd=service_dir, env={**os.environ, **build_env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        
        disabled_path = os.path.join(service_dir, ".disabled")
        enabled_path = os.path.join(service_dir, ".enabled")
        try:
            if os.path.exists(disabled_path):
                os.remove(disabled_path)
            
            # Read manifest to check if we need to add .enabled
            manifest_path = os.path.join(service_dir, "manifest.json")
            if os.path.exists(manifest_path):
                import json
                with open(manifest_path, "r", encoding="utf-8") as f:
                    mf = json.load(f)
                if mf.get("status") == "disabled":
                    with open(enabled_path, "w") as f: pass
        except Exception:
            pass
        
        out_lines = []
        for line in process.stdout:
            print(line, end="", flush=True)
            out_lines.append(line)
            
        process.wait()
        out = "".join(out_lines)
        
        if process.returncode != 0:
            conflicts = {n.lstrip("/") for n in _CONFLICT_PATTERN.findall(out)} 
            managed = {str(build_env.get(k, "")).strip() for k in MANAGED_HOSTS if build_env.get(k)}
            removable_conflicts = sorted(conflicts & managed)
            
            if removable_conflicts:
                logging.warning("Install conflict (%s). Removing %s and retrying...", service_id, removable_conflicts)
                for name in removable_conflicts: 
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                
                retry_process = subprocess.Popen(cmd, cwd=service_dir, env={**os.environ, **build_env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
                for line in retry_process.stdout:
                    print(line, end="", flush=True)
                retry_process.wait()
            else:
                err_msg = out.strip()
                if "error during connect" in err_msg and ("The system cannot find the file specified" in err_msg or "Is the docker daemon running" in err_msg):
                    err_msg = i18n.t("ERROR_DOCKER_PAUSED")
                elif "Docker Desktop is manually paused" in err_msg:
                    err_msg = i18n.t("ERROR_DOCKER_PAUSED")
                elif "failed to do request: Head" in err_msg or "context deadline exceeded" in err_msg or "connectex:" in err_msg:
                    err_msg = i18n.t("ERROR_DOCKER_HUB")
                else:
                    short_err = err_msg[-200:] if len(err_msg) > 200 else err_msg
                    err_msg = i18n.t("ERROR_INSTALL_PREFIX", short_err)
                config.INSTALL_ERRORS[service_id] = err_msg
                print(f"[INSTALL ERROR] {service_id}: {out}")

    except Exception as e:
        config.INSTALL_ERRORS[service_id] = f"Beklenmeyen Hata: {str(e)}"
        print(f"[INSTALL ERROR] {service_id}: {e}")
    finally:
        config.INSTALLING_SERVICES.discard(service_id)

def find_orionrouter_script():
    import os
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
            
    import shutil
    cmd = shutil.which("orionrouter")
    if cmd:
        return "cmd", cmd
    return None, None



def start_active_services() -> dict:
    import subprocess
    active_services = []
    g_vars = config._load_global_env()
    
    for data, m_path in config.all_manifests():
        s_dir = os.path.dirname(m_path)
        env_path = os.path.join(s_dir, ".env")
        disabled_path = os.path.join(s_dir, ".disabled")
        enabled_path = os.path.join(s_dir, ".enabled")
        
        is_manifest_disabled = data.get("status") == "disabled"
        if is_manifest_disabled:
            is_active = os.path.exists(env_path) and os.path.exists(enabled_path)
        else:
            is_active = os.path.exists(env_path) and not os.path.exists(disabled_path)
            
        if is_active:
            active_services.append((data, s_dir))
            
    active_services.sort(key=lambda item: (
        0 if item[0].get("id") == "orion-hub" or item[0].get("category") in {"core", "hub", "router"} else 1,
        int(item[0].get("order", 50))
    ))
    
    started = []
    failed = []
    
    install_mode = os.environ.get("ORION_INSTALL_MODE", "docker")
    
    for manifest, s_dir in active_services:
        try:
            if install_mode == "local":
                import sys
                base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                py_exe = sys.executable
                print("[SYSTEM] Starting all local services via 'orion.py start'")
                cflags = 0x08000000 if os.name == 'nt' else 0 # CREATE_NO_WINDOW
                subprocess.Popen([py_exe, "orion.py", "start"], cwd=base_dir, creationflags=cflags)
                return {"status": "success", "started": ["Orion Local Stack"], "failed": []}
                
            compose_file = None
            env_path = os.path.join(s_dir, ".env")
            if os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as ef:
                    for line in ef:
                        line = line.strip()
                        if line.startswith("COMPOSE_FILE="):
                            compose_file = line.split("=", 1)[1].strip()
                            break

            if not compose_file:
                hw = g_vars.get("DETECTED_GPU_VENDOR", "cpu")
                is_core = manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}
                if is_core:
                    hw = "cpu"
                    
                compose_file = manifest.get("compose_files", {}).get(hw)
                if not compose_file:
                    compose_files = manifest.get("compose_files", {})
                    if compose_files:
                        hw = list(compose_files.keys())[0]
                        compose_file = compose_files[hw]
            
            if not compose_file:
                continue
                
            category_upper = manifest.get("category", "misc").upper()
            project_name_key = f"{category_upper}_PROJECT_NAME"
            project_name = g_vars.get(project_name_key, f"orion-{manifest.get('category', 'misc')}")
            
            subprocess.run(["docker", "network", "create", g_vars.get("ORION_NETWORK", "orion-network")], stderr=subprocess.DEVNULL)
            
            compose_env = {
                **os.environ,
                **g_vars,
                "COMPOSE_PROJECT_NAME": project_name
            }
            
            cmd = ["docker-compose", "-p", project_name, "-f", compose_file, "up", "-d"]
            print(f"[SYSTEM] Starting service {manifest['name']} in {s_dir} with command: {' '.join(cmd)}")
            
            res = subprocess.run(cmd, cwd=s_dir, env=compose_env, capture_output=True, text=True, errors="replace")
            
            if res.returncode == 0:
                started.append(manifest["name"])
            else:
                print(f"[SYSTEM] Failed to start {manifest['name']}: {res.stderr}")
                failed.append(manifest["name"])
                
        except Exception as e:
            print(f"[SYSTEM] Exception starting {manifest['name']}: {e}")
            failed.append(manifest["name"])
            
    return {"status": "success", "started": started, "failed": failed}