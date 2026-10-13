import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from installer.app.core import models


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class ModelZipTests(unittest.TestCase):
    def download(self, root, content, expected_files, archive_hash=None, existing=None, cancel=False):
        target = Path(root) / "models" / "test-model"
        target.mkdir(parents=True)
        if existing:
            for name, payload in existing.items():
                (target / name).write_bytes(payload)
        manifest = {
            "id": "test-model", "folder": "test-model", "files": list(expected_files),
            "download_url": "https://example.test/test-model.zip", "archive_format": "zip",
            "archive_sha256": archive_hash or hashlib.sha256(content).hexdigest(),
            "sha256": {name: hashlib.sha256(payload).hexdigest() for name, payload in expected_files.items()},
        }
        class Response(io.BytesIO):
            def read(self, size=-1):
                chunk = super().read(size)
                if cancel:
                    models.config.DOWNLOADING_MODELS["test-service:test-model"]["cancel"] = True
                return chunk
        with patch.object(models.config, "find_manifest", return_value=({"models_path": "models"}, "")), \
             patch.object(models.config, "SHOULD_EXIT", False), \
             patch.object(models, "_remote_size", return_value=len(content)), \
             patch.object(models.urllib.request, "urlopen", return_value=Response(content)) as request:
            models.run_model_download("test-service", root, manifest)
        self.assertNotIn("test-service:test-model", models.config.DOWNLOADING_MODELS)
        return target, request.call_count

    def test_complete_archive_is_installed_with_original_filenames(self):
        files = {"model.bin": b"weights", "config.json": b"{}"}
        with tempfile.TemporaryDirectory() as root:
            target, calls = self.download(root, zip_bytes(files), files)
            self.assertEqual(calls, 1)
            self.assertEqual({p.name for p in target.iterdir()}, set(files))
            for name, payload in files.items():
                self.assertEqual((target / name).read_bytes(), payload)

    def test_invalid_archive_hash_preserves_installed_files(self):
        with tempfile.TemporaryDirectory() as root:
            target, _ = self.download(root, zip_bytes({"model.bin": b"new"}), {"model.bin": b"new"},
                                      archive_hash="0" * 64, existing={"model.bin": b"old"})
            self.assertEqual((target / "model.bin").read_bytes(), b"old")
            self.assertEqual([p.name for p in target.iterdir()], ["model.bin"])

    def test_bad_inner_file_hash_does_not_partially_replace_model(self):
        with tempfile.TemporaryDirectory() as root:
            target, _ = self.download(root, zip_bytes({"model.bin": b"new", "config.json": b"bad"}),
                                      {"model.bin": b"new", "config.json": b"{}"}, existing={"model.bin": b"old"})
            self.assertEqual((target / "model.bin").read_bytes(), b"old")
            self.assertFalse((target / "config.json").exists())

    def test_path_traversal_and_missing_files_are_rejected(self):
        for files in ({"../outside.bin": b"weights"}, {"other.bin": b"weights"}):
            with self.subTest(files=files), tempfile.TemporaryDirectory() as root:
                target, _ = self.download(root, zip_bytes(files), {"model.bin": b"weights"})
                self.assertEqual(list(target.iterdir()), [])
                self.assertFalse((Path(root) / "models" / "outside.bin").exists())

    def test_cancellation_cleans_partial_archive(self):
        files = {"model.bin": b"weights"}
        with tempfile.TemporaryDirectory() as root:
            target, _ = self.download(root, zip_bytes(files), files, cancel=True)
            self.assertEqual(list(target.iterdir()), [])

    def test_verified_existing_package_skips_archive_download(self):
        files = {"model.bin": b"weights", "config.json": b"{}"}
        with tempfile.TemporaryDirectory() as root:
            _, calls = self.download(root, zip_bytes(files), files, existing=files)
            self.assertEqual(calls, 0)
