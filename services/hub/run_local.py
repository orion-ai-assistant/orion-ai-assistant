import os
import sys
import subprocess
import signal
import time

def main():
    print("==========================================")
    print(" Starting Orion Hub natively (Local Mode)")
    print("==========================================")
    
    # Environment config
    hub_port = os.environ.get("HUB_PORT", "8000")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    
    # Commands
    api_cmd = [sys.executable, "-m", "uvicorn", "orion.api.main:app", "--host", "127.0.0.1", "--port", hub_port]
    worker_cmd = [sys.executable, "-m", "orion.worker.main"]
    
    print(f"[*] Launching API on port {hub_port}...")
    api_proc = subprocess.Popen(api_cmd, env=env)
    
    print(f"[*] Launching Worker...")
    worker_proc = subprocess.Popen(worker_cmd, env=env)
    
    def handle_shutdown(*args):
        print("\n[!] Shutting down native Hub processes...")
        try:
            api_proc.terminate()
        except:
            pass
            
        try:
            worker_proc.terminate()
        except:
            pass
            
        api_proc.wait(timeout=5)
        worker_proc.wait(timeout=5)
        print("[OK] Hub stopped.")
        sys.exit(0)
        
    # Windows signal handling
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, handle_shutdown)
    
    try:
        api_proc.wait()
        worker_proc.wait()
    except KeyboardInterrupt:
        handle_shutdown()

if __name__ == "__main__":
    main()
