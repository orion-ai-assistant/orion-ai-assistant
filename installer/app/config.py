import json
import os
import sys
from .core import detect_hardware

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
INSTALLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT  = os.path.dirname(INSTALLER_DIR)
# `python -m app` starts inside installer, but shared runtime packages live
# at the repository root. Resolve them independently of the launch directory.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
SERVICES_DIR  = os.path.join(PROJECT_ROOT, "services")
from services.shared.runtime_layout import data_root
DATA_SERVICES_DIR = str(data_root(PROJECT_ROOT) / 'services')
UI_DIR        = os.path.join(INSTALLER_DIR, "app", "ui")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
INSTALLING_SERVICES: set[str]       = set()
DOWNLOADING_MODELS:  dict[str, dict] = {}  # "service_id:model_id" -> {progress, total_mb}
INSTALL_ERRORS:      dict[str, str]  = {}
SHOULD_EXIT:         bool            = False

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------
def get_env_global() -> dict[str, str]:
    """Donanımı canlı olarak tespit eder ve sonuçları döner."""
    return detect_hardware.detect()

# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
def models_directory(service_id, service_dir, manifest):
    from services.shared.model_locations import models_directory as resolve
    settings = _load_global_env()
    return str(resolve(service_id, service_dir, manifest,
                       mode=os.environ.get('ORION_INSTALL_MODE', settings.get('ORION_INSTALL_MODE', 'docker')),
                       settings=settings))


def installation_operation():
    from contextlib import nullcontext
    settings = _load_global_env()
    if os.environ.get('ORION_INSTALL_MODE', settings.get('ORION_INSTALL_MODE', 'docker')) != 'local':
        return nullcontext()
    from services.shared.installation_registry import Registry, operation_lock
    return operation_lock(Registry(settings.get('ORION_INSTALLATION_REGISTRY')).path)


def installation_registry():
    from services.shared.installation_registry import Registry
    return Registry(_load_global_env().get('ORION_INSTALLATION_REGISTRY'))


def _load_global_env() -> dict[str, str]:
    """global.env dosyasını okur ve URL'leri HOST+PORT'tan otomatik türetir."""
    env = {}
    path = os.path.join(SERVICES_DIR, ".env.global")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#") and line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

    # Yerel ezmeleri yükle (.env.global.local)
    local_path = os.path.join(DATA_SERVICES_DIR, ".env.global.local")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#") and line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

    # CLI_LANG ayarını otomatik algıla ve yerel ayarlara kaydet
    if "CLI_LANG" not in env:
        import locale
        try:
            sys_lang = locale.getdefaultlocale()[0]
            lang_code = sys_lang.split("_")[0] if sys_lang else "en"
        except Exception:
            lang_code = "en"
        
        env["CLI_LANG"] = lang_code
        try:
            with open(local_path, "a", encoding="utf-8") as f:
                f.write(f"\nCLI_LANG={lang_code}\n")
        except Exception:
            pass

    env.setdefault('ORION_ROUTER_TLS_PORT', '9443')
    # Browser launch links use only this PC's guarded HTTP UI. Router network
    # transport still uses ORION_ROUTER_TLS_PORT and the pinned TLS policy.
    env['HUB_LAUNCH_URL'] = f'http://127.0.0.1:{env.get("ORION_LOCAL_PORT", "8910")}/manage'
    env['ROUTER_LAUNCH_URL'] = f'http://127.0.0.1:{env.get("ORION_ROUTER_LOCAL_PORT", "20128")}/dashboard'
    # HOST+PORT'tan URL'leri otomatik türet — global.env'de elle yazma
    for key, host in list(env.items()):
        if not key.endswith("_HOST") or not host:
            continue
        base = key[:-5]
        port_key = f"{base}_PORT"
        port_val = env.get(port_key)
        if base == 'ROUTER':
            port_val = env.get('ORION_ROUTER_TLS_PORT', '9443')
        if port_val:
            scheme = 'https' if base in {'HUB', 'ROUTER', 'API_2', 'NGINX', 'INSTALLER'} else 'http'
            env.setdefault(f"{base}_BASE_URL", f"{scheme}://{host}:{port_val}")

    if env.get("REDIS_HOST") and env.get("REDIS_PORT"):
        env.setdefault("REDIS_URL", f"redis://{env['REDIS_HOST']}:{env['REDIS_PORT']}/0")
    # Compose dosyaları için APP_PORT'u servis bazlı ayarla
    # (Her servis kendi .env dosyasında APP_PORT yazar, global override etmez)
    return env

def _iter_manifests():
    global_env = _load_global_env()

    # Runtime profiles and dependencies also contain manifest.json files. Never
    # descend into those trees when discovering Orion service definitions.
    paths = []
    for directory, children, files in os.walk(SERVICES_DIR):
        children[:] = [name for name in children if not name.startswith('.')
                       and name not in {'data', 'node_modules', '__pycache__', 'models'}]
        if 'manifest.json' in files:
            paths.append(os.path.join(directory, 'manifest.json'))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if (not isinstance(data, dict) or not isinstance(data.get('id'), str)
                        or not data['id'].strip() or not isinstance(data.get('category'), str)):
                    continue
                
                # Global.env'den port ve host isimlerini çek ve manifest verisini ez
                category_upper = data.get("category", "").upper()
                if category_upper == "EMBEDDING":
                    category_upper = "EMBED"
                
                port_env = data.get("port_env") or f"{category_upper}_PORT"
                host_env = data.get("host_env") or f"{category_upper}_HOST"
                
                # Diğer modüllerin (örn. services.py) bu değişkenlere erişebilmesi için manifest verisini güncelle
                data["port_env"] = port_env
                data["host_env"] = host_env
                
                if port_env in global_env:
                    try:
                        data["port"] = int(global_env[port_env])
                    except ValueError:
                        pass
                if host_env in global_env:
                    data["container_name"] = global_env[host_env]
                
                yield data, path
        except Exception:
            continue

def all_manifests() -> list[tuple[dict, str]]:
    return list(_iter_manifests())

def find_manifest(service_id: str) -> tuple[dict, str] | tuple[None, None]:
    return next(((d, p) for d, p in _iter_manifests() if d["id"] == service_id), (None, None))
