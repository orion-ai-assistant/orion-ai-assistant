import os
import sys
import zipfile
import urllib.request
import shutil
import platform
import subprocess

def download_and_extract_llama_cpp():
    print("Starting Llama.cpp (Embedding) installation for local mode...")
    
    # Define directories
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Sürüm belirleme
    version = "b11026"
    
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
                # Fallback to base_image if orion_hw_id is missing
                elif line.startswith("base_image="):
                    if "cuda" in line:
                        hw_type = "cuda"
                    elif "vulkan" in line:
                        hw_type = "vulkan"
                
    print(f"Hardware detected: {hw_type}")
    
    bin_dir = os.path.join(base_dir, "bin", hw_type)
    if not os.path.exists(bin_dir):
        os.makedirs(bin_dir, exist_ok=True)
        
    exe_path = os.path.join(bin_dir, "llama-server.exe" if os.name == 'nt' else "llama-server")
    if os.path.exists(exe_path):
        print(f"Llama.cpp {hw_type} is already installed at {exe_path}")
        return
        
    if os.name == 'nt':
        if hw_type == "cuda":
            urls = [
                f"https://github.com/ggerganov/llama.cpp/releases/download/{version}/llama-{version}-bin-win-cuda-12.4-x64.zip",
                f"https://github.com/ggerganov/llama.cpp/releases/download/{version}/cudart-llama-bin-win-cuda-12.4-x64.zip"
            ]
        elif hw_type == "vulkan":
            urls = [
                f"https://github.com/ggerganov/llama.cpp/releases/download/{version}/llama-{version}-bin-win-vulkan-x64.zip"
            ]
        else:
            urls = [
                f"https://github.com/ggerganov/llama.cpp/releases/download/{version}/llama-{version}-bin-win-cpu-x64.zip"
            ]
    else:
        print("Linux/Mac auto-download is not fully implemented in this script yet.")
        sys.exit(1)
        
    for url in urls:
        zip_name = url.split("/")[-1]
        zip_path = os.path.join(bin_dir, zip_name)
        
        print(f"Downloading from {url} ...")
        try:
            urllib.request.urlretrieve(url, zip_path)
        except Exception as e:
            print(f"Failed to download: {e}")
            sys.exit(1)
            
        print("Extracting...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(bin_dir)
        except Exception as e:
            print(f"Failed to extract: {e}")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            sys.exit(1)
            
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
    print("Llama.cpp installation complete!")

if __name__ == "__main__":
    download_and_extract_llama_cpp()
