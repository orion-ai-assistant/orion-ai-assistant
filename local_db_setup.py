import os
import sys
import platform
import urllib.request
import zipfile
import subprocess
import shutil
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_DIR = os.path.join(BASE_DIR, ".local_db")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Orion", "cache")

def install_windows():
    from services.shared.windows_postgres import (
        ARCHIVE_NAME, ARCHIVE_URL, provision_runtime, verify_unicode_runtime,
    )
    os.makedirs(LOCAL_DB_DIR, exist_ok=True)
    
    # 1. Install Redis
    redis_dir = os.path.join(LOCAL_DB_DIR, "redis")
    if not os.path.exists(os.path.join(redis_dir, "redis-server.exe")):
        os.makedirs(CACHE_DIR, exist_ok=True)
        redis_zip = os.path.join(CACHE_DIR, "redis.zip")
        if not os.path.exists(redis_zip):
            print("[*] Downloading Portable Redis for Windows...")
            urllib.request.urlretrieve("https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip", redis_zip)
        else:
            print("[*] Portable Redis zip found in cache.")
        
        print("[*] Extracting Redis...")
        with zipfile.ZipFile(redis_zip, 'r') as zip_ref:
            zip_ref.extractall(redis_dir)
    else:
        print("[OK] Portable Redis already installed.")
        
    # 2. Install Postgres
    pg_dir = os.path.join(LOCAL_DB_DIR, "postgres")
    if not os.path.exists(os.path.join(pg_dir, "bin", "pg_ctl.exe")):
        os.makedirs(CACHE_DIR, exist_ok=True)
        pg_zip = os.path.join(CACHE_DIR, ARCHIVE_NAME)
        if not os.path.exists(pg_zip):
            print("[*] Downloading Portable PostgreSQL 16 for Windows (this might take a few minutes)...")
            urllib.request.urlretrieve(ARCHIVE_URL, pg_zip)
        else:
            print("[*] Portable PostgreSQL zip found in cache.")
        
        print("[*] Extracting PostgreSQL...")
        provision_runtime(pg_zip, pg_dir)
        print("[OK] Portable PostgreSQL binaries extracted.")
    if not pg_dir.isascii():
        verify_unicode_runtime(pg_dir)
    from services.shared.runtime_layout import data_root
    data_dir = str(data_root(BASE_DIR) / '.local_db/postgres/data')
    if not os.path.exists(os.path.join(data_dir, "PG_VERSION")):
        # Initialize Database
        initdb_exe = os.path.join(pg_dir, "bin", "initdb.exe")
        pg_ctl_exe = os.path.join(pg_dir, "bin", "pg_ctl.exe")
        psql_exe = os.path.join(pg_dir, "bin", "psql.exe")
        
        print("[*] Initializing PostgreSQL Database Cluster...")
        # A failed/unknown cluster requires explicit repair, never a data wipe.
        if os.path.exists(data_dir) and os.listdir(data_dir):
            raise ValueError('PostgreSQL data directory is not empty; explicit repair required')
            
        subprocess.run([initdb_exe, "-D", data_dir, "-U", "postgres", "--auth=trust", "--locale=C", "--encoding=UTF8"], check=True)
        
        from services.shared.native_postgres import Postgres
        from services.shared.environment import get_env
        port = int(get_env('POSTGRES_PORT', default='5445', required=False))
        temporary_db = Postgres(pg_ctl_exe, data_dir, port, 'postgres', 'postgres')
        temporary_db.ensure()
        try:
            # SQL travels over stdin; the cluster and authenticated connection
            # have already been verified, including their actual data directory.
            command = [psql_exe, '-h', '127.0.0.1', '-p', str(port), '-U', 'postgres',
                       '-d', 'postgres', '-w', '-X', '-v', 'ON_ERROR_STOP=1']
            subprocess.run(command, input="CREATE USER orion WITH PASSWORD 'orion';\nGRANT pg_read_all_settings TO orion;\nCREATE DATABASE orion OWNER orion;",
                           text=True, encoding='utf-8', env=dict(os.environ, PGCLIENTENCODING='UTF8'), check=True, timeout=10)
        finally:
            temporary_db.stop()

        print("[OK] Portable PostgreSQL database cluster initialized.")
    else:
        print("[OK] Portable PostgreSQL database cluster already initialized.")

