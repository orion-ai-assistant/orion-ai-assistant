"""A dump of the memory tables before a migration changes them (docs/hafiza-tasarimi.md §13: "önce otomatik yedek").

pg_dump (custom format: compressed, restorable table by table with pg_restore) of `orion_memory*` and `orion_mem_*`
into the Hub data folder, `backups/memory/memory-<UTC time>-<reason>.dump`; the newest KEEP dumps stay, older ones
go. The password goes to pg_dump in its environment, never on its command line. Restore:
`pg_restore --clean --if-exists -d <database> <file>` (the Hub stopped).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

KEEP = 5  # Dumps kept: the last few migrations (each is the whole memory, so not more).
TIMEOUT = 3600  # Seconds one dump may take (a large memory on a slow disk), then it counts as failed.
TABLES = ("orion_memory*", "orion_mem_*")


def backup_dir() -> Path:
    from orion.kernel.data_paths import hub_data
    return hub_data() / "backups" / "memory"


def find_pg_dump() -> str | None:
    """The pg_dump of the PostgreSQL Orion runs (its own bin folder first: a pg_dump older than the server
    refuses), else one on PATH. ORION_PG_DUMP names one explicitly."""
    explicit = os.environ.get("ORION_PG_DUMP")
    if explicit and Path(explicit).is_file():
        return explicit
    name = "pg_dump.exe" if os.name == "nt" else "pg_dump"
    from orion.kernel.data_paths import install_root
    root = install_root()
    if root is not None:
        for folder in (".local_db/postgres/bin", "tools/pgsql/bin"):
            candidate = Path(root) / folder / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("pg_dump")


def _connection_args(url: str) -> tuple[list[str], dict[str, str]]:
    parsed = urlparse(url)
    args = ["-h", parsed.hostname or "127.0.0.1", "-p", str(parsed.port or 5432),
            "-U", unquote(parsed.username or "postgres"), "-d", (parsed.path or "/postgres").lstrip("/")]
    env = {"PGPASSWORD": unquote(parsed.password)} if parsed.password else {}
    return args, env


def _prune(folder: Path, keep: int = KEEP) -> list[Path]:
    dumps = sorted(folder.glob("memory-*.dump"), key=lambda p: p.name, reverse=True)
    removed = []
    for old in dumps[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError as exc:
            logger.warning("Memory backup: %s could not be removed: %s", old, exc)
    return removed


async def backup(reason: str, url: str | None = None, folder: Path | None = None) -> Path | None:
    """Dump the memory tables; the file's path, or None when it could not be made (logged: no pg_dump, a failed
    run). Partial files are removed."""
    pg_dump = find_pg_dump()
    if pg_dump is None:
        logger.warning("Memory backup skipped before %s: pg_dump not found", reason)
        return None
    try:
        if url is None:
            from services.shared.environment import get_postgres_url
            url = get_postgres_url()
        folder = folder or backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.error("Memory backup before %s FAILED: %s", reason, exc)
        return None
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = folder / f"memory-{stamp}-{re.sub(r'[^A-Za-z0-9_.-]+', '_', reason)[:60]}.dump"
    args, env = _connection_args(url)
    command = [pg_dump, *args, "-Fc", "--no-owner", "-f", str(target)]
    for table in TABLES:
        command += ["-t", table]
    started = asyncio.get_running_loop().time()
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, env={**os.environ, **env},
            creationflags=0x08000000 if os.name == "nt" else 0)
        try:
            _, error = await asyncio.wait_for(process.communicate(), TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"timed out after {TIMEOUT}s")
        if process.returncode != 0:
            raise RuntimeError((error or b"").decode("utf-8", "replace").strip()[:500] or f"exit {process.returncode}")
    except Exception as exc:
        logger.error("Memory backup before %s FAILED: %s", reason, exc)
        target.unlink(missing_ok=True)
        return None
    size = target.stat().st_size
    removed = _prune(folder)
    logger.info("Memory backup before %s: %s (%.1f MB, %.1fs)%s", reason, target, size / 2**20,
                asyncio.get_running_loop().time() - started,
                f"; {len(removed)} older removed" if removed else "")
    return target
