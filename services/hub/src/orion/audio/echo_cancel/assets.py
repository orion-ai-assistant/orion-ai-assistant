"""Echo-cancel model files: downloaded from their upstream publishers, integrity pinned in source.

The models (46 MB) are not committed; `ensure_models` fetches the exact pinned revisions into the
Hub's data folder (`<hub data>/echo_cancel/models`) the first time the PC assistant is armed, or
ahead of time from the Hub installer. `localvqe.dll` is small and has no upstream Windows build,
so it ships in `native/` (built by `services/hub/vendor/localvqe/build_localvqe.cmd`).
"""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import threading
import urllib.request

_DTLN = "https://raw.githubusercontent.com/breizhn/DTLN-aec/9d24e128b4f409db18227b8babb343016625921f/pretrained_models/"
_LOCALVQE = "https://huggingface.co/LocalAI-io/LocalVQE/resolve/29ca38495cba9d6393a92a4dd890f28dd81f758d/"
MODELS = {
    "dtln_aec_512_1.tflite": (_DTLN + "dtln_aec_512_1.tflite",
                              "569f7c3cfac96b1e093229c3ca10b5d892f5b1906105b644e457f8245b4f7383"),
    "dtln_aec_512_2.tflite": (_DTLN + "dtln_aec_512_2.tflite",
                              "fb423d867ab25d5f4716bd369c7126b6c84926175c019b832e71bf21de0e9907"),
    "localvqe-v1.2-1.3M-f32.gguf": (_LOCALVQE + "localvqe-v1.2-1.3M-f32.gguf",
                                    "4856ecf5f522b23fb2bc5caeac81f323c0ef1c4c156a9c7d40a6adbe092ba9ce"),
}
LOCALVQE_MODEL = "localvqe-v1.2-1.3M-f32.gguf"
_lock = threading.Lock()


def default_models_dir():
    override = os.environ.get("ORION_ECHO_CANCEL_MODELS")
    if override:
        return Path(override)
    from orion.kernel.data_paths import hub_data
    return hub_data() / "echo_cancel" / "models"


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def missing(models_dir):
    return [name for name, (_, sha) in MODELS.items()
            if not (Path(models_dir) / name).is_file() or digest(Path(models_dir) / name) != sha]


def ensure_models(models_dir=None, opener=urllib.request.urlopen):
    """Download whatever is missing or corrupt; raise if a download does not match its pin."""
    models_dir = Path(models_dir or default_models_dir())
    with _lock:
        todo = missing(models_dir)
        if not todo:
            return models_dir
        models_dir.mkdir(parents=True, exist_ok=True)
        for name in todo:
            url, sha = MODELS[name]
            with tempfile.NamedTemporaryFile(dir=models_dir, prefix=f".{name}.", delete=False) as part:
                request = urllib.request.Request(url, headers={"User-Agent": "Orion-Hub"})
                with opener(request, timeout=60) as response:
                    shutil.copyfileobj(response, part)
            actual = digest(part.name)
            if actual != sha:
                os.unlink(part.name)
                raise ValueError(f"{name}: SHA256 mismatch (expected {sha}, received {actual})")
            os.replace(part.name, models_dir / name)
    return models_dir
