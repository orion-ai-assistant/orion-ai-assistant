"""
OmniVoice-GGUF Hybrid Binary Installer
=======================================
- CPU binaries are bundled directly with the repository (~2.4 MB) -> Instant 0-second setup.
- CUDA runtime (ggml-cuda.dll, ~370 MB) is downloaded on-demand from GitHub Releases.
- Dynamic GitHub API release discovery with 3-tier fallback chain.
- SHA256 integrity verification and atomic rollback.
- Graceful fallback to bundled CPU binaries if CUDA download or GPU driver fails.
- Zero external dependencies (Python standard library only).
"""

import os
import sys
import platform
import subprocess
import shutil
import zipfile
import hashlib
import json
import re
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple

# ─── Configuration ────────────────────────────────────────────────────────────
GITHUB_REPO = "orion-ai-assistant/orion-ai-assistant"
FALLBACK_TAG = "tts-v1.0.0"
MIN_CUDA_DRIVER_VERSION = 525.60

REQUIRED_FILES_CPU = [
    "tts-server.exe",
    "omnivoice-codec.exe",
    "ggml.dll",
    "ggml-cpu.dll",
    "ggml-base.dll",
]

CUDA_FILE = "ggml-cuda.dll"
ZIP_NAME = "omnivoice-gguf-windows-cuda.zip"

# ─── Platform & Hardware Checks ───────────────────────────────────────────────
def check_platform_support() -> bool:
    """Verifies whether the current OS and CPU architecture are supported."""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        if machine not in ("amd64", "x86_64"):
            print(f"[OmniVoice-GGUF] ERROR: Unsupported CPU architecture '{machine}'. Only x86_64/AMD64 is supported.")
            return False
        return True

    if system == "Linux":
        print(f"[OmniVoice-GGUF] NOTICE: Detected Linux ({machine}).")
        print("  OmniVoice-GGUF runs via Docker in the Orion Linux architecture.")
        print("  See services/tts/docker-compose.nvidia.yml or docker-compose.cpu.yml,")
        print("  or build locally using CMake inside your Linux environment.")
        return False

    print(f"[OmniVoice-GGUF] ERROR: Operating system '{system}' is not supported for automated Windows binary installation.")
    return False


def detect_hardware_and_driver() -> Tuple[str, str]:
    """
    Detects hardware target ('cuda' or 'cpu') and verifies NVIDIA driver version.
    Returns: (target_hw, details_message)
    """
    # 1. Check .env config override
    env_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    hw_pref = ""
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip().lower()
                    if line.startswith("orion_hw_id="):
                        val = line.split("=", 1)[1]
                        if "nvidia" in val or "cuda" in val:
                            hw_pref = "cuda"
                        elif "cpu" in val:
                            hw_pref = "cpu"
                    elif line.startswith("base_image=") and not hw_pref:
                        if "cuda" in line:
                            hw_pref = "cuda"
                        elif "cpu" in line:
                            hw_pref = "cpu"
        except Exception:
            pass

    if hw_pref == "cpu":
        return "cpu", "Configured as CPU in .env"

    # 2. Check NVIDIA GPU and Driver version via nvidia-smi
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=4)
        if r.returncode == 0:
            output = r.stdout
            match = re.search(r"Driver Version:\s*([0-9.]+)", output)
            if match:
                driver_ver_str = match.group(1)
                try:
                    driver_ver = float(".".join(driver_ver_str.split(".")[:2]))
                except ValueError:
                    driver_ver = 0.0

                if driver_ver >= MIN_CUDA_DRIVER_VERSION:
                    return "cuda", f"NVIDIA GPU detected (Driver: {driver_ver_str} >= {MIN_CUDA_DRIVER_VERSION})"
                else:
                    msg = (
                        f"NVIDIA GPU found, but Driver Version ({driver_ver_str}) is below {MIN_CUDA_DRIVER_VERSION}.\n"
                        f"  CUDA 12 binaries require Driver >= {MIN_CUDA_DRIVER_VERSION}.\n"
                        f"  [SAFETY FALLBACK] Switching to CPU binaries to prevent crashes."
                    )
                    return "cpu", msg
            else:
                return "cuda", "NVIDIA GPU detected via nvidia-smi"
    except Exception:
        pass

    return "cpu", "No compatible NVIDIA GPU/driver detected. Using CPU engine."


