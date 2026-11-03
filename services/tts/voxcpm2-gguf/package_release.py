"""
VoxCPM2 (GGUF) engine packaging: one zip per set that build_release.bat left in build\\<cpu|vulkan|cuda>.

Each zip is a whole set (llama-tts-server.exe and every DLL next to it), since ggml.dll is linked to the
backend it was built with. The CUDA set needs CUDA's runtime besides (cudart, cuBLAS): the catalog brings it
as llama.cpp's cudart-llama-bin-win-cuda-12.4-x64.zip, the same as OmniVoice's CUDA recipe.

Prints the url, sha256 and size for services/shared/local_parts.json (engines -> voxcpm2-gguf -> windows).
Zero external dependencies (Python standard library only).
"""

import hashlib
import json
import os
import sys
import zipfile

RELEASE_TAG = "voxcpm2-v1.0.0"
BASE_URL = f"https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/{RELEASE_TAG}/"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    wanted = sys.argv[1:] or ["cpu", "vulkan", "cuda"]
    out = {}
    for name in wanted:
        folder = os.path.join(root, "build", name)
        if not os.path.isfile(os.path.join(folder, "llama-tts-server.exe")):
            print(f"[ERROR] {folder} has no llama-tts-server.exe; run build_release.bat {name} first")
            sys.exit(1)
        zip_name = f"voxcpm2-gguf-windows-{name}.zip"
        zip_path = os.path.join(root, zip_name)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for file in sorted(os.listdir(folder)):
                zf.write(os.path.join(folder, file), arcname=file)
        out[name] = {"url": BASE_URL + zip_name, "sha256": sha256(zip_path), "size": os.path.getsize(zip_path)}
        print(f"{zip_name}: {out[name]['size'] / (1024 * 1024):.2f} MB, sha256 {out[name]['sha256']}")
    print(json.dumps(out, indent=2))
    print(f"-> Upload the zips to the {RELEASE_TAG} GitHub release, then put these into local_parts.json.")


if __name__ == "__main__":
    main()
