"""
OmniVoice-GGUF Release Packaging Tool
======================================
Prepares standalone zip distributions and SHA256 checksums from compiled
Release binaries in bin/ directory for publishing to GitHub Releases.

Zero external dependencies (uses standard library: os, sys, zipfile, hashlib).
"""

import os
import sys
import zipfile
import hashlib
from typing import List, Dict

# Platform & package definitions
CPU_FILES = [
    "tts-server.exe",
    "omnivoice-codec.exe",
    "ggml.dll",
    "ggml-cpu.dll",
    "ggml-base.dll",
]

CUDA_FILES = [
    "tts-server.exe",
    "omnivoice-codec.exe",
    "ggml.dll",
    "ggml-cpu.dll",
    "ggml-base.dll",
    "ggml-cuda.dll",
]

PACKAGES = {
    "omnivoice-gguf-windows-cpu.zip": CPU_FILES,
    "omnivoice-gguf-windows-cuda.zip": CUDA_FILES,
}


def calculate_sha256(filepath: str) -> str:
    """Calculate SHA256 hash of a file efficiently."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):  # 1MB chunks
            sha.update(chunk)
    return sha.hexdigest()


def check_is_debug_binary(filepath: str) -> bool:
    """Quick check to alert if a binary was mistakenly linked against Debug CRT."""
    try:
        with open(filepath, "rb") as f:
            content = f.read(1024 * 1024)  # First 1MB usually contains import tables
            # Look for debug runtime indicators
            if b"ucrtbased.dll" in content or b"msvcp140d.dll" in content:
                return True
    except Exception:
        pass
    return False


def package_binaries():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    bin_dir = os.path.join(root_dir, "bin")

    print("=" * 65)
    print(" OmniVoice-GGUF Release Packaging Tool")
    print(f" Source directory: {bin_dir}")
    print("=" * 65)

    if not os.path.exists(bin_dir):
        print(f"[ERROR] Binary directory does not exist: {bin_dir}")
        sys.exit(1)

    # 1. Verify existence of all required files
    all_needed = set(CPU_FILES + CUDA_FILES)
    missing = [f for f in all_needed if not os.path.exists(os.path.join(bin_dir, f))]
    if missing:
        print(f"[ERROR] Missing required binaries in bin/: {', '.join(missing)}")
        print("  Please build them first using build_release.bat")
        sys.exit(1)

    # 2. Check for debug CRT
    for f in all_needed:
        full_path = os.path.join(bin_dir, f)
        if check_is_debug_binary(full_path):
            print(f"[WARNING] {f} appears to be compiled in DEBUG mode (slow).")
            print("  Please ensure you ran a Release build.")

    # 3. Create Zip Archives
    checksums: Dict[str, str] = {}

    for zip_name, file_list in PACKAGES.items():
        zip_path = os.path.join(root_dir, zip_name)
        print(f"\n[*] Creating {zip_name} ...")

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for fname in file_list:
                src_path = os.path.join(bin_dir, fname)
                fsize_mb = os.path.getsize(src_path) / (1024 * 1024)
                print(f"    + {fname} ({fsize_mb:.2f} MB)")
                zf.write(src_path, arcname=fname)

        total_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        print(f"    -> Package generated: {zip_path} ({total_size_mb:.2f} MB)")

        # Calculate checksum
        sha = calculate_sha256(zip_path)
        checksums[zip_name] = sha
        print(f"    -> SHA256: {sha}")

    # 4. Write checksums.sha256 in standard format
    checksum_file = os.path.join(root_dir, "checksums.sha256")
    with open(checksum_file, "w", encoding="utf-8") as f:
        for zip_name, sha in checksums.items():
            f.write(f"{sha}  {zip_name}\n")

    print("\n" + "=" * 65)
    print(" PACKAGING COMPLETE! Created artifacts for GitHub Release:")
    print("=" * 65)
    for zip_name, sha in checksums.items():
        zip_path = os.path.join(root_dir, zip_name)
        sz = os.path.getsize(zip_path) / (1024 * 1024)
        print(f"  1. {zip_name} ({sz:.2f} MB)")
        print(f"     SHA256: {sha}")
    print(f"  2. checksums.sha256 ({os.path.getsize(checksum_file)} bytes)")
    print("=" * 65)
    print("Ready to upload these files to your GitHub Release (e.g. tag: tts-v1.0.0).")


if __name__ == "__main__":
    package_binaries()