def install_mac():
    print("[*] Setting up Redis and PostgreSQL on macOS using Homebrew...")
    if shutil.which("brew") is None:
        print("[ERROR] Homebrew is not installed! Please install Homebrew first: https://brew.sh/")
        sys.exit(1)
        
    try:
        brew_prefix = subprocess.run(["brew", "--prefix"], capture_output=True, text=True).stdout.strip()
        user_logs = os.path.expanduser("~/Library/Logs/Homebrew")
        os.makedirs(user_logs, exist_ok=True)
        
        needs_fix = False
        if not os.access(brew_prefix, os.W_OK) or not os.access(user_logs, os.W_OK):
            needs_fix = True
        cellar_dir = os.path.join(brew_prefix, "Cellar")
        if os.path.exists(cellar_dir) and not os.access(cellar_dir, os.W_OK):
            needs_fix = True
            
        if needs_fix:
            print("\n[!] Homebrew directories are not writable by your user.")
            print("Requesting administrator privileges (sudo) to automatically fix Homebrew write permissions...")
            user = os.environ.get("USER", os.getlogin())
            subprocess.run(["sudo", "chown", "-R", user, user_logs, brew_prefix])
            subprocess.run(["chmod", "u+w", user_logs, brew_prefix])

        subprocess.run(["brew", "install", "redis", "postgresql@16"], check=False)
        subprocess.run(["brew", "services", "start", "redis"], check=False)
        subprocess.run(["brew", "services", "start", "postgresql@16"], check=False)
        subprocess.run(["brew", "link", "postgresql@16", "--force"], check=False)
        
    except Exception as e:
        print(f"[!] Warning during Homebrew installation: {e}")
    
    # Wait a bit for postgres to start
    import time
    time.sleep(3)
    
    print("[*] Configuring databases...")
    psql_cmd = ["psql", "postgres", "-c"]
    try:
        subprocess.run(psql_cmd + ["CREATE USER orion WITH PASSWORD 'orion';"])
        subprocess.run(psql_cmd + ["CREATE DATABASE orion OWNER orion;"])
    except Exception as e:
        print(f"[!] Warning during DB setup (maybe users already exist): {e}")
        
    print("[OK] Database setup complete via Homebrew.")

def install_linux():
    print("[*] Setting up Redis and PostgreSQL on Linux using APT...")
    if shutil.which("apt-get") is None:
        print("[ERROR] APT package manager not found. Are you on Debian/Ubuntu?")
        sys.exit(1)
        
    try:
        print("[*] Requesting administrator privileges (sudo) to install dependencies...")
        subprocess.run(["sudo", "apt-get", "update"], check=True)
        subprocess.run(["sudo", "apt-get", "install", "-y", "redis-server", "postgresql", "postgresql-contrib"], check=True)
        
        subprocess.run(["sudo", "systemctl", "enable", "--now", "redis-server"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "--now", "postgresql"], check=True)
        
        # Wait a bit for postgres to start
        import time
        time.sleep(3)
        
        print("[*] Configuring databases...")
        subprocess.run(["sudo", "-u", "postgres", "psql", "-c", "CREATE USER orion WITH PASSWORD 'orion';"])
        subprocess.run(["sudo", "-u", "postgres", "psql", "-c", "CREATE DATABASE orion OWNER orion;"])
    except Exception as e:
        print(f"[!] Warning during DB setup (maybe users already exist): {e}")
        
    print("[OK] Database setup complete via APT.")

def main():
    print("==========================================")
    print("   Orion Local Database & Cache Setup")
    print("==========================================")
    
    sys_name = platform.system().lower()
    
    try:
        if sys_name == 'windows':
            install_windows()
        elif sys_name == 'darwin':
            install_mac()
        elif sys_name == 'linux':
            install_linux()
        else:
            print(f"[ERROR] Unsupported OS: {sys_name}")
            sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Setup failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
