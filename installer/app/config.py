import glob
import json
import os
from .core import detect_hardware

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
INSTALLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT  = os.path.dirname(INSTALLER_DIR)
SERVICES_DIR  = os.path.join(PROJECT_ROOT, "services")
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
    local_path = os.path.join(SERVICES_DIR, ".env.global.local")
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

    # HOST+PORT'tan URL'leri otomatik türet — global.env'de elle yazma
    for key, host in list(env.items()):
        if not key.endswith("_HOST") or not host:
            continue
        base = key[:-5]
        port_key = f"{base}_PORT"
        port_val = env.get(port_key)
        if port_val:
            env.setdefault(f"{base}_BASE_URL", f"http://{host}:{port_val}")

    if env.get("REDIS_HOST") and env.get("REDIS_PORT"):
        env.setdefault("REDIS_URL", f"redis://{env['REDIS_HOST']}:{env['REDIS_PORT']}/0")
    # Compose dosyaları için APP_PORT'u servis bazlı ayarla
    # (Her servis kendi .env dosyasında APP_PORT yazar, global override etmez)
    return env

def _iter_manifests():
    global_env = _load_global_env()

    for path in glob.glob(os.path.join(SERVICES_DIR, "**", "manifest.json"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                
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
