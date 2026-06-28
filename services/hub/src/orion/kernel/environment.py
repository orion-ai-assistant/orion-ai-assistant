import os


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def get_redis_url() -> str:
    host = os.getenv("REDIS_HOST")
    port = os.getenv("REDIS_PORT")
    if host and port:
        if host == "redis" and not os.path.exists("/.dockerenv"):
            host = "127.0.0.1"
        return f"redis://{host}:{port}/0"
    raise RuntimeError("Missing REDIS_HOST or REDIS_PORT")


def get_postgres_url() -> str:
    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT")
    db = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    if host and port and db and user and password:
        if host == "postgres" and not os.path.exists("/.dockerenv"):
            host = "127.0.0.1"
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    raise RuntimeError("Missing POSTGRES_* env vars")


def get_router_base_urls() -> list[str]:
    host = os.getenv("ROUTER_HOST")
    port = os.getenv("ROUTER_PORT")
    
    if host and port:
        if not os.path.exists("/.dockerenv") and host == "router":
            host = "127.0.0.1"
        
        return [f"http://{host}:{port}"]
        
    raise RuntimeError("Missing ROUTER_HOST or ROUTER_PORT")
