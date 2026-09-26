"""Regression tests for shared video-tool installation and integrity failures."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
import runpy
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("install_ffmpeg", Path(__file__).with_name("install_ffmpeg.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class TtsReleaseTests(unittest.TestCase):
    def test_pinned_release_needs_no_latest_or_api_request(self):
        module = runpy.run_path(str(installer.ROOT / "services/tts/omnivoice-gguf/install.py"))
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network must not be needed")):
            url, _, _ = module["resolve_cuda_release_urls"]()
        self.assertEqual(url, "https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/tts-v1.0.0/omnivoice-gguf-windows-cuda.zip")


@unittest.skipUnless(os.name == "nt", "Windows package installer")
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / "ffmpeg"
        content = {name: ("fixture-" + name).encode() for name in installer.FILES}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            for name, data in content.items():
                package.writestr(installer.FILES[name], data)
            package.writestr("../unexpected.txt", b"must not be extracted")
        self.archive = archive.getvalue()
        hashes = {name: hashlib.sha256(content[name]).hexdigest() for name in installer.BINARY_HASHES}
        self.stack_patch("BINARY_HASHES", hashes)
        self.stack_patch("SHA256", hashlib.sha256(self.archive).hexdigest())
        self.check = self.stack_patch("check_tools")
        self.download = self.stack_patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(self.archive))
        self.stack_patch("platform.machine", return_value="AMD64")

    def stack_patch(self, name, *args, **kwargs):
        parts = name.split(".")
        target = installer
        for part in parts[:-1]:
            target = getattr(target, part)
        patcher = patch.object(target, parts[-1], *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_install_then_reuse_without_download(self):
        installer.ensure_ffmpeg(self.destination)
        installer.ensure_ffmpeg(self.destination)
        self.assertEqual(self.download.call_count, 1)
        self.assertTrue(installer.installed(self.destination))
        self.assertFalse((self.destination.parent / "unexpected.txt").exists())
        self.assertTrue((self.destination / "LICENSE").is_file())
        self.assertTrue((self.destination / "UPSTREAM-README.txt").is_file())
        self.assertFalse((self.destination / installer.ARCHIVE_ROOT).exists())

    def test_missing_or_corrupted_executable_repairs_install(self):
        installer.ensure_ffmpeg(self.destination)
        (self.destination / "ffprobe.exe").unlink()
        installer.ensure_ffmpeg(self.destination)
        (self.destination / "ffmpeg.exe").write_bytes(b"corrupt")
        installer.ensure_ffmpeg(self.destination)
        self.assertEqual(self.download.call_count, 3)
        self.assertTrue(installer.installed(self.destination))

    def test_bad_download_preserves_previous_files(self):
        self.destination.mkdir()
        previous = self.destination / "ffmpeg.exe"
        previous.write_bytes(b"previous installation")
        self.stack_patch("SHA256", "0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            installer.ensure_ffmpeg(self.destination)
        self.assertEqual(previous.read_bytes(), b"previous installation")
        self.check.assert_not_called()

    def test_unrunnable_download_is_not_installed(self):
        self.check.side_effect = RuntimeError("cannot execute")
        with self.assertRaisesRegex(RuntimeError, "cannot execute"):
            installer.ensure_ffmpeg(self.destination)
        self.assertFalse(self.destination.exists())

    def test_simultaneous_hub_and_llama_download_once(self):
        failures = []
        def install():
            try:
                installer.ensure_ffmpeg(self.destination)
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=install) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.download.call_count, 1)


if __name__ == "__main__":
    unittest.main()
