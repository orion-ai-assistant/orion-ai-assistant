"""Download fixed build inputs, verify bytes before publishing them to cache."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared.release_packages import sha256


def download(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for value in json.loads(Path(__file__).with_name('inputs.json').read_text()).values():
        path = destination / value['filename']
        if path.exists():
            if sha256(path) != value['sha256']:
                raise ValueError('Cached build input hash mismatch')
            continue
        with tempfile.TemporaryDirectory(dir=destination) as temporary:
            target = Path(temporary) / value['filename']
            with urllib.request.urlopen(value['url'], timeout=60) as response, target.open('xb') as output:
                import shutil
                shutil.copyfileobj(response, output)
            if sha256(target) != value['sha256']:
                raise ValueError('Downloaded build input hash mismatch')
            target.rename(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    download(parser.parse_args().destination)
