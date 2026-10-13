import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer.app.core import models


class ModelChecksumTests(unittest.TestCase):
    def test_whisper_release_catalog_has_complete_checksums_and_no_fp32(self):
        path = Path(__file__).resolve().parents[2] / "services/stt/whisper/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        compute = manifest["parameters"]["WHISPER_COMPUTE_TYPE"]
        self.assertNotIn("float32", compute["options"])
        self.assertEqual(compute["hardware_options"]["cpu"], ["int8"])
        for model in manifest["models_catalog"]:
            if model["id"] not in ("whisper-small-official", "whisper-large-v3-turbo-ct2"):
                continue
            self.assertEqual(set(model["sha256"]), set(model["files"]))
            self.assertTrue(model["download_url"].startswith(
                "https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/"))
            self.assertIn("LICENSE", model["files"])
            self.assertEqual(model["archive_format"], "zip")
            self.assertTrue(model["download_url"].endswith(".zip"))
            self.assertEqual(len(model["archive_sha256"]), 64)

    def test_turkish_finetune_is_listed_as_a_local_model(self):
        manifest_path = Path(__file__).resolve().parents[2] / "services/stt/whisper/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as root:
            service = Path(root)
            folder = service / "models" / "whisper-small-finetuned-tr"
            folder.mkdir(parents=True)
            (folder / "model.bin").write_bytes(b"local weights")
            with patch.object(models.config, "find_manifest", return_value=(manifest, str(service / "manifest.json"))):
                entries = models.check_models("whisper-stt")
            local = next(entry for entry in entries if entry["id"] == "whisper-small-finetuned-tr")
            self.assertEqual(local["type"], "local")
            self.assertTrue(local["is_installed"])

    def download(self, root, payload, expected_payload, existing=None):
        destination = Path(root) / "models" / "test-model" / "model.bin"
        if existing is not None:
            destination.parent.mkdir(parents=True)
            destination.write_bytes(existing)
        manifest = {
            "id": "test-model", "folder": "test-model", "files": ["model.bin"],
            "download_url": "https://example.test/",
            "sha256": {"model.bin": hashlib.sha256(expected_payload).hexdigest()},
        }
        with patch.object(models.config, "find_manifest", return_value=({"models_path": "models"}, "")), \
             patch.object(models.config, "SHOULD_EXIT", False), \
             patch.object(models, "_remote_size", return_value=len(payload)), \
             patch.object(models.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as request:
            models.run_model_download("test-service", root, manifest)
        return destination, request.call_count

    def test_valid_download_is_installed(self):
        with tempfile.TemporaryDirectory() as root:
            destination, calls = self.download(root, b"good", b"good")
            self.assertEqual(destination.read_bytes(), b"good")
            self.assertEqual(calls, 1)

    def test_same_size_corrupt_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            destination, _ = self.download(root, b"evil", b"good")
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".downloading").exists())

    def test_existing_same_size_corrupt_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            destination, calls = self.download(root, b"good", b"good", existing=b"evil")
            self.assertEqual(calls, 1)
            self.assertEqual(destination.read_bytes(), b"good")

    def test_existing_verified_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            destination, calls = self.download(root, b"good", b"good", existing=b"good")
            self.assertEqual(calls, 0)
            self.assertEqual(destination.read_bytes(), b"good")
