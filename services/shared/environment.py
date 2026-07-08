import os
from pathlib import Path
from collections.abc import Callable

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

    env_files = [
        services_dir / ".env.global",
        services_dir / ".env.global.local",
    ]
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
    host = get_env("ROUTER_HOST")
    port = get_env("ROUTER_PORT")
    if host and port:
        if not os.path.exists("/.dockerenv") and host == "router":
            host = "127.0.0.1"
        return [f"http://{host}:{port}"]
    raise RuntimeError("Missing ROUTER_HOST or ROUTER_PORT")
