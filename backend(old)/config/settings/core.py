"""
Core settings shared between API server and Agent service.
These are required for basic application initialization and do not depend on API-specific variables.
"""
from pathlib import Path
from common.env_helper import get_env

# Backend root directory (backend/)
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# --- Ortam ---
ENV = get_env("ENV")
IS_PROD = ENV == "prod"


# --- Config Ayarları ---
# config_dir ve config_name ayrılarak okunabilirlik ve esneklik sağlandı.
CONFIG_DIR = (get_env("CONFIG_DIR", required=False) or "config/shared").strip()
CONFIG_NAME = (get_env("CONFIG_NAME", required=False) or "ai_config.json").strip()

# Tek noktadan yönetim için full path (Path nesnesi olarak)
CONFIG_FILE = (BACKEND_DIR / CONFIG_DIR / CONFIG_NAME).absolute()

# Compose interpolation kaynaklı (Docker volume referansı)
# Örn: "ai_config_data:/app/config/shared"
CONFIG_VOLUME = (get_env("CONFIG_VOLUME", required=False) or f"ai_config_data:/app/{CONFIG_DIR}").strip()

# Paths (Shared)
AGENT_DATA_DIR = BACKEND_DIR / "agent" / "data"
