import re

with open('orion.py', 'r', encoding='utf-8') as f:
    content = f.read()

funcs = """
def find_orionrouter_script():
    import os, shutil
    if os.name == 'nt':
        appdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            ps1_path = os.path.join(appdata, "OrionRouter", "orionrouter.ps1")
            if os.path.exists(ps1_path):
                return "win", ps1_path
    else:
        sh_path = os.path.expanduser("~/.local/share/OrionRouter/orionrouter")
        if os.path.exists(sh_path):
            return "unix", sh_path
            
    cmd = shutil.which("orionrouter")
    if cmd:
        return "cmd", cmd
    return None, None

def handle_setup(args):
    import os, subprocess, sys
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
    print(f"\\n==========================================")
    print(f"   Orion AI Assistant Setup ({mode.upper()})")
    print(f"==========================================\\n")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    reqs = ["git", "python"] if mode == "local" else ["git", "docker", "python"]
    for req in reqs:
        if not __import__("shutil").which(req):
            print(f"[ERROR] Required tool not found: {req}")
            sys.exit(1)
            
    print("[OK] All required tools are present.")
    
    if mode == "local":
        local_db_script = os.path.join(base_dir, "local_db_setup.py")
        if os.path.exists(local_db_script):
            subprocess.run([sys.executable, local_db_script], check=True)
            
        print("[*] Installing Orion Router globally...")
        if os.name == 'nt':
            cmd = ["powershell", "-c", "Invoke-Command -ScriptBlock ([scriptblock]::Create((irm https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.ps1))) -ArgumentList 'local'"]
            subprocess.run(cmd, check=True)
        else:
            cmd = ["bash", "-c", "curl -sL https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/install.sh | bash -s local"]
            subprocess.run(cmd, check=True)
            
    print("[*] Setting up Installer Virtual Environment...")
    setup_environment("installer")
    print(f"\\n[OK] Setup completed successfully! You can now use 'python orion.py start {mode}' or other commands.")

def handle_start(args):
    import os, subprocess, glob
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
    print(f"\\nStarting Orion AI Assistant in {mode.upper()} mode...")
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hub_dir = os.path.join(base_dir, "services", "hub")
        hub_py = setup_environment("services/hub")
        cflags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        
        pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", "pg_ctl.exe")
        pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
        if os.path.exists(pg_ctl) and os.path.exists(pg_data):
            print("[*] Starting Portable PostgreSQL...")
            subprocess.run([pg_ctl, "start", "-D", pg_data, "-w"])
            
        redis_exe = os.path.join(base_dir, ".local_db", "redis", "redis-server.exe")
        if os.path.exists(redis_exe):
            print("[*] Starting Portable Redis...")
            subprocess.Popen([redis_exe], creationflags=cflags)
        
        if os.path.exists(os.path.join(hub_dir, "run_local.py")) and hub_py and os.path.exists(hub_py):
            print("[*] Starting Orion Hub (Local)...")
            cmd_hub = ["cmd", "/k", hub_py, "run_local.py"] if os.name == 'nt' else [hub_py, "run_local.py"]
            subprocess.Popen(cmd_hub, cwd=hub_dir, creationflags=cflags)
            
        plat, path = find_orionrouter_script()
        if path:
            print("[*] Starting Orion Router (Local)...")
            if plat == "win":
                cmd_router = ["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "start"]
                subprocess.Popen(cmd_router, creationflags=cflags)
            else:
                subprocess.Popen([path, "start"], creationflags=cflags)
        else:
            print("[!] Orion Router is not installed yet. Please run 'python orion.py setup local' first.")
            
        print("[OK] Native processes have been started in separate windows/background.")
    else:
        # Docker start logic
        import shutil
        if not shutil.which("docker"):
            print("[!] Docker is not installed.")
            return
        ensure_docker_running()
        subprocess.run(["docker", "compose", "up", "-d"])

def handle_stop(args):
    import os, subprocess, glob
    mode = "docker"
    if args and args[0] in ["local", "docker"]:
        mode = args[0]
        
    print(f"\\nStopping Orion AI Assistant in {mode.upper()} mode...")
    
    if mode == "local":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if os.name == 'nt':
            subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "run_local.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
            plat, path = find_orionrouter_script()
            if path and plat == "win":
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, "stop"], capture_output=True)
            else:
                subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "orionrouter" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
            
            pg_data = os.path.join(base_dir, ".local_db", "postgres", "data")
            pg_ctl = os.path.join(base_dir, ".local_db", "postgres", "bin", "pg_ctl.exe")
            if os.path.exists(pg_ctl) and os.path.exists(pg_data):
                subprocess.run([pg_ctl, "stop", "-D", pg_data, "-m", "fast"])
            
            subprocess.run(["powershell", "-c", 'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "redis-server.exe" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "run_local.py"], capture_output=True)
            plat, path = find_orionrouter_script()
            if path:
                subprocess.run([path, "stop"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "orionrouter"], capture_output=True)
        print("[OK] Local Python processes stopped.")
"""

# COMMANDS kısmını bul
commands_pattern = r"COMMANDS = \{.*?def cmd_help\(args=None\):"
new_commands = """COMMANDS = {
    "installer": handle_installer,
    "setup": handle_setup,
    "start": handle_start,
    "stop": handle_stop,
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help
}

def cmd_help(args=None):"""

content = re.sub(commands_pattern, new_commands, content, flags=re.DOTALL)

# cmd_help içine setup, start, stop ekle
help_pattern = r"def cmd_help\(args=None\):.*?(?=\n\n)"
new_help = """def cmd_help(args=None):
    print("\\n=== ORION AI ASSISTANT MANAGER ===")
    print("  python orion.py setup [local|docker]   : Prepare the environment")
    print("  python orion.py start [local|docker]   : Start services")
    print("  python orion.py stop [local|docker]    : Stop services")
    print("  python orion.py installer              : Start GUI installer")
    print("==================================\\n")"""

content = re.sub(help_pattern, new_help, content, flags=re.DOTALL)

# Dosyanın sonuna yeni fonksiyonları ekle (COMMANDS tanımından önce)
content = content.replace("# YENİ KOMUT EKLENECEĞİ ZAMAN SADECE BU SÖZLÜĞE YAZILACAK", funcs + "\n# YENİ KOMUT EKLENECEĞİ ZAMAN SADECE BU SÖZLÜĞE YAZILACAK")

with open('orion.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("orion.py fixed!")
