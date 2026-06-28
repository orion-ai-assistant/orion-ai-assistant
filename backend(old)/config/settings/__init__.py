"""
Centralized settings package.

Lazy attribute lookup for backward compatibility.
`from config.settings import X` hâlâ çalışır;
sadece X'i içeren alt modül yüklendiği için diğerleri tetiklenmez.

Alt modüller:
  - admin:      ADMIN_HOST, ADMIN_PORT, ALLOWED_ORIGINS, API_BACKEND_URL
  - ai:         GOOGLE_API_KEY, LANGSMITH_API_KEY, TAVILY_API_KEY
  - api_server: ADMIN_TOKEN, API_HOST/PORT, ENV, IS_PROD,
                                                                REDIS_URL, paths
    - core:       CONFIG_DIR, CONFIG_NAME, CONFIG_FILE, paths
"""


def __getattr__(name: str):
    """Lazy re-export: only the relevant submodule is loaded on first access."""
    for module_name in ("core", "admin", "ai", "api_server"):
        try:
            import importlib
            module = importlib.import_module(f".{module_name}", package=__name__)
            if hasattr(module, name):
                globals()[name] = getattr(module, name)
                return globals()[name]
        except Exception:
            continue

    raise AttributeError(f"module 'config.settings' has no attribute {name}")