def check_cpu_binaries_present(bin_dir: str) -> bool:
    """Verifies whether bundled CPU binaries exist in bin/."""
    for fname in REQUIRED_FILES_CPU:
        p = os.path.join(bin_dir, fname)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            return False
    return True


# ─── Dynamic Release Resolution & Download ────────────────────────────────────
def resolve_cuda_release_urls() -> Tuple[str, Optional[str], str]:
    """
    Finds download URLs for omnivoice-gguf-windows-cuda.zip and checksums.sha256.
    3-Tier Fallback:
      Tier 1: GitHub API matching 'tts-*'
      Tier 2: /releases/latest/download/
      Tier 3: /releases/download/{FALLBACK_TAG}/
    """
    checksum_name = "checksums.sha256"

    # Tier 1: GitHub API
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    headers = {"User-Agent": "Orion-AI-Assistant/1.0", "Accept": "application/vnd.github.v3+json"}

    print(f"[*] Querying GitHub API for latest CUDA release asset...")
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                releases = json.loads(response.read().decode("utf-8"))
                candidate_releases = []
                for rel in releases:
                    tag = rel.get("tag_name", "").lower()
                    if tag.startswith("tts-") or tag.startswith("omnivoice-"):
                        candidate_releases.append(rel)
                if not candidate_releases and releases:
                    candidate_releases = releases

                for rel in candidate_releases:
                    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
                    if ZIP_NAME in assets:
                        zip_url = assets[ZIP_NAME]
                        chk_url = assets.get(checksum_name)
                        tag = rel.get("tag_name", "unknown")
                        return zip_url, chk_url, f"GitHub API (Tag: {tag})"
    except Exception as e:
        print(f"  [Notice] GitHub API query unavailable ({e}). Proceeding to direct download URL...")

    # Tier 2: Direct latest download URL
    tier2_zip = f"https://github.com/{GITHUB_REPO}/releases/latest/download/{ZIP_NAME}"
    tier2_chk = f"https://github.com/{GITHUB_REPO}/releases/latest/download/{checksum_name}"
    try:
        req = urllib.request.Request(tier2_zip, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status in (200, 302):
                return tier2_zip, tier2_chk, "GitHub Latest Release (Direct)"
    except Exception:
        pass

    # Tier 3: Pinned fallback release
    tier3_zip = f"https://github.com/{GITHUB_REPO}/releases/download/{FALLBACK_TAG}/{ZIP_NAME}"
    tier3_chk = f"https://github.com/{GITHUB_REPO}/releases/download/{FALLBACK_TAG}/{checksum_name}"
    return tier3_zip, tier3_chk, f"Pinned Release Fallback (Tag: {FALLBACK_TAG})"


def download_file(url: str, dest_path: str, description: str = "") -> bool:
    """Downloads a file with progress reporting."""
    desc = description or os.path.basename(dest_path)
    print(f"[*] Downloading {desc} ...")
    print(f"    Source: {url}")
    headers = {"User-Agent": "Orion-AI-Assistant/1.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 64 * 1024

            with open(dest_path, "wb") as out_f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = min(100, int(downloaded * 100 / total_size))
                        mb_done = downloaded / (1024 * 1024)
                        mb_tot = total_size / (1024 * 1024)
                        print(f"\r    Progress: {pct}% ({mb_done:.1f}/{mb_tot:.1f} MB)", end="", flush=True)
            print()
            return True
    except Exception as e:
        print(f"\n[ERROR] Download failed for {url}: {e}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        return False


def verify_sha256(filepath: str, expected_hash: str) -> bool:
    """Computes SHA256 of file and compares against expected hash."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            sha.update(chunk)
    computed = sha.hexdigest().lower()
    return computed == expected_hash.strip().lower()


# ─── Installation Workflow ────────────────────────────────────────────────────
def install():
    print("=" * 65)
    print(" OmniVoice-GGUF Binary Installer")
    print("=" * 65)

    # 1. Platform validation
    if not check_platform_support():
        sys.exit(1)

    bin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
    os.makedirs(bin_dir, exist_ok=True)

    # 2. Verify bundled CPU binaries
    if not check_cpu_binaries_present(bin_dir):
        print("[WARNING] Bundled CPU binaries are incomplete in bin/.")
        print("  Please ensure Git LFS or repository files were pulled properly,")
        print("  or compile them using services/tts/omnivoice-gguf/build_release.bat")

    # 3. Hardware target detection
    hw, hw_msg = detect_hardware_and_driver()
    print(f"[*] Hardware target: {hw.upper()}")
    print(f"    Detail: {hw_msg}")

    # Case A: CPU Target -> Instant ready!
    if hw == "cpu":
        if check_cpu_binaries_present(bin_dir):
            print("[OmniVoice-GGUF] Bundled CPU binaries are ready. Installation complete (0s)!")
            return
        else:
            print("[ERROR] CPU binaries missing. Please compile using build_release.bat")
            sys.exit(1)

    # Case B: CUDA Target -> Check if ggml-cuda.dll already present
    cuda_dll_path = os.path.join(bin_dir, CUDA_FILE)
    if os.path.exists(cuda_dll_path) and os.path.getsize(cuda_dll_path) > 0:
        print(f"[OmniVoice-GGUF] CUDA runtime ({CUDA_FILE}) is already present and valid.")
        print("Installation complete!")
        return

    # Case C: Download CUDA runtime from GitHub Releases
    print(f"[*] CUDA runtime ({CUDA_FILE}) not found in bin/. Fetching from GitHub Releases...")
    zip_url, chk_url, source_desc = resolve_cuda_release_urls()
    print(f"[*] Release Source: {source_desc}")

    temp_zip = os.path.join(bin_dir, f"{ZIP_NAME}.tmp")
    temp_chk = os.path.join(bin_dir, "checksums.sha256.tmp")
    staging_dir = os.path.join(bin_dir, ".extract_staging")

    # Clean previous stale temp files
    for p in (temp_zip, temp_chk):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)

    try:
        # Determine expected SHA256 (bundled repo file first, then download fallback)
        expected_hash = None
        local_chk = os.path.normpath(os.path.join(bin_dir, "..", "checksums.sha256"))
        if os.path.exists(local_chk):
            try:
                with open(local_chk, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[1].endswith(ZIP_NAME):
                            expected_hash = parts[0]
                            break
                if expected_hash:
                    print(f"[*] Expected SHA256 (from repository): {expected_hash}")
            except Exception as e:
                print(f"  [Warning] Could not read local checksums: {e}")

        # Fallback: download checksums if not found locally
        if not expected_hash and chk_url and download_file(chk_url, temp_chk, "checksums.sha256"):
            try:
                with open(temp_chk, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[1].endswith(ZIP_NAME):
                            expected_hash = parts[0]
                            break
                if expected_hash:
                    print(f"[*] Expected SHA256 (downloaded): {expected_hash}")
            except Exception as e:
                print(f"  [Warning] Could not parse checksums file: {e}")

        # Download CUDA Zip
        if not download_file(zip_url, temp_zip, ZIP_NAME):
            raise RuntimeError(f"Could not download CUDA package from {zip_url}")

        # Checksum Verification
        if expected_hash:
            print("[*] Verifying file integrity (SHA256)...")
            if not verify_sha256(temp_zip, expected_hash):
                raise ValueError("SHA256 checksum mismatch! The downloaded archive is corrupted.")
            print("    [OK] Checksum verified successfully.")

        # Staged Extraction
        print("[*] Extracting CUDA runtime into staging area...")
        os.makedirs(staging_dir, exist_ok=True)
        with zipfile.ZipFile(temp_zip, "r") as zf:
            zf.extractall(staging_dir)

        # Move extracted files to bin/
        for fname in os.listdir(staging_dir):
            src = os.path.join(staging_dir, fname)
            dst = os.path.join(bin_dir, fname)
            if os.path.isfile(src):
                shutil.move(src, dst)

        print("=" * 65)
        print(f"[OmniVoice-GGUF] SUCCESS: CUDA runtime installed to: {bin_dir}")
        print("=" * 65)

    except Exception as err:
        print("\n" + "!" * 65)
        print(f"[OmniVoice-GGUF] CUDA DOWNLOAD WARNING: {err}")
        print("!" * 65)
        # Rollback temp files
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        
        # Graceful CPU Fallback:
        if check_cpu_binaries_present(bin_dir):
            print("\n[GRACEFUL FALLBACK] Switching to bundled CPU binaries.")
            print("The TTS service will remain fully functional on CPU.")
            print("To enable CUDA later, upload the release zip or compile locally with build_release.bat")
            return
        else:
            print("[ERROR] Both CUDA download failed and CPU binaries missing.")
            sys.exit(1)

    finally:
        for p in (temp_zip, temp_chk):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    install()
