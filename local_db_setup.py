import os
import sys
import platform
import urllib.request
import zipfile
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_DIR = os.path.join(BASE_DIR, ".local_db")

def install_windows():
    os.makedirs(LOCAL_DB_DIR, exist_ok=True)
    
    # 1. Install Redis
    redis_dir = os.path.join(LOCAL_DB_DIR, "redis")
    if not os.path.exists(os.path.join(redis_dir, "redis-server.exe")):
        print("[*] Downloading Portable Redis for Windows...")
        redis_zip = os.path.join(LOCAL_DB_DIR, "redis.zip")
        urllib.request.urlretrieve("https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip", redis_zip)
        
        print("[*] Extracting Redis...")
        with zipfile.ZipFile(redis_zip, 'r') as zip_ref:
            zip_ref.extractall(redis_dir)
        os.remove(redis_zip)
    else:
        print("[OK] Portable Redis already installed.")
        
    # 2. Install Postgres
    pg_dir = os.path.join(LOCAL_DB_DIR, "postgres")
    if not os.path.exists(os.path.join(pg_dir, "bin", "pg_ctl.exe")):
        print("[*] Downloading Portable PostgreSQL 16 for Windows (this might take a few minutes)...")
        pg_zip = os.path.join(LOCAL_DB_DIR, "postgres.zip")
        urllib.request.urlretrieve("https://get.enterprisedb.com/postgresql/postgresql-16.3-1-windows-x64-binaries.zip", pg_zip)
        
        print("[*] Extracting PostgreSQL...")
        with zipfile.ZipFile(pg_zip, 'r') as zip_ref:
            zip_ref.extractall(LOCAL_DB_DIR)
        
        # EDB zip creates a 'pgsql' folder, let's rename it
        pgsql_extracted = os.path.join(LOCAL_DB_DIR, "pgsql")
        if os.path.exists(pgsql_extracted):
            # Clean up existing target if any
            if os.path.exists(pg_dir):
                shutil.rmtree(pg_dir, ignore_errors=True)
            os.rename(pgsql_extracted, pg_dir)
        os.remove(pg_zip)
        print("[OK] Portable PostgreSQL binaries extracted.")
        
    data_dir = os.path.join(pg_dir, "data")
    if not os.path.exists(os.path.join(data_dir, "PG_VERSION")):
        # Initialize Database
        initdb_exe = os.path.join(pg_dir, "bin", "initdb.exe")
        pg_ctl_exe = os.path.join(pg_dir, "bin", "pg_ctl.exe")
        psql_exe = os.path.join(pg_dir, "bin", "psql.exe")
        
        print("[*] Initializing PostgreSQL Database Cluster...")
        # Clear existing data directory if it's partially initialized
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir, ignore_errors=True)
            
        subprocess.run([initdb_exe, "-D", data_dir, "-U", "postgres", "--auth=trust", "--locale=C", "--encoding=UTF8"], check=True)
        
        print("[*] Starting temporary PostgreSQL to create users...")
        subprocess.run([pg_ctl_exe, "start", "-D", data_dir, "-w"])
        
        try:
            print("[*] Creating router_user and orion_router database...")
            subprocess.run([psql_exe, "-U", "postgres", "-c", "CREATE USER router_user WITH PASSWORD 'router_pass';"])
            subprocess.run([psql_exe, "-U", "postgres", "-c", "CREATE DATABASE orion_router OWNER router_user;"])
            
            print("[*] Creating orion user and orion database...")
            subprocess.run([psql_exe, "-U", "postgres", "-c", "CREATE USER orion WITH PASSWORD 'orion';"])
            subprocess.run([psql_exe, "-U", "postgres", "-c", "CREATE DATABASE orion OWNER orion;"])
        finally:
            print("[*] Stopping temporary PostgreSQL...")
            subprocess.run([pg_ctl_exe, "stop", "-D", data_dir, "-m", "fast"])
            
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
        subprocess.run(psql_cmd + ["CREATE USER router_user WITH PASSWORD 'router_pass';"])
        subprocess.run(psql_cmd + ["CREATE DATABASE orion_router OWNER router_user;"])
        
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
        subprocess.run(["sudo", "-u", "postgres", "psql", "-c", "CREATE USER router_user WITH PASSWORD 'router_pass';"])
        subprocess.run(["sudo", "-u", "postgres", "psql", "-c", "CREATE DATABASE orion_router OWNER router_user;"])
        
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
