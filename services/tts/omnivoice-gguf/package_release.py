"""
OmniVoice-GGUF Release Packaging Tool (CUDA Only)
=================================================
Prepares the CUDA distribution zip and SHA256 checksum for GitHub Releases.

Note: CPU binaries are already bundled directly in the Git repository (~2.4 MB).
Only the heavy CUDA runtime (ggml-cuda.dll, ~370 MB uncompressed, ~127 MB compressed)
is distributed via GitHub Releases.

Zero external dependencies (Python standard library only).
"""

import os
import sys
import zipfile
import hashlib

CUDA_FILES = [
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
    bin_dir = os.path.join(root_dir, "bin")

    print("=" * 65)
    print(" OmniVoice-GGUF CUDA Release Packaging Tool")
    print(f" Source directory: {bin_dir}")
    print("=" * 65)

    if not os.path.exists(bin_dir):
        print(f"[ERROR] Binary directory does not exist: {bin_dir}")
        sys.exit(1)

    # 1. Verify existence of ggml-cuda.dll
    cuda_path = os.path.join(bin_dir, "ggml-cuda.dll")
    if not os.path.exists(cuda_path):
        print(f"[ERROR] Missing ggml-cuda.dll in {bin_dir}")
        print("  Please build it first using build_release.bat")
        sys.exit(1)

    # 2. Package CUDA Zip
    zip_path = os.path.join(root_dir, ZIP_NAME)
    print(f"\n[*] Creating {ZIP_NAME} ...")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        fsize_mb = os.path.getsize(cuda_path) / (1024 * 1024)
        print(f"    + ggml-cuda.dll ({fsize_mb:.2f} MB)")
        zf.write(cuda_path, arcname="ggml-cuda.dll")

    total_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
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
    print(f"     -> Upload ONLY this zip to GitHub Release (Tag: tts-v1.0.0)")
    print(f"  2. checksums.sha256 ({os.path.getsize(checksum_file)} bytes)")
    print(f"     -> Committed directly into Git repo (no upload needed)")
    print("=" * 65)


if __name__ == "__main__":
    package_cuda_release()
