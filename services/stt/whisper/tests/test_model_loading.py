import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "whisper_model_loading", Path(__file__).resolve().parents[1] / "model_loading.py"
)
loading = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loading)


class ModelLoadingTests(unittest.TestCase):
    def test_cuda_failure_recovers_on_cpu(self):
        loaded = object()
        with patch.object(loading, "WhisperModel", side_effect=[
            RuntimeError("unsupported hybrid"), RuntimeError("unsupported fp16"),
            RuntimeError("CUDA unavailable"), loaded,
        ]) as factory:
            result = loading.load_whisper_robust("local-model")
        self.assertEqual(result, (loaded, "cpu", "int8"))
        self.assertEqual([(c.kwargs["device"], c.kwargs["compute_type"])
                          for c in factory.call_args_list],
                         [("cuda", "int8_float16"), ("cuda", "float16"),
                          ("cuda", "int8"), ("cpu", "int8")])
        self.assertGreater(factory.call_args.kwargs["cpu_threads"], 0)

    def test_cpu_selection_and_explicit_compute_are_respected(self):
        loaded = object()
        with patch.object(loading, "WhisperModel", return_value=loaded) as factory:
            result = loading.load_whisper_robust("local-model", "cpu", "int8")
        self.assertEqual(result, (loaded, "cpu", "int8"))
        factory.assert_called_once()

    def test_legacy_float32_setting_uses_supported_mode(self):
        for device, expected in (("cuda", "int8_float16"), ("cpu", "int8")):
            with patch.object(loading, "WhisperModel", return_value=object()) as factory:
                result = loading.load_whisper_robust("local-model", device, "float32")
            self.assertEqual(result[1:], (device, expected))
            self.assertEqual(factory.call_args.kwargs["compute_type"], expected)

    def test_failure_is_reported_with_cause(self):
        error = RuntimeError("invalid model")
        with patch.object(loading, "WhisperModel", side_effect=error) as factory:
            with self.assertRaises(RuntimeError) as raised:
                loading.load_whisper_robust("missing-model", "cpu", "int8")
        factory.assert_called_once()
        self.assertIs(raised.exception.__cause__, error)
