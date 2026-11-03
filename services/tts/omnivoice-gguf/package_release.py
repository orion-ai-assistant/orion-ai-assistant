"""
OmniVoice-GGUF Release Packaging Tool (CUDA Only)
=================================================
Prepares the CUDA distribution zip and SHA256 checksum for GitHub Releases.

Note: the CPU-only set is bundled directly in the Git repository (bin/, ~2.4 MB).
The CUDA set (build/cuda/ from build_release.bat: its own exes and ggml*.dll next to
ggml-cuda.dll, ~370 MB uncompressed) is distributed via GitHub Releases.

Zero external dependencies (Python standard library only).
"""

import os
import sys
import zipfile
import hashlib

# A whole set: ggml.dll is linked to the backends it was built with, so the CPU-only ggml.dll in bin/ cannot load
# ggml-cuda.dll and the zip brings its own (and the exes and DLLs built with it).
CUDA_FILES = [
    "tts-server.exe",
    "omnivoice-codec.exe",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cpu.dll",
    "ggml-cuda.dll",
]

ZIP_NAME = "omnivoice-gguf-windows-cuda.zip"


def calculate_sha256(filepath: str) -> str:
    """Calculate SHA256 hash of a file efficiently."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):  # 1MB chunks
            sha.update(chunk)
    return sha.hexdigest()


def package_cuda_release():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(root_dir, "build", "cuda")

    print("=" * 65)
    print(" OmniVoice-GGUF CUDA Release Packaging Tool")
    print(f" Source directory: {build_dir}")
    print("=" * 65)

    # 1. Verify the whole set is there
    missing = [name for name in CUDA_FILES if not os.path.exists(os.path.join(build_dir, name))]
    if missing:
        print(f"[ERROR] Missing in {build_dir}: {', '.join(missing)}")
        print("  Please build it first using build_release.bat cuda")
        sys.exit(1)

    # 2. Package CUDA Zip
    zip_path = os.path.join(root_dir, ZIP_NAME)
    print(f"\n[*] Creating {ZIP_NAME} ...")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in CUDA_FILES:
            path = os.path.join(build_dir, name)
            print(f"    + {name} ({os.path.getsize(path) / (1024 * 1024):.2f} MB)")
            zf.write(path, arcname=name)

    size = os.path.getsize(zip_path)
    total_size_mb = size / (1024 * 1024)
    print(f"    -> Generated: {zip_path} ({total_size_mb:.2f} MB)")

    # 3. Calculate Checksum
    sha = calculate_sha256(zip_path)
    print(f"    -> SHA256: {sha}")

    # 4. Write checksums.sha256
    checksum_file = os.path.join(root_dir, "checksums.sha256")
    with open(checksum_file, "w", encoding="utf-8") as f:
        f.write(f"{sha}  {ZIP_NAME}\n")

    print("\n" + "=" * 65)
    print(" PACKAGING COMPLETE! Created artifacts:")
    print("=" * 65)
    print(f"  1. {ZIP_NAME} ({total_size_mb:.2f} MB)")
    print(f"     SHA256: {sha}")
    print(f"     size:   {size}")
    print(f"     -> Upload this zip to the next tts-v... GitHub Release and put its url, sha256 and size")
    print(f"        into services/shared/local_parts.json (omnivoice-gguf -> windows -> cuda).")
    print(f"  2. checksums.sha256 ({os.path.getsize(checksum_file)} bytes)")
    print(f"     -> Committed directly into Git repo (no upload needed)")
    print("=" * 65)


if __name__ == "__main__":
    package_cuda_release()
