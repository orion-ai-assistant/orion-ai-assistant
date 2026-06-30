import os
import re
import subprocess
import logging
from . import config

# ==========================================
# AYARLAR VE SABİTLER
# ==========================================
_ENV_KEY_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?:[^}]*)\}")
_CONFLICT_PATTERN = re.compile(r'container name "(/[^"]+)" is already in use', re.IGNORECASE)
_IMAGE_PATTERN = re.compile(r"^\s*image:\s*(.+?)\s*$", re.IGNORECASE)

SERVICE_MOUNT_PREFIXES = {"embedding-infinity": "/app/.cache/"}
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

def _read_env(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {k.strip(): v.strip() for line in f if "=" in line and not line.strip().startswith("#") for k, v in [line.split("=", 1)]}
    except OSError:
        return {}

def _run_compose(files: list[str], action: str, cwd: str, env: dict) -> bool:
    success = False
    seen = set()
    for f in files:
        if not f or f in seen or not os.path.exists(os.path.join(cwd, f)):
            continue
        seen.add(f)
        res = subprocess.run(["docker-compose", "-f", f, action], cwd=cwd, env={**os.environ, **env}, capture_output=True, stdin=subprocess.DEVNULL)
        # Decode manually to avoid UnicodeDecodeError on Windows
        _ = res.stdout.decode("utf-8", errors="replace")

        success = True
    return success

def _get_compose_image(compose_file: str, cwd: str) -> str:
    try:
        with open(os.path.join(cwd, compose_file), "r", encoding="utf-8") as f:
            for line in f:
                m = _IMAGE_PATTERN.match(line)
                if m:
                    return m.group(1).strip()
    except OSError:
        return ""
    return ""

def _image_exists(image_name: str) -> bool:
    if not image_name:
        return False
    try:
        res = subprocess.run(["docker", "image", "inspect", image_name], capture_output=True, stdin=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False

async def check_and_resolve_port_conflict(port: int = 20128) -> dict:
    try:
        import psutil
    except ImportError:
        return {"status": "error", "message": "psutil kütüphanesi eksik, port taraması yapılamıyor."}
    
    found_pid = None
    process_name = ""
    
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == 'LISTEN':
                found_pid = conn.pid
                break
                
        if found_pid:
            try:
                proc = psutil.Process(found_pid)
                process_name = proc.name()
                proc.kill()
                proc.wait(timeout=3)
                return {"status": "success", "message": f"Port {port}'u kullanan {process_name} (PID: {found_pid}) basariyla kapatildi."}
            except psutil.AccessDenied:
                return {"status": "error", "message": f"Port {port}'u kullanan uygulama kapatilamadi (Erisim engellendi). Lutfen manuel kapatin."}
            except psutil.NoSuchProcess:
                return {"status": "success", "message": "Islem zaten kapanmis."}
            except Exception as e:
                return {"status": "error", "message": f"Kapatma hatasi: {e}"}
                
        return {"status": "success", "message": f"Port {port} temiz, cakisca yok."}
    except psutil.AccessDenied:
        # Some OS restrict net_connections
        return {"status": "warning", "message": "Port taramasi icin yetki yetersiz, lutfen portlarin bos oldugundan emin olun."}
    except Exception as e:
        return {"status": "error", "message": f"Port tarama hatasi: {e}"}

# ==========================================
# ANA FONKSİYONLAR
# ==========================================
def _resolve_model_path(service_id: str, full_dir: str, selected: str, valid_exts: tuple) -> str:
    prefix = SERVICE_MOUNT_PREFIXES.get(service_id)

    # Infinity Otomatik Model Tespiti
    if service_id == "embedding-infinity" and (not selected or selected == prefix):
        if os.path.isdir(full_dir):
            for d in sorted(os.listdir(full_dir)):
                cand = os.path.join(full_dir, d)
                if os.path.isdir(cand) and d.lower() != "huggingface":
                    if os.path.exists(os.path.join(cand, "config.json")) and os.path.exists(os.path.join(cand, "model.safetensors")):
                        return f"{prefix.rstrip('/')}/{d}" if prefix else d

    if not selected: 
        return ""
        
    full_path = os.path.join(full_dir, selected)
    
    # Path yoksa, patlamaması için direkt selected dön (FileNotError fix)
    if not os.path.exists(full_path):
        return selected

    if os.path.isdir(full_path):
        # Klasör seçildiğinde ana modeli bul (mmproj OLMAYAN ilk .gguf)
        match = next(
            (n for n in sorted(os.listdir(full_path)) 
             if n.lower().endswith(valid_exts) and "mmproj" not in n.lower() and "vision" not in n.lower()), 
            None
        )
        # Eğer yukarıdaki filtreyle bir şey bulunamazsa (belki sadece mmproj vardır), herhangi bir geçerli dosyayı al
        if not match:
            match = next((n for n in sorted(os.listdir(full_path)) if n.lower().endswith(valid_exts)), None)
            
        return os.path.join(selected, match).replace("\\", "/") if match else selected

    return selected

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
                    return None, {"status": "error", "message": f"{label} değeri {p_min}-{p_max} arası olmalıdır."}
                validated[p_id] = num_val
            except (ValueError, TypeError):
                return None, {"status": "error", "message": f"{label} geçerli bir sayı olmalıdır."}
        else:
            validated[p_id] = val

    return {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in validated.items()}, None

def get_services() -> list[dict]:
    containers = {}
    # Hata fırlatma ihtimaline karşı try/except (Docker daemon erişim sorunları için)
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, stdin=subprocess.DEVNULL)
        stdout = r.stdout.decode("utf-8", errors="replace")
        containers = {line.split("|")[0]: line.split("|")[1].startswith("Up") for line in stdout.splitlines() if "|" in line}

    except Exception:
        pass

    services = []
    for data, _ in config.all_manifests():
        c_name = data.get("container_name", "")
        data.update({
            "is_installed": c_name in containers,
            "is_running": containers.get(c_name, False),
            "is_installing": data["id"] in config.INSTALLING_SERVICES
        })
        services.append(data)
        
    # Sıralama mantığı: Hub/Core -> Order -> Name
    return sorted(services, key=lambda s: (0 if s.get("id") == "orion-hub" or s.get("category") in {"core", "hub", "router"} else 1, int(s.get("order", 50)), s.get("name", "")))

def prepare_install(service_id: str, hardware: str, env_id: str, model_file: str, mmproj_file: str, params: dict):
    if service_id in config.INSTALLING_SERVICES: 
        return {"status": "error", "message": "Bu servis zaten kuruluyor."}, "", "", {}, set()
    
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
        return {"status": "error", "message": "Servis manifesti bulunamadı."}, "", "", {}, set()

    is_core = manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}
    if not model_file and not is_core: 
        return {"status": "error", "message": "Lütfen bir model dosyası seçin."}, "", "", {}, set()

    hw = "cpu" if is_core else (hardware or g_vars.get("DETECTED_GPU_VENDOR", "cpu"))
    compose_file = manifest.get("compose_files", {}).get(hw)
    if not compose_file: 
        return {"status": "error", "message": f"Bu donanım desteklenmiyor: {hw}"}, "", "", {}, set()

    # Orijinal Environment Fallback
    envs = manifest.get("supported_environments", [])
    selected_env_id = env_id or (envs[0]["id"] if envs else None)
    c_env = next((e for e in envs if e.get("id") == selected_env_id), {})
    
    catalog = manifest.get("models_catalog", [])
    sel_mod = next((m for m in catalog if model_file in (m.get("folder"), m.get("id"), m.get("file_name"))), {})
    model_env = sel_mod.get("env", {})

    f_params, err = _validate_params(params, manifest.get("parameters", {}))
    if err: 
        return err, "", "", {}, set()

    from .core.models import VALID_EXTENSIONS 
    m_dir = os.path.join(s_dir, manifest.get("models_path", "models"))
    model_file = _resolve_model_path(service_id, m_dir, model_file, VALID_EXTENSIONS)
    
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

    mmproj_file = _resolve_model_path(service_id, m_dir, mmproj_file, (".gguf",))

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
    keys.update({"COMPOSE_PROJECT_NAME", "BASE_IMAGE", "MODEL_FILE", "MMPROJ_FILE", "MMPROJ_ARGS", "EXTRA_ARGS", "GPU_COUNT", "REBUILD_IMAGE"})

    config.INSTALLING_SERVICES.add(service_id)
    return None, s_dir, compose_file, build_env, keys

