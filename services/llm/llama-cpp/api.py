import os
import sys
import subprocess
import signal

def get_env_dict():
    env_vars = {}
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()
    return env_vars

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_vars = get_env_dict()
    
    # Donanım durumunu .env'den okuyalım
    hw_type = "cpu"
    env_path = os.path.join(base_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line.startswith("orion_hw_id="):
                    hw_val = line.split("=", 1)[1]
                    if "nvidia" in hw_val or "cuda" in hw_val:
                        hw_type = "cuda"
                    elif "vulkan" in hw_val or "amd" in hw_val or "rocm" in hw_val:
                        hw_type = "vulkan"
                    elif "cpu" in hw_val:
                        hw_type = "cpu"
                elif line.startswith("base_image="):
                    if "cuda" in line:
                        hw_type = "cuda"
                    elif "vulkan" in line:
                        hw_type = "vulkan"
                
    bin_dir = os.path.join(base_dir, "bin", hw_type)
    exe_path = os.path.join(bin_dir, "llama-server.exe" if os.name == 'nt' else "llama-server")
    
    if not os.path.exists(exe_path):
        print(f"llama-server ({hw_type}) binary not found! Please ensure installation was successful.")
        sys.exit(1)

    env_vars = get_env_dict()
    
    model_file = env_vars.get("MODEL_FILE", "")
    if not model_file:
        print("MODEL_FILE not set in .env")
        sys.exit(1)
        
    model_path = os.path.join(base_dir, "models", model_file)
    if not os.path.exists(model_path):
        # Fallback to absolute if needed, or if it's already absolute
        if os.path.exists(model_file):
            model_path = model_file
        else:
            print(f"Model file not found: {model_path}")
            sys.exit(1)

    port = env_vars.get("LLM_PORT")
    if not port:
        # Check global env
        global_env = os.path.join(base_dir, "..", "..", ".env.global")
        if os.path.exists(global_env):
            with open(global_env, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("LLM_PORT="):
                        port = line.strip().split("=")[1]
                        break
    if not port: port = "8085"
    
    # Base command
    cmd = [
        exe_path,
        "-m", model_path,
        "--port", port,
        "--host", "0.0.0.0",
        "-c", "8192"  # Context size, can be overridden by EXTRA_ARGS
    ]
    
    # GPU layers
    gpu_count = env_vars.get("GPU_COUNT", "0")
    if str(gpu_count).isdigit() and int(gpu_count) > 0:
        # User requested GPU offloading. 
        # Typically -ngl 99 offloads all layers to GPU.
        cmd.extend(["-ngl", "99"])
        
    # Multimodal / Vision
    mmproj_file = env_vars.get("MMPROJ_FILE", "")
    if mmproj_file:
        mmproj_path = os.path.join(base_dir, "models", mmproj_file)
        if os.path.exists(mmproj_path):
            cmd.extend(["--mmproj", mmproj_path])
        elif os.path.exists(mmproj_file):
            cmd.extend(["--mmproj", mmproj_file])

    # Extra arguments defined by user
    extra_args = env_vars.get("EXTRA_ARGS", "")
    if extra_args:
        # Basitçe parçala (tırnak işaretleri arası boşlukları vs. tam çözemez ama şimdilik yeterli)
        import shlex
        cmd.extend(shlex.split(extra_args))
        
    print(f"Starting Llama.cpp Server: {' '.join(cmd)}")
    
    # Start the process
    process = subprocess.Popen(cmd)
    
    # Handle graceful shutdown
    def handle_sigterm(signum, frame):
        print("Stopping Llama.cpp Server...")
        process.terminate()
        process.wait()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    if os.name != 'nt':
        signal.signal(signal.SIGINT, handle_sigterm)
        
    try:
        process.wait()
    except KeyboardInterrupt:
        handle_sigterm(None, None)

if __name__ == "__main__":
    main()
