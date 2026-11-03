"""The zip of the STT engine that Orion builds itself (whisper.cpp's own release has no Vulkan build for
Windows), with the SHA-256 and size local_parts.json needs.

    python package.py      whisper-server and its DLLs from build_vulkan.bat (%TEMP%\\wcpp-vk\\bin)

It goes to the `stt-whispercpp-<version>` release. Everything else the part needs comes from where its maker
publishes it (docs/bilesenler.md): CUDA and CPU from whisper.cpp's release, the models and the VAD from Hugging Face.
"""
import hashlib
import json
import os
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
VERSION = 'v1.9.5'
SOURCE = Path(os.environ['TEMP']) / 'wcpp'
BUILT = Path(os.environ['TEMP']) / 'wcpp-vk' / 'bin'
FILES = ['whisper-server.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll', 'ggml-vulkan.dll']


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def vulkan():
    target = HERE / f'whisper-cpp-{VERSION}-windows-vulkan.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in FILES:
            bundle.write(BUILT / name, name)
        bundle.write(SOURCE / 'LICENSE', 'LICENSE-whisper.cpp.txt')
    return target


if __name__ == '__main__':
    made = vulkan()
    print(json.dumps({'file': made.name, 'sha256': sha256(made), 'size': made.stat().st_size}, indent=1))
