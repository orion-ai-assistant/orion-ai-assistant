"""Shared native video tools installer. Release integrity is pinned in source."""
import argparse
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "services/llm/llama-cpp/bin/ffmpeg"
URL = "https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/ffmpeg-v9.0.2/orion-ffmpeg-9.0.2-windows-x64.zip"
SHA256 = "1a69e2301c0abaaf536275f5b8dbca1b14c1f75908b1e49c2694125df48a6a50"
BINARY_HASHES = {
    "ffmpeg.exe": "3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec",
    "ffprobe.exe": "f0d36ecbbdd3bcfac3efa078c96c7271c2e68b3810595552ac3b7f17e9a65c52",
}
FILES = (*BINARY_HASHES, "LICENSE", "UPSTREAM-README.txt", "THIRD_PARTY_NOTICES.txt", "INSTALL.txt", "BUILD-INFO.txt")


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed(destination):
    return all((destination / name).is_file() and digest(destination / name) == value
               for name, value in BINARY_HASHES.items())


def check_tools(destination):
    for name in BINARY_HASHES:
        subprocess.run([str(destination / name), "-version"], check=True,
                       capture_output=True, timeout=30)


@contextmanager
def installation_lock(destination):
    # OS lock is released even if an installer crashes. Hub and llama may install concurrently.
    import msvcrt
    lock_path = destination.parent / ".ffmpeg-install.lock"
    with open(lock_path, "a+b") as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + 600
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another FFmpeg installation is still running")
                time.sleep(0.25)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def ensure_ffmpeg(destination=DESTINATION):
    if os.name != "nt":
        # Windows binaries cannot serve Linux containers or native Unix processes.
        if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
            raise RuntimeError("Install ffmpeg and ffprobe using your OS package manager first")
        return None
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("The pinned FFmpeg package requires Windows x64")
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with installation_lock(destination):
        if installed(destination):
            check_tools(destination)
            print(f"FFmpeg + FFprobe already installed: {destination}", flush=True)
            return destination
        with tempfile.TemporaryDirectory(prefix=".ffmpeg-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            archive = staging / "download.zip"
            print(f"Downloading FFmpeg: {URL}", flush=True)
            request = urllib.request.Request(URL, headers={"User-Agent": "Orion-Installer"})
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            actual = digest(archive)
            if actual != SHA256:
                raise ValueError(f"FFmpeg SHA256 mismatch: expected {SHA256}, received {actual}. Installation stopped.")
            with zipfile.ZipFile(archive) as package:
                # Only known flat files are extracted; arbitrary archive paths are never used.
                for name in FILES:
                    with package.open(name) as source, (staging / name).open("wb") as target:
                        shutil.copyfileobj(source, target)
            if not installed(staging):
                raise ValueError("FFmpeg executable hashes do not match the pinned release")
            check_tools(staging)
            destination.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                os.replace(staging / name, destination / name)
            print(f"FFmpeg + FFprobe installed and verified: {destination}", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    ensure_ffmpeg(parser.parse_args().destination)