def stop_service(service_id: str) -> bool:
    manifest, s_dir, g_vars = _get_context(service_id)
    if not manifest: return False
    
    category_upper = manifest.get("category", "misc").upper()
    project_name_key = f"{category_upper}_PROJECT_NAME"
    g_vars["COMPOSE_PROJECT_NAME"] = g_vars.get(project_name_key, f"orion-{manifest.get('category', 'misc')}")

    if manifest.get("id") == "orion-hub" or manifest.get("category") in {"core", "hub", "router"}:
        return _run_compose(list(manifest.get("compose_files", {}).values()), "stop", s_dir, g_vars)
    
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

    return _run_compose(list(manifest.get("compose_files", {}).values()), "down", s_dir, g_vars)

def run_installation(service_id: str, service_dir: str, compose_file: str, build_env: dict, env_file_keys: set[str]):
    try:
        # Kurulumda `.env.global` ve `.env.global.local` referansı
        g_vars_file = {
            **_read_env(os.path.join(config.SERVICES_DIR, ".env.global")),
            **_read_env(os.path.join(config.SERVICES_DIR, ".env.global.local"))
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
        
        # Adım 1: Imaj build (sadece gerekiyorsa)
        force_rebuild = str(build_env.get("REBUILD_IMAGE", "false")).lower() == "true"
        image_name = _get_compose_image(compose_file, service_dir)
        if force_rebuild or not _image_exists(image_name):
            build_cmd = ["docker-compose", "-f", compose_file, "build"]
            subprocess.run(build_cmd, cwd=service_dir, env={**os.environ, **build_env})

        # Adım 2: Şimdi servisi başlat (Kod değişikliklerini algılaması için --build eklendi)
        cmd = ["docker-compose", "-f", compose_file, "up", "-d", "--build"]
        res = subprocess.run(cmd, cwd=service_dir, env={**os.environ, **build_env}, capture_output=True)
        
        if res.returncode != 0:
            out = res.stderr.decode("utf-8", errors="replace") + "\n" + res.stdout.decode("utf-8", errors="replace")

            conflicts = {n.lstrip("/") for n in _CONFLICT_PATTERN.findall(out)} 
            managed = {str(build_env.get(k, "")).strip() for k in MANAGED_HOSTS if build_env.get(k)}
            removable_conflicts = sorted(conflicts & managed)
            
            if removable_conflicts:
                logging.warning("Install conflict (%s). Removing %s and retrying...", service_id, removable_conflicts)
                for name in removable_conflicts: 
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                subprocess.run(cmd, cwd=service_dir, env={**os.environ, **build_env})
            else:
                print(f"[INSTALL ERROR] {service_id}: {out}")

    except Exception as e:
        print(f"[INSTALL ERROR] {service_id}: {e}")
    finally:
        config.INSTALLING_SERVICES.discard(service_id)