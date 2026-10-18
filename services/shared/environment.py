import os
from pathlib import Path
from collections.abc import Callable
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def load_environment():
    if not load_dotenv:
        return
    this_file = Path(__file__).resolve()
    # services/shared/environment.py -> parents: 1=shared, 2=services, 3=repo_root
    repo_root = this_file.parents[2]
    services_dir = repo_root / "services"
    from .runtime_layout import data_root, component
    try:
        data_services = data_root(repo_root) / 'services'
        packaged = component(repo_root)
    except RuntimeError:
        # This tree carries an orion-release.json but is not a registered component: the retained
        # bootstrap that runs the update watcher. It has no environment of its own; the caller (the
        # launcher) supplies the registered component's environment. Raising here broke every stop
        # started by the watcher ("Package is not registered at this path").
        return

    env_files = [
        services_dir / ".env.global",
        data_services / ".env.global.local",
    ]
    if packaged:
        # Packaged standalone entrypoints do not rely on the source launcher's
        # pre-merged environment. Data overrides must win over package defaults;
        # explicitly supplied process values still have the highest priority.
        from dotenv import dotenv_values
        values = {}
        for env_file in env_files:
            if env_file.exists():
                values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
        for key, value in values.items():
            os.environ.setdefault(key, value)
        return
    for env_file in env_files:
        if env_file.exists():
            load_dotenv(env_file)


load_environment()


def get_env(name: str, default=None, required: bool = True, cast: Callable | None = None):
    value = os.getenv(name, default)
    if required and (value is None or (isinstance(value, str) and value == "")):
        raise RuntimeError(f"Missing required env var: {name}")
    if cast is not None and value is not None:
        try:
            return cast(value)
        except Exception as exc:
            raise RuntimeError(f"Failed to cast env var {name}: {exc}") from exc
    return value


def get_redis_url() -> str:
    host = get_env("REDIS_HOST")
    port = get_env("REDIS_PORT")
    if host and port:
        if host == "redis":
            if not os.path.exists("/.dockerenv"):
                host = "127.0.0.1"
            else:
                port = "6379"
        return f"redis://{host}:{port}/0"
    raise RuntimeError("Missing REDIS_HOST or REDIS_PORT")


def get_postgres_url() -> str:
    host = get_env("POSTGRES_HOST")
    port = get_env("POSTGRES_PORT")
    db = get_env("POSTGRES_DB")
    user = get_env("POSTGRES_USER")
    password = get_env("POSTGRES_PASSWORD")
    if host and port and db and user and password:
        if host == "postgres":
            if not os.path.exists("/.dockerenv"):
                host = "127.0.0.1"
            else:
                port = "5432"
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    raise RuntimeError("Missing POSTGRES credentials")


def get_router_base_urls() -> list[str]:
    # Trust storage holds UUID + SPKI independently from the current address.
    from orion.kernel.local_security import SecurityStore
    trusted = SecurityStore().routers()
    from services.shared.router_connection import read_connection
    selected = read_connection()
    if selected:
        match = next((entry for entry in trusted if entry['id'] == selected.get('id')), None)
        if not match or (selected.get('fp') and selected['fp'] != match['fp']):
            raise RuntimeError('Kayıtlı Router güveni bulunamadı; bağlantı otomatik değiştirilmedi.')
        return [match['url']]
    if trusted:
        if len(trusted) != 1:
            raise RuntimeError('Birden fazla güvenilen Router var; panelden kullanacağınız Router’ı seçin.')
        return [trusted[0]['url']]
    host = get_env("ROUTER_HOST")
    port = get_env("ORION_ROUTER_TLS_PORT", default="9443", required=False)
    if host and port:
        if not os.path.exists("/.dockerenv") and host == "router":
            host = "127.0.0.1"
        return [f"https://{host}:{port}"]
    raise RuntimeError("Missing ROUTER_HOST or ROUTER_PORT")


def get_tts_base_urls() -> list[str]:
    host = get_env("TTS_HOST", default="tts", required=False)
    port = get_env("TTS_PORT", default=8810, required=False)
    if host and port:
        if not os.path.exists("/.dockerenv") and host == "tts":
            host = "127.0.0.1"
        return [f"http://{host}:{port}"]
    return ["http://127.0.0.1:8810"]

