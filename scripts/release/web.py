"""Reading the public feeds and inputs over HTTPS, verified by Windows itself (services/shared/windows_trust.py)."""
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.windows_trust import context  # noqa: E402


def read(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout, context=context()) as response:
        return response.read()


def download(url, target, timeout=60):
    """Streams `url` into the new file `target` (it must not exist)."""
    with urllib.request.urlopen(url, timeout=timeout, context=context()) as response, Path(target).open('xb') as output:
        while chunk := response.read(1 << 20):
            output.write(chunk)
